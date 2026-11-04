"""End-to-end automation: weekly build → manifest → timed publishing.

state/manifest.json   every built post: id, slot, publish time, files, captions
state/published.json  ledger of what went out where (per platform): never posts twice
state/pauses.json     posts (or everything) the owner has paused

Safety: anything missing or failing is skipped and emailed, never guessed. A post
is never published more than `late_limit_minutes` after its time ("aaj"/"kal"
content goes stale).
"""
import io
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from PIL import Image

from postgen import notify, storage
from postgen.calendar import CalendarGap, festivals_on, timings_on
from postgen.config import OUTPUT_DIR, STATE_DIR, load_yaml
from postgen.occasions import OccasionPicker

MANIFEST = STATE_DIR / "manifest.json"
LEDGER = STATE_DIR / "published.json"
PAUSES = STATE_DIR / "pauses.json"
REEL_DIR = "1_reel_6AM"
SLOT_DIRS = {"post_am": "1_post_6AM", "story_am": "2_story_6AM", "post_pm": "3_post_8PM", "story_pm": "4_story_8PM"}
SLOT_NAMES = {"post_am": "6 AM post", "story_am": "6 AM story", "post_pm": "8 PM post", "story_pm": "8 PM story"}


def cfg() -> dict:
    return load_yaml("settings")["automation"]


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- manifest ----------------

def items_for_day(day_dir: Path) -> list[dict]:
    """Manifest items from a rendered day folder (the folders are the source of truth)."""
    c = cfg()
    tz = ZoneInfo(c["timezone"])
    d = date.fromisoformat(day_dir.name[:10])
    items = []
    for slot, sub in SLOT_DIRS.items():
        folder = day_dir / sub
        pngs = sorted(folder.glob("*.png")) if folder.exists() else []
        if not pngs:
            continue
        hh, mm = map(int, c["times"][slot].split(":"))
        story = slot.startswith("story")
        kind = "story" if story else ("carousel" if len(pngs) > 1 else "image")
        reel = day_dir / REEL_DIR / "reel.mp4"
        if slot == "post_am" and c.get("am_post") == "reel" and reel.exists():
            folder, kind, pngs = reel.parent, "reel", [reel, reel.parent / "cover.png"]
        cap = lambda name: (folder / name).read_text(encoding="utf-8").strip() if (folder / name).exists() else ""
        items.append({
            "id": f"{d}_{slot}", "date": str(d), "slot": slot,
            "publish_at": datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).isoformat(),
            "kind": kind,                                   # reel files: [video, cover]
            "files": [str(p.relative_to(OUTPUT_DIR)) for p in pngs],
            "caption_instagram": "" if story else cap("caption_instagram.txt"),
            "caption_facebook": "" if story else cap("caption_facebook.txt"),
        })
    return items


def update_manifest(days: list[date]) -> list[dict]:
    manifest = {i["id"]: i for i in _load(MANIFEST, [])}
    added = []
    for d in days:
        for folder in OUTPUT_DIR.glob(f"{d}_*/"):
            for item in items_for_day(folder):
                manifest[item["id"]] = item
                added.append(item)
    _save(MANIFEST, sorted(manifest.values(), key=lambda i: i["publish_at"]))
    return added


# ---------------- pauses ----------------

def is_paused(item: dict) -> bool:
    p = _load(PAUSES, {"all": False, "items": []})
    return p.get("all") or item["id"] in p["items"] or item["date"] in p["items"]


def set_pause(target: str, pause: bool = True):
    """target: 'all', a date '2026-11-02', or an item id '2026-11-02_post_am'."""
    p = _load(PAUSES, {"all": False, "items": []})
    if target == "all":
        p["all"] = pause
    elif pause and target not in p["items"]:
        p["items"].append(target)
    elif not pause and target in p["items"]:
        p["items"].remove(target)
    _save(PAUSES, p)


# ---------------- publishing ----------------

def publish_due(now: Optional[datetime] = None, client=None, dry_run: bool = False) -> dict:
    """Publish every manifest item whose time has come (and isn't paused, stale or done)."""
    c = cfg()
    now = now or datetime.now(ZoneInfo(c["timezone"]))
    # Dry runs keep their own ledger so a trial week behaves like the real thing.
    ledger_path = LEDGER.with_name("published_dryrun.json") if dry_run else LEDGER
    ledger = _load(ledger_path, {})
    report = {"published": [], "skipped": [], "failed": []}
    # Ids of posts this system published: they must never count as "already posted".
    ours = {v["id"] for slot in ledger.values() for v in slot.values() if isinstance(v, dict) and "id" in v}
    for item in _load(MANIFEST, []):
        at = datetime.fromisoformat(item["publish_at"])
        if at > now:
            continue
        done = ledger.setdefault(item["id"], {})
        todo = [p for p in c["platforms"] if p not in done]
        if not todo:
            continue
        if is_paused(item):
            if "paused" not in done:
                done["paused"] = now.isoformat()
                report["skipped"].append(f"{item['id']}: paused")
            continue
        if now - at > timedelta(minutes=c["late_limit_minutes"]):
            if "stale" not in done:
                done["stale"] = now.isoformat()
                report["skipped"].append(f"{item['id']}: more than {c['late_limit_minutes']} min late, not posted")
            continue
        try:
            urls = _media_urls(item, dry_run)
        except Exception as e:                              # noqa: BLE001 - report, never crash the run
            report["failed"].append(f"{item['id']}: image upload failed: {e}")
            continue
        for platform in todo:
            try:
                if not dry_run:
                    existing = client.recent_posts(platform, at, cfg_window(), exclude=ours)
                    if existing:
                        done[platform] = {"skipped": existing, "at": now.isoformat()}
                        report["skipped"].append(f"{item['id']} → {platform}: something is already posted "
                                                 f"around {at:%H:%M} ({', '.join(existing)}), not posting again")
                        continue
                ref = "dry-run" if dry_run else _publish_one(client, platform, item, urls)
                done[platform] = {"id": ref, "at": now.isoformat()}
                ours.add(ref)
                report["published"].append(f"{item['id']} → {platform}")
            except Exception as e:                          # noqa: BLE001
                report["failed"].append(f"{item['id']} → {platform}: {e}")
    _save(ledger_path, ledger)
    if report["failed"] or report["skipped"]:
        notify.alert("Some posts did not go out",
                     "\n".join(["Failed:", *report["failed"], "", "Skipped:", *report["skipped"]]))
    return report


