"""Background library: generate-and-cache with Gemini, 4-week reuse rotation,
and a fallback chain that never blocks a week.

Library layout: backgrounds/<tag>/<name>.png + <name>.json sidecar
(prompt, model, created, occasion). Rejected images live in backgrounds/_rejected/.
"""
import io
import json
import random
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Protocol

from PIL import Image

from postgen.config import BACKGROUNDS_DIR, STATE_DIR, load_yaml

WIDTH, HEIGHT = 1080, 1350
TAGS = ["vishnu", "shiv", "lakshmi", "ganesh", "hanuman", "durga", "surya", "moon_full", "moon_new", "diya", "cosmic"]
REJECTED_DIR = BACKGROUNDS_DIR / "_rejected"
USED_FILE = STATE_DIR / "used_backgrounds.json"
LOG_FILE = STATE_DIR / "generation_log.jsonl"


# ---------------- providers ----------------

class _Verdict:
    def __init__(self, ok: bool, issues: list[str]):
        self.ok, self.issues = ok, issues


class ProviderError(Exception):
    def __init__(self, msg: str, quota: bool = False):
        super().__init__(msg)
        self.quota = quota   # quota/billing: stop calling for this run


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, aspect: str) -> bytes:
        """Return encoded image bytes (any size/format Pillow can read)."""


class GeminiProvider:
    def __init__(self, model: str, image_size: str, client=None):
        from google import genai
        self.name = model
        self.model = model
        self.image_size = image_size
        self.client = client or genai.Client()
        self._aspect_fallback: dict[str, str] = {}

    def generate(self, prompt: str, aspect: str) -> bytes:
        from google.genai import errors, types
        aspect = self._aspect_fallback.get(aspect, aspect)
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect, image_size=self.image_size),
        )
        try:
            resp = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
        except errors.ClientError as e:
            if e.code == 400 and "aspect" in str(e).lower() and aspect == "4:5":
                self._aspect_fallback["4:5"] = "3:4"   # closest supported; cropped to 4:5 later
                return self.generate(prompt, "4:5")
            raise ProviderError(f"{self.model}: {_short_error(e)}", quota=e.code in (402, 403, 429)) from e
        except errors.ServerError as e:
            raise ProviderError(f"{self.model}: server {e.code}") from e
        for cand in resp.candidates or []:
            for part in (cand.content.parts if cand.content else None) or []:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        reasons = [str(c.finish_reason) for c in resp.candidates or []]
        raise ProviderError(f"{self.model}: no image returned (finish: {reasons or 'none'})")


def _short_error(e) -> str:
    """One readable line instead of the full JSON error body."""
    msg = (getattr(e, "message", None) or str(e)).split("\n")[0].strip()
    if "free_tier" in str(e) and "limit: 0" in str(e):
        return (f"{e.code}: this Google project is on the free tier, which has no image quota. "
                "Enable billing for the project in Google AI Studio, then retry.")
    if e.code == 402:
        return "402: the project's prepaid Gemini credits are used up. Add credits in AI Studio (ai.studio/projects), then retry."
    return f"{e.code} {getattr(e, 'status', '')}: {msg}"[:300]


def with_retries(provider: ImageProvider, prompt: str, aspect: str, tries: int, sleep=time.sleep) -> bytes:
    delay = 2.0
    for attempt in range(1, tries + 1):
        try:
            return provider.generate(prompt, aspect)
        except ProviderError as e:
            if attempt == tries or e.quota and attempt >= 2:
                raise
            sleep(delay + random.uniform(0, 1))
            delay *= 2
    raise AssertionError("unreachable")


# ---------------- prompts & images ----------------

def subject_for(tag: str, occasion_slug: Optional[str] = None) -> str:
    subjects = load_yaml("subjects")
    return (subjects.get("occasions") or {}).get(occasion_slug) or subjects["subjects"][tag]


def build_prompt(tag: str, occasion_slug: Optional[str] = None, variation: int = 0) -> str:
    style = load_yaml("style")
    subject = subject_for(tag, occasion_slug)
    look = (style.get("looks") or {}).get(occasion_slug or "")
    parts = [look.strip() if look else style["master_style"].strip(), f"Subject: {subject}."]
    if style.get("variations"):
        parts.append(style["variations"][variation % len(style["variations"])])
    parts += [style["composition"].strip(), "Avoid: " + style["negative"].strip()]
    return " ".join(parts)


