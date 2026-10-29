"""Evening (8 PM) "Kal ka din" post: previews TOMORROW so followers can prepare.

Same rules as the morning post: the LLM writes only text from facts with no
times or dates; timings and dates are added by code from computed/curated data.
"""
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import create_model

from postgen import names
from postgen.captions import SHARED_RULES, CaptionError, CaptionWriter, _camel_tag
from postgen.config import load_yaml
from postgen.models import DayFacts, Occasion, hm

EVENING_PROMPT = SHARED_RULES + """
This is the EVENING post (published around 8 PM). It previews TOMORROW so followers can prepare tonight. All facts describe tomorrow.

Field rules:
- hook: first line, max 110 characters, about tomorrow ("Kal ... hai", "Kal subah ..."). Name tomorrow's occasion, or on an ordinary day the weekday's deity. Don't reuse the opening words or sentence pattern of hooks already used this week.
- title_hi: Devanagari title for the image, max 24 characters, e.g. "कल है करवा चौथ" or "कल बुधवार: गणेश जी का दिन".
- points_hi: 2–3 short Devanagari checklist items for tomorrow, max 38 characters each: practical, traditional things to prepare or do (e.g. "पूजा की थाली और करवा तैयार रखें", "सुबह सूर्य को जल अर्पित करें"). No times.
- lines: 2–3 short Hinglish lines on why tomorrow matters and how to prepare tonight. Tradition and belief, never promises.
- upay: exactly one simple remedy for tomorrow, Hinglish, imperative ("Kal subah ... karein"). It belongs to tomorrow's main occasion, or on an ordinary day to the weekday's deity.
- hashtags: 2–4 picked from the allowed list, most relevant first.
Do not add a call-to-action or app mention; the app adds those."""


@dataclass
class EveningCaption:
    for_date: str                 # the day being previewed (tomorrow)
    llm: dict
    caption: str                  # Instagram ("link in bio")
    caption_facebook: str         # with the clickable Play Store link
    hashtags: list[str]
    title_hi: str
    subline_hi: str               # tomorrow's date + tithi, code-built
    points_hi: list[str]
    times: list[tuple[str, str]]  # (Hinglish label, value) shown on the post, code-built
    cta: str
    app_mention: bool
    attempts: int
    usage: dict = field(default_factory=dict)


