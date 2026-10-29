"""Daily rashifal facts for Panchang-day carousels.

Predictions are kalagana's rule-based gochara (planetary transits counted from
the Moon sign), computed offline. The LLM only phrases them; the band label,
rashi names and which grahas are favourable/challenging all come from here.
"""
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from kalagana import daily_rashifal

from postgen.config import STATE_DIR

RASHI_ROMAN = ["Mesh", "Vrishabh", "Mithun", "Kark", "Singh", "Kanya",
               "Tula", "Vrishchik", "Dhanu", "Makar", "Kumbh", "Meen"]
RASHI_HI = ["मेष", "वृषभ", "मिथुन", "कर्क", "सिंह", "कन्या", "तुला", "वृश्चिक", "धनु", "मकर", "कुंभ", "मीन"]
RASHI_SYMBOL = ["♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓"]
GRAHA_ROMAN = {"Sun": "Surya", "Moon": "Chandra", "Mars": "Mangal", "Mercury": "Budh", "Jupiter": "Guru",
               "Venus": "Shukra", "Saturn": "Shani", "Rahu": "Rahu", "Ketu": "Ketu"}
BAND = {  # kalagana band → (Hinglish, Devanagari) label shown on the post
    "Excellent": ("Ati Shubh", "अति शुभ"),
    "Good": ("Shubh", "शुभ"),
    "Average": ("Saamaanya", "सामान्य"),
    "Mixed": ("Mishrit", "मिश्रित"),
    "Challenging": ("Saavdhaani rakhein", "सावधानी रखें"),
}
ROTATION_FILE = STATE_DIR / "rashifal_rotation.json"


@dataclass
class RashiFacts:
    index: int
    roman: str
    hi: str
    symbol: str
    band: str              # kalagana band
    band_roman: str
    band_hi: str
    favourable: list[dict]   # [{"graha": "Chandra", "area": "self, health and personal matters"}]
    challenging: list[dict]

    def for_llm(self) -> dict:
        return {"rashi": self.roman, "overall": self.band_roman,
                "favourable_transits": self.favourable, "challenging_transits": self.challenging}


def rashi_facts(index: int, d: date, tz: str) -> RashiFacts:
    # Evaluate at local noon so the day's reading reflects the Moon's position that day.
    when = datetime.combine(d, time(12, 0), ZoneInfo(tz))
    r = daily_rashifal(index, when)
    by_graha = {t.graha: t for t in r.transits}

    def side(names):
        out = []
        for n in names:
            g = n.split(" (")[0]   # "Sun (7th)" → "Sun"
            out.append({"graha": GRAHA_ROMAN[g], "area": by_graha[g].house_theme})
        return out

    return RashiFacts(index=index, roman=RASHI_ROMAN[index], hi=RASHI_HI[index], symbol=RASHI_SYMBOL[index],
                      band=r.band, band_roman=BAND[r.band][0], band_hi=BAND[r.band][1],
                      favourable=side(r.favourable), challenging=side(r.challenging))


def rashis_for(d: date, per_day: int = 3) -> list[int]:
    """Rashis featured on `d`. Each new date takes the next `per_day` rashis in
    order, so all 12 are covered every 4 Panchang days; a date keeps its rashis
    when regenerated."""
    state = {"cursor": 0, "assigned": {}}
    if ROTATION_FILE.exists():
        state = json.loads(ROTATION_FILE.read_text())
    key = d.isoformat()
    if key not in state["assigned"]:
        c = state["cursor"]
        state["assigned"][key] = [(c + i) % 12 for i in range(per_day)]
        state["cursor"] = (c + per_day) % 12
        ROTATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        ROTATION_FILE.write_text(json.dumps(state, indent=2))
    return state["assigned"][key]