def fingerprint(img: Image.Image) -> int:
    """64-bit perceptual hash: near-identical pictures give small Hamming distances."""
    small = img.convert("L").resize((8, 8), Image.LANCZOS)
    px = list(small.getdata())
    mean = sum(px) / 64
    return sum(1 << i for i, v in enumerate(px) if v > mean)


NEAR_DUPLICATE_BITS = 14   # out of 64; below this two images look like the same picture


def near_duplicate(img: Image.Image, others: list[Path]) -> Optional[Path]:
    fp = fingerprint(img)
    for o in others:
        if bin(fp ^ fingerprint(Image.open(o))).count("1") <= NEAR_DUPLICATE_BITS:
            return o
    return None


def to_post_size(data: bytes) -> Image.Image:
    """Centre-crop to 4:5 and resize to 1080x1350 (Lanczos, up or down)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    target = WIDTH / HEIGHT
    if w / h > target:            # too wide → trim sides
        nw = round(h * target)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    elif w / h < target:          # too tall → trim top/bottom, keep more of the top (subject sits upper-middle)
        nh = round(w / target)
        top = (h - nh) // 3
        img = img.crop((0, top, w, top + nh))
    return img.resize((WIDTH, HEIGHT), Image.LANCZOS)


def placeholder_background() -> Image.Image:
    """Last resort when the library is empty and generation is down: a plain
    indigo→saffron gradient, so rendering never blocks."""
    top, bottom = (30, 27, 75), (120, 60, 20)
    img = Image.new("RGB", (WIDTH, HEIGHT))
    px = img.load()
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        for x in range(WIDTH):
            px[x, y] = c
    return img


# ---------------- library ----------------

@dataclass
class Choice:
    path: Path
    status: str          # "generated" | "reused" | "fallback"
    note: str = ""


class Library:
    def __init__(self, providers: Optional[list[ImageProvider]] = None, root: Path = BACKGROUNDS_DIR,
                 used_file: Path = USED_FILE, log_file: Path = LOG_FILE, sleep=time.sleep, qa=None):
        self.cfg = load_yaml("settings")["images"]
        self.root, self.used_file, self.log_file, self.sleep = root, used_file, log_file, sleep
        self._providers = providers
        self.qa = qa          # optional ImageQA: automatic review before an image enters the library
        self.quota_hit = False

    # providers are created lazily so reuse-only runs don't need a key
    @property
    def providers(self) -> list[ImageProvider]:
        if self._providers is None:
            self._providers = []
            for model in [self.cfg["primary_model"], self.cfg.get("secondary_model")]:
                if model:
                    sizes = self.cfg["price_usd"].get(model, {})
                    size = self.cfg["image_size"] if self.cfg["image_size"] in sizes else next(iter(sizes), "1K")
                    self._providers.append(GeminiProvider(model, size))
        return self._providers

    # ----- state -----
    def _used(self) -> dict:
        return json.loads(self.used_file.read_text()) if self.used_file.exists() else {}

    @staticmethod
    def _slot_key(on: date, slot: Optional[str]) -> str:
        """Usage is recorded per post: '2026-10-27' (morning) or '2026-10-27@evening'."""
        return on.isoformat() + (f"@{slot}" if slot else "")

    def mark_used(self, path: Path, on: date, slot: Optional[str] = None):
        used = self._used()
        dates = set(used.get(self._key(path), []))
        dates.add(self._slot_key(on, slot))
        used[self._key(path)] = sorted(dates)
        self.used_file.parent.mkdir(parents=True, exist_ok=True)
        self.used_file.write_text(json.dumps(used, indent=2))

    def _key(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def _log(self, **entry):
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **entry}) + "\n")

    def _rejected(self, tag: str) -> list[Path]:
        return list((self.root / "_rejected").glob(f"{tag}__*.png"))

    def images(self, tag: str) -> list[Path]:
        return sorted((self.root / tag).glob("*.png"))

    def unused(self, tag: str, on: date) -> list[Path]:
        """Images not used within the reuse window around `on` (either side, so
        regenerating an earlier week can't collide with a later one)."""
        window = timedelta(days=self.cfg["reuse_window_days"])
        used = self._used()
        out = []
        for p in self.images(tag):
            dates = [date.fromisoformat(d[:10]) for d in used.get(self._key(p), [])]
            if all(abs(d - on) >= window or d == on for d in dates):
                out.append(p)
        return out

    def _last_used(self, p: Path) -> str:
        return max(self._used().get(self._key(p), []), default="")

    # ----- generation -----
    def generate(self, tag: str, occasion_slug: Optional[str] = None) -> Path:
        """Generate one image into backgrounds/<tag>/, retrying with the next
        variation when automatic review rejects it."""
        tries = 1 + self.cfg.get("qa_regenerations", 2)
        last = None
        for _ in range(tries):
            path = self._generate_once(tag, occasion_slug)
            subject = json.loads(path.with_suffix(".json").read_text())["subject"]
            dup = near_duplicate(Image.open(path), [p for p in self.images(tag) if p != path])
            if dup is not None:
                verdict = _Verdict(False, [f"too similar to {dup.name}; make it look different"])
            elif self.qa:
                verdict = self.qa.check(Image.open(path), subject)
            else:
                return path
            if verdict.ok:
                return path
            last = verdict.issues
            dest = self.reject(path)
            meta = json.loads(dest.with_suffix(".json").read_text())
            dest.with_suffix(".json").write_text(json.dumps({**meta, "rejected_by": "auto-review",
                                                             "issues": verdict.issues}, ensure_ascii=False, indent=2))
            self._log(tag=tag, occasion=occasion_slug, ok=False, error="auto-review rejected: " + "; ".join(verdict.issues))
        raise ProviderError(f"{tag}: auto-review rejected {tries} images in a row (last: {last})")

    def _generate_once(self, tag: str, occasion_slug: Optional[str] = None) -> Path:
        if self.quota_hit:
            raise ProviderError("skipped: quota/billing error earlier in this run", quota=True)
        prompt = build_prompt(tag, occasion_slug, variation=len(self.images(tag)) + len(self._rejected(tag)))
        errors = []
        for provider in self.providers:
            try:
                data = with_retries(provider, prompt, self.cfg["aspect_ratio"], self.cfg["retries"], self.sleep)
            except ProviderError as e:
                errors.append(str(e))
                self._log(model=provider.name, tag=tag, occasion=occasion_slug, ok=False, error=str(e))
                if e.quota:
                    self.quota_hit = True
                continue
            img = to_post_size(data)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            name = f"{occasion_slug or tag}_{stamp}"
            out = self.root / tag / f"{name}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out, optimize=True)
            size = getattr(provider, "image_size", "")
            meta = {"prompt": prompt, "subject": subject_for(tag, occasion_slug),
                    "model": provider.name, "image_size": size, "tag": tag,
                    "occasion": occasion_slug, "created": datetime.now().isoformat(timespec="seconds")}
            out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            cost = self.cfg["price_usd"].get(provider.name, {}).get(size)
            self._log(model=provider.name, tag=tag, occasion=occasion_slug, ok=True, file=self._key(out), cost_usd=cost)
            return out
        raise ProviderError("; ".join(errors), quota=self.quota_hit)

    def top_up(self, tag: str, on: date) -> int:
        """If fewer than min_unused images are free for `on`, generate a batch."""
        if len(self.unused(tag, on)) >= self.cfg["min_unused_per_tag"]:
            return 0
        made = 0
        for _ in range(self.cfg["generate_batch"]):
            try:
                self.generate(tag)
                made += 1
            except ProviderError:
                break
        return made

    # ----- selection -----
    def assigned(self, tag: str, on: date, slot: Optional[str] = None) -> Optional[Path]:
        """The image already used for this post in this tag, if any (re-renders keep it)."""
        used, key = self._used(), self._slot_key(on, slot)
        for p in self.images(tag):
            if key in used.get(self._key(p), []):
                return p
        return None

    def choose(self, on: date, tag: str, occasion_slug: str, priority: int, slot: Optional[str] = None) -> Choice:
        """Pick (and mark used) a background for a post on `on` (`slot` tells the
        morning and evening posts apart). Re-running for the same post returns the
        same image and never re-generates."""
        if (prev := self.assigned(tag, on, slot)) is not None:
            side = prev.with_suffix(".json")
            fresh = side.exists() and json.loads(side.read_text()).get("occasion") == occasion_slug
            return Choice(prev, "generated" if fresh else "reused")
        if priority == 1:
            try:
                return self._take(self.generate(tag, occasion_slug), on, "generated", slot=slot)
            except ProviderError as e:
                note = f"generation failed: {e}"
        else:
            note = ""
            self.top_up(tag, on)
            free = self.unused(tag, on)
            if free:
                pick = min(free, key=lambda p: (self._last_used(p), p.name))
                return self._take(pick, on, "reused", slot=slot)
        # fallback: ignore the reuse window, then fall back to cosmic, then a plain gradient
        for t in [tag, "cosmic"]:
            imgs = self.images(t)
            if imgs:
                pick = min(imgs, key=lambda p: (self._last_used(p), p.name))
                return self._take(pick, on, "fallback", note or f"no unused '{tag}' image", slot=slot)
        out = self.root / "_placeholder.png"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            placeholder_background().save(out)
        return Choice(out, "fallback", (note + "; " if note else "") + "library empty: plain gradient")

    def _take(self, path: Path, on: date, status: str, note: str = "", slot: Optional[str] = None) -> Choice:
        self.mark_used(path, on, slot)
        return Choice(path, status, note)

    def release(self, path: Path, on: date):
        """Forget that `path` was used on `on` (e.g. when a day gets a new background)."""
        used = self._used()
        dates = [d for d in used.get(self._key(path), []) if d != on.isoformat()]
        used[self._key(path)] = dates
        self.used_file.write_text(json.dumps(used, indent=2))

    def reject(self, path: Path) -> Path:
        """Move an image (and sidecar) to _rejected/ so it is never reused."""
        dest_dir = self.root / "_rejected"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{path.parent.name}__{path.name}"
        shutil.move(str(path), dest)
        side = path.with_suffix(".json")
        if side.exists():
            shutil.move(str(side), dest.with_suffix(".json"))
        used = self._used()
        used.pop(self._key(path), None)
        self.used_file.write_text(json.dumps(used, indent=2))
        return dest

    # ----- bootstrap -----
    def bootstrap(self, tags: list[str], per_tag: int, report=print) -> dict[str, int]:
        """Fill each tag up to `per_tag` images. Resumable: counts what exists,
        and stops cleanly on quota/billing errors."""
        made = {}
        for tag in tags:
            have = len(self.images(tag))
            made[tag] = 0
            while have + made[tag] < per_tag:
                try:
                    path = self.generate(tag)
                except ProviderError as e:
                    report(f"  {tag}: stopped ({e})")
                    if self.quota_hit:
                        return made
                    break
                made[tag] += 1
                report(f"  {tag}: {path.name}")
        return made


def contact_sheet(paths: list[Path], out: Path, cols: int = 3, thumb_w: int = 360) -> Path:
    """Grid of thumbnails with the lower-40% text zone outlined, for reviewing a batch."""
    from PIL import ImageDraw
    thumb_h = round(thumb_w * HEIGHT / WIDTH)
    rows = max(1, -(-len(paths) // cols))
    sheet = Image.new("RGB", (cols * thumb_w + (cols + 1) * 12, rows * (thumb_h + 30) + 12), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        x, y = 12 + (i % cols) * (thumb_w + 12), 12 + (i // cols) * (thumb_h + 30)
        sheet.paste(Image.open(p).convert("RGB").resize((thumb_w, thumb_h), Image.LANCZOS), (x, y))
        draw.rectangle([x, y + round(thumb_h * 0.6), x + thumb_w - 1, y + thumb_h - 1], outline=(255, 200, 0))
        draw.text((x, y + thumb_h + 6), f"{p.parent.name}/{p.name}"[:58], fill=(220, 220, 220))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