class EveningWriter:
    def __init__(self, base: CaptionWriter):
        self.base = base
        self.weekdays = load_yaml("occasions")["weekdays"]
        tags = [t for t in base.allowed_tags if t not in ("#AajKaPanchang", "#Panchang")]
        self.model = create_model("EveningDraft", hook=(str, ...), title_hi=(str, ...), points_hi=(list[str], ...),
                                  lines=(list[str], ...), upay=(str, ...),
                                  hashtags=(list[Literal[tuple(tags)]], ...))

    @staticmethod
    def timings(tomorrow: DayFacts, occ: Occasion) -> list[tuple[str, str]]:
        """Curated Drik timings on festival days; otherwise Rahu kaal + sunrise."""
        if tomorrow.timings:
            return list(tomorrow.timings)
        return [("Rahu Kaal", tomorrow.rahu_kaal.fmt()), ("Sunrise", hm(tomorrow.sunrise))]

    def facts(self, tomorrow: DayFacts, occ: Occasion, rule_tithi: Optional[str]) -> dict:
        limb = names.occasion_limb(tomorrow, rule_tithi)
        wd = self.weekdays[tomorrow.weekday]
        f = {
            "tomorrow_weekday": names.WEEKDAY_ROMAN[tomorrow.weekday],
            "weekday_deity": wd["deity"],
            "hindu_month": names.MONTH_ROMAN[names.lunar_month(tomorrow, limb)],
            "tithis_tomorrow": [t.name for t in tomorrow.tithis],
            "observances_tomorrow": tomorrow.festivals,
            "timings_shown_below": [label for label, _ in self.timings(tomorrow, occ)],
            "hooks_already_used_this_week": self.base.hooks_used,
        }
        if occ.kind != "aaj-ka-panchang":
            f = {"tomorrow_occasion": occ.name_en,
                 "occasion_type": "major festival" if occ.priority == 1 else "vrat/parv",
                 "occasion_tithi": limb.name, **f}
        else:
            f = {"tomorrow_occasion": None, "note": "Ordinary day: focus on the weekday deity.", **f}
        return f

    def problems(self, data: dict, tomorrow: DayFacts, occ: Occasion) -> list[str]:
        flat = {k: " ".join(v) if isinstance(v, list) else v for k, v in data.items() if k != "hashtags"}
        allowed = " ".join([occ.name_en, *tomorrow.festivals, *(t.name for t in tomorrow.tithis)])
        shown = [label for label, _ in self.timings(tomorrow, occ)]
        probs = self.base.guardrail_problems(flat, allowed, shown)
        if len(data["hook"]) > self.base.rules["hook_max_chars"]:
            probs.append(f"hook is {len(data['hook'])} characters; max is 110")
        if not 2 <= len(data["hashtags"]) <= 4:
            probs.append(f"pick 2–4 hashtags, not {len(data['hashtags'])}")
        if not 2 <= len(data["lines"]) <= 3:
            probs.append(f"lines must be 2–3, not {len(data['lines'])}")
        if not 2 <= len(data["points_hi"]) <= 3:
            probs.append(f"points_hi must be 2–3 items, not {len(data['points_hi'])}")
        for p in data["points_hi"]:
            if len(p) > 45:
                probs.append(f"points_hi item '{p}' is {len(p)} characters; max is 38")
        if len(data["title_hi"]) > 30:
            probs.append(f"title_hi is {len(data['title_hi'])} characters; max is 24")
        for k in ("title_hi", "points_hi"):
            if not re.search(r"[ऀ-ॿ]", flat[k]):
                probs.append(f"{k} must be in Devanagari")
        if occ.kind != "aaj-ka-panchang" and occ.name_hi not in data["title_hi"]:
            probs.append(f"title_hi must contain '{occ.name_hi}'")
        return probs

    def write(self, today: DayFacts, tomorrow: DayFacts, occ: Occasion, rule: Optional[dict]) -> EveningCaption:
        rule_tithi = (rule or {}).get("tithi")
        facts = self.facts(tomorrow, occ, rule_tithi)
        feedback, usage = [], {"input_tokens": 0, "output_tokens": 0}
        tries = self.base.settings.get("max_attempts", 3)
        for attempt in range(1, tries + 1):
            data, u = self.base._call(self.model, facts, feedback, system=EVENING_PROMPT)
            usage = {k: usage[k] + u[k] for k in usage}
            feedback = self.problems(data, tomorrow, occ)
            if not feedback:
                self.base.hooks_used.append(data["hook"])
                return self.assemble(data, today, tomorrow, occ, rule_tithi, attempt, usage)
        raise CaptionError(f"evening {today.date}: still invalid after {tries} attempts: {feedback}")

    def assemble(self, data, today, tomorrow, occ, rule_tithi, attempts, usage) -> EveningCaption:
        brand = self.base.brand
        n = today.date.toordinal()
        cta = brand["evening_ctas"][n % len(brand["evening_ctas"])]
        app = (n + 1) % brand["app_mention_every_n_posts"] == 0     # offset from the morning rotation
        tags = ([_camel_tag(occ.name_en)] if occ.kind != "aaj-ka-panchang" else []) + list(data["hashtags"])
        seen, hashtags = set(), []
        for t in tags:
            if t.lower() not in seen:
                seen.add(t.lower())
                hashtags.append(t)
        hashtags = hashtags[: self.base.max_tags]
        limb = names.occasion_limb(tomorrow, rule_tithi)
        times = self.timings(tomorrow, occ)
        icon = {"Rahu Kaal": "⚠️", "Sunrise": "🌅"}
        timing_lines = "".join(f"\n{icon.get(label, '🕉️')} {label}: {value}" for label, value in times)
        parts = [data["hook"],
                 f"📅 Kal: {names.date_roman(tomorrow.date, tomorrow.weekday)} · {names.tithi_line_roman(tomorrow, limb)}"
                 f"{timing_lines}\n📍 {tomorrow.location} ke samay anusaar",
                 "\n".join(data["lines"]),
                 f"🪔 Kal ka upay: {data['upay']}",
                 cta + (f"\n{brand['app_mention']}" if app else ""),
                 " ".join(hashtags)]
        text = "\n\n".join(parts)
        fb = text.replace("(link in bio)", f"👉 {brand['facebook_link']}")
        return EveningCaption(for_date=str(tomorrow.date), llm=data, caption=text, caption_facebook=fb,
                              hashtags=hashtags, title_hi=data["title_hi"],
                              subline_hi=names.subline_hi(tomorrow, limb), points_hi=data["points_hi"],
                              times=times, cta=cta, app_mention=app, attempts=attempts, usage=usage)
