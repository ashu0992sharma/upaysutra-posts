"""Caption generation. The LLM writes only devotional text (hook, significance,
upay, thought, Devanagari tagline) from facts that contain no times or dates;
code validates it, then assembles the final caption with code-built fact lines."""
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field, create_model

from postgen.config import load_yaml, location_from_env
from postgen.models import DayFacts, Occasion
from postgen import names, rashifal

FALLBACK_BETA = "server-side-fallback-2026-07-01"

SHARED_RULES = """You write daily Instagram/Facebook captions for UpaySutra, an astrology consultation app for Hindi-speaking users in North India.

Voice: warm, simple, devotional Hinglish in Roman script (e.g. "Aaj Rama Ekadashi hai — Shri Hari ki kripa paane ka sundar din 🙏"). Short lines. Like a respectful family elder, never a salesman. 1–2 emojis per line at most.

You are given the day's facts as JSON. Those facts are the only facts you may use:
- Never state any clock time, calendar date, English month name, muhurat time or tithi start/end. (Hindu month names from the facts, like "Kartik maas", are fine.) The app adds timings itself, below your text.
- "timings_shown_below" lists the only timings the post will show (the app prints them right after the upay, before your body text). Don't point to them ("neeche/upar diya hai"), and never mention a muhurat or timing that isn't listed.
- Mention only tithis, festivals and nakshatras that appear in the facts.
- Describe significance as tradition and belief ("maana jaata hai", "shastron mein kaha gaya hai"), not as promised results.

Hard guardrails (posts that break these are rejected):
- No guaranteed outcomes: never "100%", "guarantee", "pakka", "zaroor milega", "turant fal", "chamatkar".
- No money promises (paise aayenge, dhan ki baarish, loan/karz mukti) and no medical or health claims (rog, bimari, ilaaj, cure).
- No fear-mongering: never "warna", "nahi to", "anarth", "shraap", "bura hoga", or threats of bad luck for not doing something.
- The upay must be exactly one simple, safe remedy anyone can do at home: lighting a diya, a short well-known mantra, daan of food or clothes, tulsi or surya arghya, etc. No fasting rules for the sick, no fire risk, nothing costly, nothing that needs an astrologer.
"""

SYSTEM_PROMPT = SHARED_RULES + """
Field rules:
- hook: the first line, max 110 characters, specific and benefit-led, names the occasion. It must not reuse the opening words or the sentence pattern of any hook already used this week (given in the facts). Don't list tithi names in the hook. Vary the shape: a question, a benefit, a small invitation, a line of bhakti.
- body: 2–4 short lines on the day's significance.
- upay: one remedy, 1–2 lines, in the imperative ("Aaj shaam ghar ke mandir mein ... jalayein"). It must belong to the main occasion: its deity, ritual or tradition (Karwa Chauth → Karwa Mata / chandra arghya, Ahoi Ashtami → Ahoi Mata). Never base it on a secondary observance from "observances_today".
- thought: one line, a gentle thought for the day.
- hashtags: 2–4 picked from the allowed list, most relevant first.
- name_hi: the occasion name in Devanagari exactly as Hindi speakers write it (e.g. "रमा एकादशी").
- image_tagline: a short Devanagari line for the post image, max 32 characters, devotional, no claims (e.g. "श्री हरि की कृपा बनी रहे").
- upay_hi (festival/vrat days): the same upay in short, simple Devanagari Hindi for the image, max 70 characters (e.g. "शाम को तुलसी के पास घी का दीपक जलाएँ").
- rashifal (Panchang days only): one entry per rashi in "rashifal", same order. Base each reading ONLY on that rashi's favourable_transits and challenging_transits (graha + life area) and its "overall" level. Write it for a follower, not an astrologer: turn the transits into plain, practical guidance about the life areas (work, family, relationships, travel, peace of mind). Never list planets; name at most ONE graha, and only if it helps (e.g. "Guru ki kripa se ..."). Gentle, practical guidance ("aaj ... par dhyan dein", "... ke liye accha samay maana ja raha hai"), never certain events. No lucky colour/number, no health or money promises, no fear. If an area is about health, speak of routine and rest, never illness; if about money, speak of careful planning, never gains.
  - line: Hinglish, max 100 characters, for the caption.
  - slide_hi: the same reading in simple Devanagari Hindi, max 90 characters, for the rashi's image slide.
Do not add a call-to-action or app mention; the app adds those."""


class CaptionError(Exception):
    pass