def cfg_window() -> int:
    return int(cfg().get("duplicate_window_minutes", 60))


def _media_urls(item: dict, dry_run: bool) -> list[str]:
    if dry_run or not storage.enabled():
        return [f"(dry-run)/{f}" for f in item["files"]]
    return [storage.upload_video(OUTPUT_DIR / f, "media/" + f) if f.endswith(".mp4") else
            storage.upload_media(OUTPUT_DIR / f, "media/" + str(Path(f).with_suffix(".jpg"))) for f in item["files"]]


def _publish_one(client, platform: str, item: dict, urls: list[str]) -> str:
    kind = item["kind"]
    if kind == "reel":
        if platform == "instagram":
            return client.ig_reel(urls[0], item["caption_instagram"], urls[1])
        if platform == "facebook":
            return client.fb_reel(urls[0], item["caption_facebook"])
    if platform == "instagram":
        if kind == "story":
            return client.ig_story(urls[0])
        if kind == "carousel":
            return client.ig_carousel(urls, item["caption_instagram"])
        return client.ig_image(urls[0], item["caption_instagram"])
    if platform == "facebook":
        if kind == "story":
            return client.fb_story(urls[0])
        if kind == "carousel":
            return client.fb_carousel(urls, item["caption_facebook"])
        return client.fb_image(urls[0], item["caption_facebook"])
    raise ValueError(platform)


# ---------------- checks & emails ----------------

def calendar_problems(days: list[date]) -> list[str]:
    """Problems that must block or warn before a week is built."""
    probs = []
    last_needed = max(days) + timedelta(days=1)            # evening posts preview the next day
    try:
        festivals_on(last_needed)
    except CalendarGap as e:
        probs.append(f"BLOCKER: {e}")
    horizon = date.today() + timedelta(days=cfg()["calendar_warn_days"])
    try:
        festivals_on(horizon)
    except CalendarGap:
        probs.append(f"Calendar runs out before {horizon}: add the next months to config/calendar/ from drikpanchang.com.")
    picker = OccasionPicker()
    for d in days:
        try:
            names = festivals_on(d)
        except CalendarGap:
            continue
        rules = [picker.rule_for(n) for n in names]
        if any(r and r["priority"] == 1 for r in rules) and not timings_on(d):
            probs.append(f"{d}: major festival ({', '.join(names)}) has no curated Drik timings: "
                         "the post will go out without muhurat/moonrise times.")
    return probs


def _thumb(png: Path, width: int = 360) -> bytes:
    img = Image.open(png).convert("RGB")
    img.thumbnail((width, width * 2))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _reel_script(item: dict) -> str:
    path = OUTPUT_DIR / Path(item["files"][0]).parent / "script.json"
    return "\n".join(ln["say"] for ln in _load(path, []))


def preview_email(d: date):
    """Veto-mode email: tomorrow's posts with thumbnails, captions and how to pause."""
    items = [i for i in _load(MANIFEST, []) if i["date"] == str(d)]
    if not items:
        notify.alert(f"No posts built for {d}", "The weekly build has nothing for this day. Check the build run.")
        return
    repo = os.getenv("GITHUB_REPOSITORY", "<your-repo>")
    pause_url = f"https://github.com/{repo}/actions/workflows/pause.yml"
    html, text, imgs = [f"<h2>Posts for {d:%A %d %b}</h2>",
                        f"<p>These go out automatically. To stop one, open <a href='{pause_url}'>Pause a post</a> "
                        f"→ Run workflow → enter the id below (or the date to stop the whole day).</p>"], [], []
    for i in items:
        t = datetime.fromisoformat(i["publish_at"]).strftime("%I:%M %p").lstrip("0")
        html.append(f"<h3>{t} · {SLOT_NAMES[i['slot']]} · id <code>{i['id']}</code></h3>")
        for n, f in enumerate(i["files"]):
            if f.endswith(".mp4"):
                continue                                   # the reel's cover.png stands in for it
            cid = f"{i['id']}_{n}"
            imgs.append((cid, _thumb(OUTPUT_DIR / f)))
            html.append(f"<img src='cid:{cid}' width='180' style='margin:2px'>")
        if i["caption_instagram"]:
            html.append("<pre style='white-space:pre-wrap;font-family:sans-serif'>" + i["caption_instagram"] + "</pre>")
        what = "reel" if i["kind"] == "reel" else f"{len(i['files'])} image(s)"
        if i["kind"] == "reel":
            html.append("<p><b>Reel</b> (cover shown). Voiceover script:</p><pre style='white-space:pre-wrap;"
                        "font-family:sans-serif'>" + _reel_script(i) + "</pre>")
        text.append(f"{t} {SLOT_NAMES[i['slot']]} ({i['id']}): {what}")
    notify.send(f"Tomorrow's posts ({d:%a %d %b}) — reply not needed",
                "\n".join(text) + f"\n\nPause: {pause_url}", "\n".join(html), imgs)
