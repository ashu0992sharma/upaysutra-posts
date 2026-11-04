"""HTML/CSS templates → PNG via headless Chromium (correct Devanagari shaping).
All text on images comes from code or validated captions; images carry none."""
import tempfile
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from postgen import names
from postgen.backgrounds import HEIGHT, WIDTH
from postgen.config import ROOT, load_yaml
from postgen.models import DayFacts

TEMPLATES = ROOT / "templates"
FONTS = ROOT / "assets" / "fonts"
LOCATION_HI = {"New Delhi": "नई दिल्ली"}

_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


def date_chip(d: date, weekday: str) -> str:
    return f"{d.day} {names.GREG_MONTH_HI[d.month - 1]} · {names.WEEKDAY_HI[weekday]}"


def panchang_html(day: DayFacts, background: Path, tagline: str, swipe: bool) -> str:
    limb = day.tithis[0]
    month = names.MONTH_HI[names.lunar_month(day, limb)]
    paksha = names.PAKSHA_HI[day.paksha]
    return _env.get_template("panchang.html").render(
        fonts=FONTS.as_uri(), background=background.as_uri(),
        date_chip=date_chip(day.date, day.weekday), headline="आज का पंचांग",
        subline=f"{names.date_hi(day.date, day.weekday)} · {month} {paksha} पक्ष",
        tagline=tagline, rows=names.panchang_rows_hi(day),
        location=LOCATION_HI.get(day.location, day.location), swipe=swipe)


def rashi_html(day: DayFacts, background: Path, slide: dict, page: str) -> str:
    return _env.get_template("rashi.html").render(
        fonts=FONTS.as_uri(), background=background.as_uri(),
        date_chip=date_chip(day.date, day.weekday), page=page, **slide)


def festival_html(day: DayFacts, background: Path, cap: dict) -> str:
    return _env.get_template("post.html").render(
        fonts=FONTS.as_uri(), background=background.as_uri(), date_chip=date_chip(day.date, day.weekday),
        headline=cap["image_headline"], subline=cap["image_subline"], tagline=cap["image_tagline"],
        times=names.timing_rows_hi(day.timings), upay=cap.get("upay_hi"),
        location=LOCATION_HI.get(day.location, day.location))


STORY = (1080, 1920)


def _card(day_for_chip: DayFacts, background: Path, story: bool, **kw) -> str:
    w, h = STORY if story else (WIDTH, HEIGHT)
    return _env.get_template("card.html").render(
        fonts=FONTS.as_uri(), background=background.as_uri(), w=w, h=h, story=story,
        date_chip=date_chip(day_for_chip.date, day_for_chip.weekday),
        location=LOCATION_HI.get(day_for_chip.location, day_for_chip.location), **kw)


def evening_html(tomorrow: DayFacts, background: Path, ev: dict, story: bool) -> str:
    """8 PM 'Kal ka din' post (feed) or its story version."""
    extra = {"cta": "अपने सवाल ज्योतिषियों से पूछें", "cta_sub": load_yaml("brand")["story_cta_sub"]} if story else {}
    return _card(tomorrow, background, story, kicker="कल का दिन", title=ev["title_hi"], subline=ev["subline_hi"],
                 times=names.timing_rows_hi([tuple(t) for t in ev["times"]]), points=ev["points_hi"],
                 points_title="कल की तैयारी", footer_right="सेव करें, कल काम आएगा 🔖", **extra)


def morning_story_html(day: DayFacts, background: Path, cap: dict) -> str:
    """6 AM story: the day's key info + app CTA, with room for the link sticker."""
    if cap["kind"] == "panchang":
        times = names.timing_rows_hi([("Rahu Kaal", day.rahu_kaal.fmt())] +
                                     ([("Abhijit Muhurat", day.abhijit_muhurat.fmt())] if names.abhijit_applies(day) else []))
        return _card(day, background, True, kicker="आज का दिन", title="आज का पंचांग",
                     subline=names.subline_hi(day, day.tithis[0], with_time=True), times=times,
                     cta="अपनी राशि का आज का राशिफल देखें", cta_sub=load_yaml("brand")["story_cta_sub"])
    return _card(day, background, True, kicker="आज का पर्व", title=cap["image_headline"], subline=cap["image_subline"],
                 times=names.timing_rows_hi(day.timings), upay=cap.get("upay_hi"),
                 cta="अपनी कुंडली के अनुसार उपाय जानें", cta_sub=load_yaml("brand")["story_cta_sub"])


def reel_frame_html(day: DayFacts, background: Path, text: str, kicker: str = "",
                    hook: bool = False, cta: bool = False) -> str:
    """One reel slide (1080x1920): a single spoken line, large, in the safe middle band."""
    return _env.get_template("reel.html").render(
        fonts=FONTS.as_uri(), background=background.as_uri(), date_chip=date_chip(day.date, day.weekday),
        text=text, kicker=kicker, hook=hook, cta=cta)


def cta_html(day: DayFacts, background: Path, page: str) -> str:
    """Closing slide: one message pointing to the app (other rashifals + astrologers)."""
    return _env.get_template("cta.html").render(
        fonts=FONTS.as_uri(), background=background.as_uri(), date_chip=date_chip(day.date, day.weekday),
        page=page, cta=load_yaml("brand")["cta_slide"])


def to_png(htmls: list[tuple]) -> list[Path]:
    """Render each (html, out_path[, (w, h)]); default size 1080x1350."""
    from playwright.sync_api import sync_playwright
    outs = []
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        for i, (html, out, *size) in enumerate(htmls):
            w, h = size[0] if size else (WIDTH, HEIGHT)
            page.set_viewport_size({"width": w, "height": h})
            src = Path(tmp) / f"slide{i}.html"
            src.write_text(html, encoding="utf-8")
            page.goto(src.as_uri())
            page.evaluate("document.fonts.ready")
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
            outs.append(out)
        browser.close()
    return outs