class RashiReading(BaseModel):
    rashi: Literal[tuple(rashifal.RASHI_ROMAN)]
    line: str
    slide_hi: str


@dataclass
class Caption:
    kind: str                     # "occasion" | "panchang"
    llm: dict                     # validated LLM fields
    caption: str                  # final text to post (Instagram: "link in bio")
    caption_facebook: str         # same, with the clickable Play Store link
    hashtags: list[str]
    image_headline: str           # Devanagari
    image_subline: str            # Devanagari, code-built (date + tithi)
    image_tagline: str            # Devanagari, LLM
    upay_hi: str                  # Devanagari upay for festival images ("" on Panchang days)
    cta: str
    app_mention: bool
    attempts: int
    usage: dict = field(default_factory=dict)
    rashifal_slides: list[dict] = field(default_factory=list)  # Panchang days: one image slide per rashi


def _camel_tag(name: str) -> str:
    return "#" + "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", name) if w)


class CaptionWriter:
    def __init__(self, client: Optional[anthropic.Anthropic] = None):
        self.settings = load_yaml("settings")["captions"]
        self.brand = load_yaml("brand")
        self.rules = load_yaml("guardrails")
        tags_cfg = load_yaml("hashtags")
        self.allowed_tags = sorted({*tags_cfg["general"], *(t for ts in tags_cfg["by_tag"].values() for t in ts)})
        panchang_only = set(tags_cfg["by_tag"]["cosmic"]) | {"#Panchang"}
        occasion_tags = [t for t in self.allowed_tags if t not in panchang_only]
        self.max_tags = tags_cfg["max_total"]
        self.client = client or anthropic.Anthropic()
        self.tz = location_from_env().tz
        self.banned = [(cat, re.compile(p, re.IGNORECASE)) for cat, ps in self.rules["banned"].items() for p in ps]
        self.timing = [re.compile(p, re.IGNORECASE) for p in self.rules["timing_patterns"]]
        self.models = {
            "occasion": create_model("OccasionCaption", hook=(str, ...), body=(list[str], ...), upay=(str, ...),
                                     hashtags=(list[Literal[tuple(occasion_tags)]], ...), name_hi=(str, ...),
                                     image_tagline=(str, ...), upay_hi=(str, ...)),
            "panchang": create_model("PanchangCaption", hook=(str, ...), thought=(str, ...),
                                     hashtags=(list[Literal[tuple(self.allowed_tags)]], ...), image_tagline=(str, ...),
                                     rashifal=(list[RashiReading], ...)),
        }
        self.hooks_used: list[str] = []

    # ---------- facts sent to the LLM (no times, no dates) ----------
    def _rashis(self, day: DayFacts) -> list[rashifal.RashiFacts]:
        cfg = self.settings.get("rashifal", {})
        if not cfg.get("enabled", True):
            return []
        return [rashifal.rashi_facts(i, day.date, self.tz) for i in rashifal.rashis_for(day.date, cfg.get("per_day", 3))]

    def _facts(self, day: DayFacts, occ: Occasion, rule_tithi: Optional[str],
               rashis: list[rashifal.RashiFacts]) -> dict:
        limb = names.occasion_limb(day, rule_tithi)
        facts = {
            "weekday": names.WEEKDAY_ROMAN[day.weekday],
            "hindu_month": names.MONTH_ROMAN[names.lunar_month(day, limb)],
            "paksha": day.paksha,
            "tithis_today": [t.name for t in day.tithis],
            "nakshatra": day.nakshatra,
            "yoga": day.yoga,
            "observances_today": day.festivals,
            "timings_shown_below": [label for label, _ in day.timings] + (["Panchang table"] if occ.kind == "aaj-ka-panchang" else []),
            "hooks_already_used_this_week": self.hooks_used,
        }
        if rashis:
            facts["rashifal"] = [r.for_llm() for r in rashis]
        if occ.kind != "aaj-ka-panchang":
            facts = {"occasion": occ.name_en, "occasion_type": "major festival" if occ.priority == 1 else "vrat/parv",
                     "occasion_tithi": limb.name, **facts}
        return facts

    # ---------- validation ----------
    def _problems(self, kind: str, data: dict, day: DayFacts, occ: Occasion, name_hi_rule: str,
                  rashis: list[rashifal.RashiFacts] = ()) -> list[str]:
        probs = []
        texts = {k: v for k, v in data.items() if k not in ("hashtags", "rashifal")}
        flat = {k: " ".join(v) if isinstance(v, list) else v for k, v in texts.items()}
        for r in data.get("rashifal") or []:
            flat[f"rashifal {r['rashi']} line"] = r["line"]
            flat[f"rashifal {r['rashi']} slide_hi"] = r["slide_hi"]
        allowed = " ".join([occ.name_en, *day.festivals, *(t.name for t in day.tithis)])
        probs += self.guardrail_problems(flat, allowed, [label for label, _ in day.timings],
                                         check_timing_refs=(kind != "panchang"))
        if len(data["hook"]) > self.rules["hook_max_chars"]:
            probs.append(f"hook is {len(data['hook'])} characters; max is 110")
        if not 2 <= len(data["hashtags"]) <= 4:
            probs.append(f"pick 2–4 hashtags, not {len(data['hashtags'])}")
        if len(data["image_tagline"]) > 40:
            probs.append(f"image_tagline is {len(data['image_tagline'])} characters; max is 32")
        if not re.search(r"[ऀ-ॿ]", data["image_tagline"]):
            probs.append("image_tagline must be in Devanagari")
        if kind == "panchang":
            probs += self._rashifal_problems(data.get("rashifal") or [], rashis)
        if kind == "occasion":
            if not 2 <= len(data["body"]) <= 4:
                probs.append(f"body must be 2–4 lines, not {len(data['body'])}")
            if len(data["upay_hi"]) > 80:
                probs.append(f"upay_hi is {len(data['upay_hi'])} characters; max is 70")
            if not re.search(r"[\u0900-\u097F]", data["upay_hi"]):
                probs.append("upay_hi must be in Devanagari")
            if name_hi_rule not in data["name_hi"]:
                probs.append(f"name_hi must contain '{name_hi_rule}'")
        return probs

    def guardrail_problems(self, flat: dict[str, str], allowed_text: str, shown_labels: list[str],
                           check_timing_refs: bool = True) -> list[str]:
        """Checks shared by every LLM-written field: banned claims, no times/dates,
        only the given tithis, no references to timings the post doesn't show."""
        probs = []
        for k, v in flat.items():
            for cat, pat in self.banned:
                if m := pat.search(v):
                    probs.append(f"{k}: contains '{m.group(0)}' ({cat.replace('_', ' ')}) — remove it")
            for pat in self.timing:
                if m := pat.search(v):
                    probs.append(f"{k}: contains '{m.group(0)}' — never write times, dates or months")
            for word in _tithi_mentions(v) - _tithi_mentions(allowed_text):
                probs.append(f"{k}: mentions tithi '{word}', which is not in the facts — remove it")
        if check_timing_refs:
            alltext = " ".join(flat.values())
            shown = " ".join(shown_labels).lower()
            if re.search(r"\bmuhur?a?t\b", alltext, re.IGNORECASE) and "muhurat" not in shown:
                probs.append("mentions a muhurat, but no muhurat is shown on this post — remove it")
            if re.search(r"\b(neeche|upar)\s+(diya|diye|di)\b", alltext, re.IGNORECASE):
                probs.append("don't point to where timings are ('neeche/upar diya hai') — the post shows them already")
        return probs

    def _rashifal_problems(self, readings: list[dict], rashis: list[rashifal.RashiFacts]) -> list[str]:
        want = [r.roman for r in rashis]
        got = [r["rashi"] for r in readings]
        if got != want:
            return [f"rashifal must have exactly these rashis in this order: {want} (got {got})"]
        probs = []
        for r, facts in zip(readings, rashis):
            given = {t["graha"] for t in facts.favourable + facts.challenging}
            for g in _graha_mentions(r["line"] + " " + r["slide_hi"]) - given:
                probs.append(f"rashifal {r['rashi']}: mentions {g}, which isn't in its transits — use only {sorted(given)}")
            if len(_graha_mentions(r["slide_hi"])) > 1 or len(_graha_mentions(r["line"])) > 1:
                probs.append(f"rashifal {r['rashi']}: don't list planets — name at most one graha, then give plain guidance")
            if len(r["line"]) > 110:
                probs.append(f"rashifal {r['rashi']} line is {len(r['line'])} characters; max is 100")
            if len(r["slide_hi"]) > 100:
                probs.append(f"rashifal {r['rashi']} slide_hi is {len(r['slide_hi'])} characters; max is 90")
            if not re.search(r"[\u0900-\u097F]", r["slide_hi"]):
                probs.append(f"rashifal {r['rashi']} slide_hi must be in Devanagari")
        return probs

    # ---------- LLM call ----------
    def _call(self, model_cls, facts: dict, feedback: list[str], system: str = SYSTEM_PROMPT):
        msg = "Day facts:\n" + json.dumps(facts, ensure_ascii=False, indent=2)
        if feedback:
            msg += "\n\nYour previous draft was rejected for these reasons. Fix all of them:\n- " + "\n- ".join(feedback)
        resp = self.client.beta.messages.parse(
            model=self.settings["model"],
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": self.settings.get("effort", "medium")},
            system=system,
            messages=[{"role": "user", "content": msg}],
            output_format=model_cls,
        )
        if resp.stop_reason == "refusal":
            raise CaptionError(f"model declined: {getattr(resp.stop_details, 'category', None)}")
        if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
            raise CaptionError(f"no usable output (stop_reason={resp.stop_reason})")
        u = resp.usage
        return resp.parsed_output.model_dump(), {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}

    def write(self, day: DayFacts, occ: Occasion, rule: Optional[dict]) -> Caption:
        kind = "panchang" if occ.kind == "aaj-ka-panchang" else "occasion"
        rule_tithi = (rule or {}).get("tithi")
        rashis = self._rashis(day) if kind == "panchang" else []
        facts = self._facts(day, occ, rule_tithi, rashis)
        feedback, usage = [], {"input_tokens": 0, "output_tokens": 0}
        tries = self.settings.get("max_attempts", 3)
        for attempt in range(1, tries + 1):
            data, u = self._call(self.models[kind], facts, feedback)
            usage = {k: usage[k] + u[k] for k in usage}
            feedback = self._problems(kind, data, day, occ, occ.name_hi, rashis)
            if not feedback:
                self.hooks_used.append(data["hook"])
                return self._assemble(kind, data, day, occ, rule_tithi, attempt, usage, rashis)
        raise CaptionError(f"{day.date}: still invalid after {tries} attempts: {feedback}")

    def reassemble(self, day: DayFacts, occ: Occasion, rule: Optional[dict], saved: dict) -> Caption:
        """Rebuild a saved caption with the current layout/brand text, without an
        LLM call. Body lines pointing at timings ("neeche diya hai") are dropped:
        timings now come before the body."""
        kind = "panchang" if occ.kind == "aaj-ka-panchang" else "occasion"
        data = dict(saved["llm"])
        if "body" in data:
            data["body"] = [ln for ln in data["body"]
                            if not re.search(r"\b(neeche|upar)\s+(diya|diye|di)\b", ln, re.IGNORECASE)]
        rashis = self._rashis(day) if kind == "panchang" else []
        return self._assemble(kind, data, day, occ, (rule or {}).get("tithi"), saved.get("attempts", 1),
                              saved.get("usage", {}), rashis)

    # ---------- assembly (all facts from code) ----------
    def _assemble(self, kind, data, day, occ, rule_tithi, attempts, usage, rashis=()) -> Caption:
        n = day.date.toordinal()
        ctas = self.brand["ctas"]
        cta = ctas[n % len(ctas)]
        app = n % self.brand["app_mention_every_n_posts"] == 0
        slides = []
        tags = [_camel_tag(occ.name_en)] + [t for t in data["hashtags"]]
        seen, hashtags = set(), []
        for t in tags:
            if t.lower() not in seen:
                seen.add(t.lower())
                hashtags.append(t)
        hashtags = hashtags[: self.max_tags]

        limb = names.occasion_limb(day, rule_tithi)
        where = f"📍 {day.location} ke samay anusaar"
        if kind == "panchang":
            rows = "\n".join(f"{_ROW_ICON[label]} {label}: {value}" for label, value in names.panchang_rows(day))
            month = names.MONTH_ROMAN[names.lunar_month(day, day.tithis[0])]
            # Order: what people act on first (before "See more"), background last.
            parts = [data["hook"],
                     f"📅 {names.date_roman(day.date, day.weekday)} · {month}, {day.paksha} Paksha\n{rows}\n{where}"]
            headline = occ.name_hi
            if rashis:
                lines = [f"{r.symbol} {r.roman} ({r.band_roman}): {rd['line']}"
                         for r, rd in zip(rashis, data["rashifal"])]
                parts.append("🔮 Aaj ka rashifal (swipe karein 👉)\n" + "\n".join(lines))
                slides = [{"rashi": r.roman, "symbol": r.symbol, "name_hi": r.hi, "band": r.band,
                           "band_hi": r.band_hi, "text_hi": rd["slide_hi"]}
                          for r, rd in zip(rashis, data["rashifal"])]
                app = False  # the rashifal CTA below is this post's app mention
            parts.append(f"💭 {data['thought']}")
        else:
            timing_lines = "".join(f"\n🕉️ {label}: {value}" for label, value in day.timings)
            # Order: hook → upay → timings (visible before "See more") → significance.
            parts = [data["hook"], f"🪔 Aaj ka upay: {data['upay']}",
                     f"📅 {names.date_roman(day.date, day.weekday)} · {names.tithi_line_roman(day, limb)}"
                     f"{timing_lines}\n{where}",
                     "\n".join(data["body"])]
            headline = data["name_hi"]
        closing = cta + (f"\n{self.brand['app_mention']}" if app else "")
        if slides:
            closing += f"\n{self.brand['rashifal_cta']}"
        parts += [closing, " ".join(hashtags)]
        text = "\n\n".join(parts)
        fb = text.replace("(link in bio)", f"👉 {self.brand['facebook_link']}")
        return Caption(kind=kind, llm=data, caption=text, caption_facebook=fb, hashtags=hashtags,
                       image_headline=headline, image_subline=names.subline_hi(day, limb, with_time=(kind == "panchang")),
                       image_tagline=data["image_tagline"], upay_hi=data.get("upay_hi", ""), cta=cta, app_mention=app or bool(slides),
                       attempts=attempts, usage=usage, rashifal_slides=slides)


