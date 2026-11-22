import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from postgen import backgrounds
from postgen.captions import CaptionError, CaptionWriter
from postgen.checks import check_calendar
from postgen.config import DATA_DIR, OUTPUT_DIR, load_yaml, location_from_env
from postgen.models import hm
from postgen.occasions import OccasionPicker
from postgen.panchang import PanchangSource


def cmd_plan(args):
    loc = location_from_env()
    source, picker = PanchangSource(loc), OccasionPicker()
    print(f"Occasion plan · {loc.name} · {args.start} (+{args.days - 1} days)\n")
    for day in source.range(args.start, args.days):
        occ = picker.pick(day)
        print(f"{day.date} {day.weekday[:3]}  P{occ.priority}  {occ.name_en:<22} [{occ.tag}]")
        print(f"    tithi {day.tithi} (till {hm(day.tithis[0].end)}) · {day.nakshatra} · {day.yoga} yoga · "
              f"sunrise {hm(day.sunrise)} · sunset {hm(day.sunset)} · Rahu kaal {day.rahu_kaal.fmt()}")
        if occ.also:
            print(f"    also today: {', '.join(occ.also)}")


def cmd_check_calendar(args):
    problems, checked = check_calendar(args.year, PanchangSource(location_from_env()), OccasionPicker())
    print(f"Checked {checked} calendar entries for {args.year} against computed tithis.")
    for p in problems:
        print("  ✗ " + p)
    if problems:
        sys.exit(1)
    print("  ✓ all consistent")