_ROW_ICON = {"Tithi": "🌙", "Nakshatra": "⭐", "Yoga": "🔆", "Sunrise / Sunset": "🌅",
             "Rahu Kaal": "⚠️", "Abhijit Muhurat": "✨"}

# Spelling variants → canonical tithi, for "only today's tithis" checks.
_TITHI_WORDS = {
    "pratipada": "pratipada", "padwa": "pratipada", "dwitiya": "dwitiya", "dooj": "dwitiya",
    "tritiya": "tritiya", "chaturthi": "chaturthi", "chauth": "chaturthi", "panchami": "panchami",
    "shashthi": "shashthi", "shashti": "shashthi", "saptami": "saptami", "ashtami": "ashtami",
    "navami": "navami", "dashami": "dashami", "ekadashi": "ekadashi", "ekadasi": "ekadashi",
    "gyaras": "ekadashi", "dwadashi": "dwadashi", "trayodashi": "trayodashi", "teras": "trayodashi",
    "chaturdashi": "chaturdashi", "chaudas": "chaturdashi", "purnima": "purnima", "poornima": "purnima",
    "punam": "purnima", "amavasya": "amavasya", "amavas": "amavasya",
}
_TITHI_RE = re.compile(r"\b(" + "|".join(sorted(_TITHI_WORDS, key=len, reverse=True)) + r")\b", re.IGNORECASE)


def _tithi_mentions(text: str) -> set[str]:
    return {_TITHI_WORDS[m.lower()] for m in _TITHI_RE.findall(text)}


_GRAHA_WORDS = {"surya": "Surya", "sun": "Surya", "chandra": "Chandra", "chandrama": "Chandra", "moon": "Chandra",
                "mangal": "Mangal", "mars": "Mangal", "budh": "Budh", "mercury": "Budh", "guru": "Guru",
                "brihaspati": "Guru", "jupiter": "Guru", "shukra": "Shukra", "venus": "Shukra", "shani": "Shani",
                "saturn": "Shani", "rahu": "Rahu", "ketu": "Ketu",
                "सूर्य": "Surya", "चंद्र": "Chandra", "चंद्रमा": "Chandra", "मंगल": "Mangal", "बुध": "Budh",
                "गुरु": "Guru", "बृहस्पति": "Guru", "शुक्र": "Shukra", "शनि": "Shani", "राहु": "Rahu", "केतु": "Ketu"}
_GRAHA_RE = re.compile(r"(?<![\w\u0900-\u097F])(" + "|".join(sorted(map(re.escape, _GRAHA_WORDS), key=len, reverse=True))
                       + r")(?![\w\u0900-\u097F])", re.IGNORECASE)


def _graha_mentions(text: str) -> set[str]:
    return {_GRAHA_WORDS[m.lower()] for m in _GRAHA_RE.findall(text)}