def cmd_captions(args):
    if args.slot == "evening":
        return cmd_evening(args)
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE")):
        sys.exit("No Anthropic credentials: set ANTHROPIC_API_KEY in .env (see .env.example).")
    loc = location_from_env()
    source, picker, writer = PanchangSource(loc), OccasionPicker(), CaptionWriter()
    out = DATA_DIR / str(args.week or args.start)
    path = out / "captions.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    regen = {str(args.start + timedelta(days=i)) for i in range(args.days)}
    writer.hooks_used = [c["llm"]["hook"] for c in existing if "llm" in c and c["date"] not in regen]
    results, failed = [], []
    for day in source.range(args.start, args.days):
        occ = picker.pick(day)
        rule = next((r for r in picker.rules if r["slug"] == occ.kind), None)
        print(f"… {day.date} {occ.name_en}", file=sys.stderr)
        try:
            cap = writer.write(day, occ, rule)
        except CaptionError as e:
            failed.append(str(day.date))
            results.append({"date": str(day.date), "occasion": occ.name_en, "error": str(e)})
            continue
        results.append({"date": str(day.date), "occasion": occ.name_en, "slug": occ.slug,
                        "priority": occ.priority, "tag": occ.tag, **asdict(cap)})
    out.mkdir(parents=True, exist_ok=True)
    merged = {c["date"]: c for c in existing}
    merged.update({r["date"]: r for r in results})          # regenerating some days keeps the others
    path.write_text(json.dumps(sorted(merged.values(), key=lambda c: c["date"]), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    tokens = [r["usage"] for r in results if "usage" in r]
    print(f"\nWrote {out / 'captions.json'} · {len(results) - len(failed)}/{len(results)} ok · "
          f"tokens in/out {sum(t['input_tokens'] for t in tokens)}/{sum(t['output_tokens'] for t in tokens)}",
          file=sys.stderr)
    if failed:
        print(f"Needs manual caption: {', '.join(failed)}", file=sys.stderr)


def cmd_bootstrap(args):
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        sys.exit("No Gemini credentials: set GEMINI_API_KEY in .env (billing must be enabled; "
                 "image models have no free tier).")
    lib = backgrounds.Library()
    tags = args.tags or backgrounds.TAGS
    per_tag = args.per_tag or lib.cfg["bootstrap_per_tag"]
    print(f"Bootstrapping {', '.join(tags)} to {per_tag} images each")
    made = lib.bootstrap(tags, per_tag)
    total = sum(made.values())
    paths = [p for t in tags for p in lib.images(t)]
    if paths:
        sheet = backgrounds.contact_sheet(paths, DATA_DIR / "backgrounds" / f"backgrounds_{'_'.join(tags) if len(tags) <= 3 else 'all'}.png")
        print(f"Contact sheet: {sheet}")
    print(f"Generated {total} new image(s).")
    if lib.quota_hit:
        print("Stopped on a quota/billing error; run again later to resume.")


def evening_background(occ, tomorrow_weekday: str) -> tuple[str, str, int]:
    """(tag, slug, priority) for the evening post's background: the festival's own
    look on the eve of a festival, else the next day's weekday deity."""
    if occ.kind != "aaj-ka-panchang":
        return occ.tag, occ.slug, occ.priority
    return load_yaml("occasions")["weekdays"][tomorrow_weekday]["tag"], "kal-ka-din", 3


def cmd_evening(args):
    """8 PM 'Kal ka din' posts: each previews the next day."""
    from postgen.evening import EveningWriter
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE")):
        sys.exit("No Anthropic credentials: set ANTHROPIC_API_KEY in .env (see .env.example).")
    source, picker = PanchangSource(location_from_env()), OccasionPicker()
    writer = EveningWriter(CaptionWriter())
    out = DATA_DIR / str(args.week or args.start)
    path = out / "evening.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    regen = {str(args.start + timedelta(days=i)) for i in range(args.days)}
    writer.base.hooks_used = [c["llm"]["hook"] for c in existing if "llm" in c and c["date"] not in regen]
    results = []
    for i in range(args.days):
        d = args.start + timedelta(days=i)
        today, tomorrow = source.day(d), source.day(d + timedelta(days=1))
        occ = picker.pick(tomorrow)
        rule = next((r for r in picker.rules if r["slug"] == occ.kind), None)
        tag, slug, prio = evening_background(occ, tomorrow.weekday)
        print(f"… {d} evening → kal {tomorrow.date} {occ.name_en}", file=sys.stderr)
        try:
            ev = writer.write(today, tomorrow, occ, rule)
        except CaptionError as e:
            results.append({"date": str(d), "occasion": occ.name_en, "error": str(e)})
            continue
        results.append({"date": str(d), "occasion": occ.name_en, "tag": tag, "slug": slug, "priority": prio, **asdict(ev)})
    out.mkdir(parents=True, exist_ok=True)
    merged = {c["date"]: c for c in existing}
    merged.update({r["date"]: r for r in results})
    path.write_text(json.dumps(sorted(merged.values(), key=lambda c: c["date"]), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    for r in results:
        print(f"{r['date']}  → {r['occasion']}: " + (r.get("title_hi") or "ERROR " + r.get("error", "")))


def cmd_recaption(args):
    """Re-layout saved captions (no LLM call) after layout or brand-text changes."""
    start = args.start or args.date
    path = DATA_DIR / str(start) / "captions.json"
    caps = json.loads(path.read_text(encoding="utf-8"))
    picker, writer = OccasionPicker(), CaptionWriter(client=object())   # client unused: no LLM call
    source = PanchangSource(location_from_env())
    for i, c in enumerate(caps):
        if args.date and c["date"] != str(args.date) or "llm" not in c:
            continue
        day = source.day(date.fromisoformat(c["date"]))
        occ = picker.pick(day)
        rule = next((r for r in picker.rules if r["slug"] == occ.kind), None)
        cap = writer.reassemble(day, occ, rule, c)
        caps[i] = {k: c[k] for k in ("date", "occasion", "slug", "priority", "tag")} | asdict(cap)
        print(f"re-laid out {c['date']} {c['occasion']}")
    path.write_text(json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_anthropic() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"))


def _image_qa():
    """Automatic review of new backgrounds (needs Anthropic credentials)."""
    if not _has_anthropic() or not load_yaml("settings")["images"].get("qa_enabled", True):
        return None
    from postgen.imageqa import ImageQA
    return ImageQA()


def day_folder(d: date, weekday: str) -> Path:
    """One folder per post day, directly in output/, e.g. output/2026-10-29_Thu/."""
    return OUTPUT_DIR / f"{d}_{weekday[:3]}"


SLOTS = {"post_am": "1_post_6AM", "story_am": "2_story_6AM", "post_pm": "3_post_8PM", "story_pm": "4_story_8PM"}


def _fresh(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.iterdir():
        if f.is_file():
            f.unlink()
    return folder


def _write_captions(folder: Path, caption: str, caption_fb: Optional[str]):
    (folder / "caption_instagram.txt").write_text(caption + "\n", encoding="utf-8")
    fb = caption_fb or caption.replace("(link in bio)", f"👉 {load_yaml('brand')['facebook_link']}")
    (folder / "caption_facebook.txt").write_text(fb + "\n", encoding="utf-8")


def _write_sticker(folder: Path, what: str):
    brand = load_yaml("brand")
    (folder / "link_sticker.txt").write_text(
        f"STORY: {what}\n\n"
        "INSTAGRAM STORY: post from the Instagram app (Business Suite can't add Instagram link stickers)\n"
        "  1. Instagram app → Your story → pick story.png\n"
        "  2. Sticker icon → Link → paste:\n"
        f"     {brand['story_link']}\n"
        f"  3. Sticker text: {brand['story_sticker_text']}\n"
        "  4. Place the sticker in the empty space just below the yellow box → Share\n\n"
        "FACEBOOK STORY: Business Suite → Create story → Facebook Page\n"
        "  1. Upload story.png\n"
        "  2. 'Swipe up link' → Web link → paste:\n"
        f"     {brand['facebook_story_link']}\n"
        "     (Facebook rejects Play Store links here; this website page leads to the app.)\n", encoding="utf-8")


def cmd_render(args):
    """Render all of one day's posts into output/<date>_<Dow>/<slot>/."""
    from postgen import render
    start = args.start or args.date
    week = DATA_DIR / str(start)
    caps = json.loads((week / "captions.json").read_text(encoding="utf-8"))
    cap = next((c for c in caps if c["date"] == str(args.date) and "llm" in c), None)
    eve_path = week / "evening.json"
    eve = next((c for c in (json.loads(eve_path.read_text(encoding="utf-8")) if eve_path.exists() else [])
                if c["date"] == str(args.date) and "llm" in c), None)
    source = PanchangSource(location_from_env())
    day = source.day(args.date)
    lib = backgrounds.Library(qa=_image_qa())
    out = day_folder(args.date, day.weekday)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():              # files from the old flat layout
        if f.is_file():
            f.unlink()
    jobs, notes = [], []
    if cap:
        choice = lib.choose(args.date, cap["tag"], cap["slug"], cap["priority"])
        notes.append(f"6 AM background: {choice.path.relative_to(backgrounds.BACKGROUNDS_DIR)} ({choice.status})")
        post = _fresh(out / SLOTS["post_am"])
        # Files are numbered in posting order: 1 panchang, 2..4 rashis, 5 app slide.
        if cap["kind"] == "panchang":
            slides = cap.get("rashifal_slides") or []
            jobs.append((render.panchang_html(day, choice.path, cap["image_tagline"], swipe=bool(slides)), post / "1_panchang.png"))
            n = len(slides) + 1 + (1 if slides else 0)
            for i, sl in enumerate(slides, start=2):
                jobs.append((render.rashi_html(day, choice.path, sl, f"{i}/{n}"), post / f"{i}_{sl['rashi'].lower()}.png"))
            if slides:
                jobs.append((render.cta_html(day, choice.path, f"{n}/{n}"), post / f"{n}_app.png"))
        else:
            jobs.append((render.festival_html(day, choice.path, cap), post / f"1_{cap['slug']}.png"))
        _write_captions(post, cap["caption"], cap.get("caption_facebook"))
        story = _fresh(out / SLOTS["story_am"])
        jobs.append((render.morning_story_html(day, choice.path, cap), story / "story.png", render.STORY))
        _write_sticker(story, "6 AM — today's key timings + app link")
    if eve:
        tomorrow = source.day(date.fromisoformat(eve["for_date"]))
        choice = lib.choose(args.date, eve["tag"], eve["slug"], eve["priority"], slot="evening")
        notes.append(f"8 PM background: {choice.path.relative_to(backgrounds.BACKGROUNDS_DIR)} ({choice.status})")
        post = _fresh(out / SLOTS["post_pm"])
        jobs.append((render.evening_html(tomorrow, choice.path, eve, story=False), post / "1_kal-ka-din.png"))
        _write_captions(post, eve["caption"], eve.get("caption_facebook"))
        story = _fresh(out / SLOTS["story_pm"])
        jobs.append((render.evening_html(tomorrow, choice.path, eve, story=True), story / "story.png", render.STORY))
        _write_sticker(story, "8 PM — tomorrow's preview + app link")
    for path in render.to_png(jobs):
        print(path.relative_to(OUTPUT_DIR))
    write_posting_plan(week, caps)
    for n in notes:
        print(n)


def write_posting_plan(week: Path, caps: list[dict]):
    """POSTING_PLAN.txt for the week (in output/_data/<week>/): every slot folder, in order, with its time."""
    lines = [f"UPAYSUTRA — POSTING PLAN ({caps[0]['date']} to {caps[-1]['date']})", "",
             "Each day folder has up to 4 sub-folders, in posting order:",
             "  1_post_6AM   feed post: upload images 1, 2, ... in order; caption_instagram.txt for Instagram,",
             "               caption_facebook.txt for Facebook ('Customize post for Facebook and Instagram')",
             "  2_story_6AM  story: story.png + link sticker (see link_sticker.txt)",
             "  3_post_8PM   feed post 'Kal ka din' (previews tomorrow): same steps as the morning post",
             "  4_story_8PM  story: story.png + link sticker", ""]
    for c in caps:
        folders = sorted(OUTPUT_DIR.glob(f"{c['date']}_*/"))
        if not folders:
            continue
        lines.append(f"output/{folders[0].name}/  {c['occasion']}")
        for sub in sorted(p for p in folders[0].iterdir() if p.is_dir()):
            files = sorted(f.name for f in sub.glob("*.png"))
            lines.append(f"  {sub.name:<13} {', '.join(files)}")
        lines.append("")
    (week / "POSTING_PLAN.txt").write_text("\n".join(lines), encoding="utf-8")


def cmd_reel(args):
    build_reel(args.date, args.start or args.date)


def build_reel(d: date, start: date) -> Path:
    """Build the day's reel: voiced script → one slide per line → MP4 (+ cover)."""
    import shutil
    import tempfile
    from postgen import reel, render
    week = DATA_DIR / str(start)
    caps = json.loads((week / "captions.json").read_text(encoding="utf-8"))
    cap = next((c for c in caps if c["date"] == str(d) and "llm" in c), None)
    if cap is None:
        raise RuntimeError(f"no 6 AM caption for {d}")
    day = PanchangSource(location_from_env()).day(d)
    lib = backgrounds.Library(qa=_image_qa())
    bg = lib.assigned(cap["tag"], d) or lib.choose(d, cap["tag"], cap["slug"], cap["priority"]).path
    writer = reel.ReelWriter(CaptionWriter())
    lines, usage = writer.script(day, cap)
    cfg = writer.cfg
    out = day_folder(d, day.weekday) / "1_reel_6AM"
    out.mkdir(parents=True, exist_ok=True)
    kicker = cap["image_headline"]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        jobs, audio, segs = [], [], []
        for i, ln in enumerate(lines):
            first, last = i == 0, i == len(lines) - 1
            k = "आज का राशिफल" if ln.rashi else "" if first or last else kicker
            html = render.reel_frame_html(day, bg, ln.show, kicker=k, hook=first, cta=last, rashi=ln.rashi)
            jobs.append((html, tmp / f"f{i}.png", render.STORY))
        render.to_png(jobs)
        total = 0.0
        for i, ln in enumerate(lines):
            a = reel.tts(ln.say, tmp / f"a{i}.mp3", cfg)
            secs = reel.media_seconds(a) + cfg["pause_seconds"]
            reel.segment(tmp / f"f{i}.png", a, secs, tmp / f"s{i}.mp4", cfg["fps"])
            segs.append(tmp / f"s{i}.mp4")
            total += secs
        reel.concat(segs, out / "reel.mp4")
        shutil.copy(tmp / "f0.png", out / "cover.png")
    (out / "script.json").write_text(json.dumps([ln.__dict__ for ln in lines], ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    post = out.parent / "1_post_6AM"                 # same captions as the 6 AM post (already rendered)
    for name, fallback in (("caption_instagram.txt", cap["caption"]),
                           ("caption_facebook.txt", cap.get("caption_facebook") or cap["caption"])):
        text = (post / name).read_text(encoding="utf-8") if (post / name).exists() else fallback + "\n"
        (out / name).write_text(text.replace(" (swipe karein 👉)", ""), encoding="utf-8")   # nothing to swipe on a reel
    chars = sum(len(ln.say) for ln in lines)
    print(f"{out / 'reel.mp4'}  ·  {total:.1f} s  ·  {len(lines)} lines  ·  {chars} voice characters")
    for ln in lines:
        print(f"  [{ln.source}] {ln.say}")
    return out / "reel.mp4"


def cmd_build_week(args):
    """Build a whole week unattended: captions (6 AM + 8 PM), images, manifest, summary email."""
    from argparse import Namespace
    from postgen import automation, notify
    start = args.start or _next_monday()
    days = [start + timedelta(days=i) for i in range(args.days)]
    probs = automation.calendar_problems(days)
    blockers = [p for p in probs if p.startswith("BLOCKER")]
    if blockers:
        notify.alert(f"Week of {start} NOT built", "\n".join(probs))
        sys.exit(1)
    week = DATA_DIR / str(start)
    failures, skipped = [], []
    recaptioned = {"morning": set(), "evening": set()}
    for slot, name in (("morning", "captions.json"), ("evening", "evening.json")):
        for d in days:
            if not args.force and _has_caption(week / name, d):
                continue                             # already written: no LLM call
            recaptioned[slot].add(d)
            try:
                cmd_captions(Namespace(start=d, days=1, week=start, slot=slot))
            except SystemExit:
                raise
            except Exception as e:                   # noqa: BLE001
                failures.append(f"{d} {slot} caption: {e}")
    for path, label in ((week / "captions.json", "6 AM caption"), (week / "evening.json", "8 PM caption")):
        for c in (json.loads(path.read_text(encoding="utf-8")) if path.exists() else []):
            if "error" in c and date.fromisoformat(c["date"]) in days:
                failures.append(f"{c['date']} {label} failed after retries (slot will be skipped): {c['error'][:200]}")
    for d in days:
        new_caption = d in recaptioned["morning"] or d in recaptioned["evening"]
        if not args.force and not new_caption and _images_built(d):
            skipped.append(f"{d:%a} images")
            continue
        try:
            cmd_render(Namespace(start=start, date=d))
        except Exception as e:                       # noqa: BLE001
            failures.append(f"{d} render: {e}")
    if automation.cfg().get("am_post") == "reel":
        for d in days:
            if not args.force and d not in recaptioned["morning"] and _reel_built(d):
                skipped.append(f"{d:%a} reel")
                continue
            try:
                build_reel(d, start)
            except Exception as e:                   # noqa: BLE001 - the 6 AM carousel goes out instead
                failures.append(f"{d} reel: {e} (the 6 AM carousel will be posted instead)")
    items = automation.update_manifest(days)
    summary = [f"Built {len(items)} posts/stories for {days[0]} to {days[-1]}.", ""]
    for d in days:
        summary.append(f"{d:%a %d %b}: " + ", ".join(i["slot"] for i in items if i["date"] == str(d)))
    if skipped:
        summary += ["", "Already built, not regenerated: " + ", ".join(skipped)]
    if probs or failures:
        summary += ["", "Needs attention:", *probs, *failures]
    notify.send(f"Week of {start:%d %b} built" + (" — needs attention" if probs or failures else ""), "\n".join(summary))
    print("\n".join(summary))


def _has_caption(path: Path, d: date) -> bool:
    """True when the week file already holds a good (non-error) caption for d."""
    caps = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return any(c["date"] == str(d) and "llm" in c for c in caps)


def _images_built(d: date) -> bool:
    """All four slot folders for d already have their image."""
    out = day_folder(d, d.strftime("%A"))
    return all(any((out / folder).glob("*.png")) for folder in SLOTS.values())


def _reel_built(d: date) -> bool:
    reel = day_folder(d, d.strftime("%A")) / "1_reel_6AM"
    return (reel / "reel.mp4").exists() and (reel / "cover.png").exists()


def _next_monday() -> date:
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def cmd_publish(args):
    from postgen import automation
    client = None
    if not args.dry_run:
        from postgen.meta import MetaClient
        client = MetaClient()
    report = automation.publish_due(client=client, dry_run=args.dry_run, only=args.item)
    for k, v in report.items():
        for line in v:
            print(f"{k}: {line}")


def cmd_preview(args):
    from postgen import automation
    if automation.cfg()["mode"] != "veto" and not args.date:
        print("automation.mode is not 'veto': no nightly preview")
        return
    automation.preview_email(args.date or date.today() + timedelta(days=1))


def cmd_pause(args):
    from postgen import automation
    automation.set_pause(args.target, pause=not args.resume)
    print(("resumed " if args.resume else "paused ") + args.target)


def cmd_sync(args):
    from postgen import storage
    if not storage.enabled():
        sys.exit("R2 storage not configured (R2_* env vars)")
    fn = storage.sync_down if args.direction == "down" else storage.sync_up
    fn(light=args.light)


def main():
    p = argparse.ArgumentParser(prog="postgen")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="print the occasion picked for each day")
    sp.add_argument("--start", type=date.fromisoformat, required=True)
    sp.add_argument("--days", type=int, default=7)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("check-calendar", help="verify config/calendar/<year>.yaml against computed tithis")
    sp.add_argument("--year", type=int, default=date.today().year)
    sp.set_defaults(func=cmd_check_calendar)

    sp = sub.add_parser("captions", help="generate captions for each day (milestone 2)")
    sp.add_argument("--start", type=date.fromisoformat, required=True)
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--week", type=date.fromisoformat, help="week folder to merge into (default: --start)")
    sp.add_argument("--slot", choices=["morning", "evening"], default="morning",
                    help="morning = 6 AM post; evening = 8 PM 'Kal ka din' post previewing the next day")
    sp.set_defaults(func=cmd_captions)

    sp = sub.add_parser("bootstrap", help="fill the background library (3 images per tag)")
    sp.add_argument("--tags", nargs="+", choices=backgrounds.TAGS)
    sp.add_argument("--per-tag", type=int)
    sp.set_defaults(func=cmd_bootstrap)

    sp = sub.add_parser("recaption", help="re-layout saved captions without calling the LLM")
    sp.add_argument("--date", type=date.fromisoformat)
    sp.add_argument("--start", type=date.fromisoformat, help="week folder (default: --date)")
    sp.set_defaults(func=cmd_recaption)

    sp = sub.add_parser("render", help="render one day's post images from captions.json")
    sp.add_argument("--date", type=date.fromisoformat, required=True)
    sp.add_argument("--start", type=date.fromisoformat, help="week folder (default: --date)")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("reel", help="build the day's reel (voiceover + slides → MP4)")
    sp.add_argument("--date", type=date.fromisoformat, required=True)
    sp.add_argument("--start", type=date.fromisoformat, help="week folder (default: --date)")
    sp.set_defaults(func=cmd_reel)

    sp = sub.add_parser("build-week", help="unattended: build next week's posts, manifest, summary email")
    sp.add_argument("--start", type=date.fromisoformat, help="Monday to start from (default: next Monday)")
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--force", action="store_true", help="regenerate days that are already built")
    sp.set_defaults(func=cmd_build_week)

    sp = sub.add_parser("publish", help="publish every post whose time has come")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--item", help="publish only this post id, even if more than 2 h late")
    sp.set_defaults(func=cmd_publish)

    sp = sub.add_parser("preview", help="email the next day's posts (veto mode)")
    sp.add_argument("--date", type=date.fromisoformat)
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("pause", help="pause 'all', a date, or a post id (e.g. 2026-11-02_post_am)")
    sp.add_argument("target")
    sp.add_argument("--resume", action="store_true")
    sp.set_defaults(func=cmd_pause)

    sp = sub.add_parser("sync", help="copy working state from/to R2 (GitHub Actions)")
    sp.add_argument("direction", choices=["down", "up"])
    sp.add_argument("--light", action="store_true", help="only what publishing needs (fast)")
    sp.set_defaults(func=cmd_sync)

    args = p.parse_args()
    args.func(args)
