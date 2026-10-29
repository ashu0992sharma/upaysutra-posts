"""Hinglish / Devanagari renderings of computed panchang names, and the
code-built fact lines (dates, tithis, timings) that captions and images show."""
from datetime import date, datetime, timedelta
from typing import Optional

from postgen.models import DayFacts, Limb, hm

WEEKDAY_ROMAN = {"Monday": "Somvar", "Tuesday": "Mangalvar", "Wednesday": "Budhvar", "Thursday": "Guruvar",
                 "Friday": "Shukravar", "Saturday": "Shanivar", "Sunday": "Ravivar"}
WEEKDAY_HI = {"Monday": "सोमवार", "Tuesday": "मंगलवार", "Wednesday": "बुधवार", "Thursday": "गुरुवार",
              "Friday": "शुक्रवार", "Saturday": "शनिवार", "Sunday": "रविवार"}
GREG_MONTH_HI = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]

# kalagana purnimanta month → common North-Indian spelling
LUNAR_MONTHS = ["Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada",
                "Ashwin", "Kartika", "Margashirsha", "Pausha", "Magha", "Phalguna"]
MONTH_ROMAN = {"Chaitra": "Chaitra", "Vaishakha": "Vaishakh", "Jyeshtha": "Jyeshtha", "Ashadha": "Ashadh",
               "Shravana": "Shravan", "Bhadrapada": "Bhadrapad", "Ashwin": "Ashwin", "Kartika": "Kartik",
               "Margashirsha": "Margashirsh", "Pausha": "Paush", "Magha": "Magh", "Phalguna": "Phalgun"}
MONTH_HI = {"Chaitra": "चैत्र", "Vaishakha": "वैशाख", "Jyeshtha": "ज्येष्ठ", "Ashadha": "आषाढ़",
            "Shravana": "श्रावण", "Bhadrapada": "भाद्रपद", "Ashwin": "आश्विन", "Kartika": "कार्तिक",
            "Margashirsha": "मार्गशीर्ष", "Pausha": "पौष", "Magha": "माघ", "Phalguna": "फाल्गुन"}
PAKSHA_HI = {"Shukla": "शुक्ल", "Krishna": "कृष्ण"}
TITHI_HI = {"Pratipada": "प्रतिपदा", "Dwitiya": "द्वितीया", "Tritiya": "तृतीया", "Chaturthi": "चतुर्थी",
            "Panchami": "पंचमी", "Shashthi": "षष्ठी", "Saptami": "सप्तमी", "Ashtami": "अष्टमी",
            "Navami": "नवमी", "Dashami": "दशमी", "Ekadashi": "एकादशी", "Dwadashi": "द्वादशी",
            "Trayodashi": "त्रयोदशी", "Chaturdashi": "चतुर्दशी", "Purnima": "पूर्णिमा", "Amavasya": "अमावस्या"}


def tithi_hi(name: str) -> str:
    """'Krishna Ekadashi' → 'कृष्ण एकादशी'; 'Purnima' → 'पूर्णिमा'."""
    parts = name.split()
    if len(parts) == 2:
        return f"{PAKSHA_HI[parts[0]]} {TITHI_HI[parts[1]]}"
    return TITHI_HI[name]


def date_roman(d: date, weekday: str) -> str:
    return f"{WEEKDAY_ROMAN[weekday]}, {d.day} {d.strftime('%b')} {d.year}"


def date_hi(d: date, weekday: str) -> str:
    return f"{WEEKDAY_HI[weekday]}, {d.day} {GREG_MONTH_HI[d.month - 1]} {d.year}"


def occasion_limb(day: DayFacts, tithi: Optional[str]) -> Limb:
    """The tithi limb the occasion is about (falls back to the sunrise tithi)."""
    if tithi:
        for limb in day.tithis:
            if limb.name == tithi or limb.name.endswith(" " + tithi) or tithi.endswith(limb.name):
                return limb
    return day.tithis[0]


def lunar_month(day: DayFacts, limb: Limb) -> str:
    """Purnimanta month of `limb`. kalagana gives the month at sunrise; a
    Krishna Pratipada that starts after a sunrise Purnima is already next month."""
    month = day.hindu_month
    if limb is not day.tithis[0] and day.tithis[0].name == "Purnima" and limb.name.startswith("Krishna"):
        month = LUNAR_MONTHS[(LUNAR_MONTHS.index(month) + 1) % 12]
    return month


# Times after midnight belong to the same panchang day (it runs sunrise to
# sunrise). "kal/कल" is ambiguous in Hindi (yesterday or tomorrow), so they are
# written as "der raat" (before 4 AM) or "agli subah" (4 AM to sunrise).
def _next_day_prefix(t: datetime, day: DayFacts, roman: bool) -> str:
    if t.date() <= day.date:
        return ""
    late = t.hour < 4
    return ("der raat " if late else "agli subah ") if roman else ("देर रात " if late else "अगली सुबह ")


def _when(day: DayFacts, limb: Limb, starts: bool) -> str:
    t = limb.start if starts else limb.end
    return f"{_next_day_prefix(t, day, roman=True)}{hm(t)} {'se' if starts else 'tak'}"


def limb_timing(day: DayFacts, limb: Limb, limbs: list[Limb]) -> str:
    """'10:37 AM tak' / '2:53 PM se' / 'poore din'."""
    if len(limbs) == 1:
        return "poore din"
    if limb is limbs[0]:
        return _when(day, limb, starts=False)
    return _when(day, limb, starts=True)


def _when_hi(day: DayFacts, limb: Limb, limbs: list[Limb]) -> str:
    if len(limbs) == 1:
        return "पूरे दिन"
    if limb is limbs[0]:
        t, word = limb.end, "तक"
    else:
        t, word = limb.start, "से"
    clock = time_hi(t)                       # "रात 1:09"
    if t.date() > day.date:                  # "देर रात 1:09" / "अगली सुबह 5:10"
        clock = _next_day_prefix(t, day, roman=False) + clock.split(" ", 1)[1]
    return f"{clock} {word}"


def tithi_line_roman(day: DayFacts, limb: Limb) -> str:
    """'Kartik Krishna Ekadashi'. No computed time: on occasion posts the only
    timings shown are the curated Drik ones (computed tithi ends drift ~3 min)."""
    return f"{MONTH_ROMAN[lunar_month(day, limb)]} {limb.name}"


def subline_hi(day: DayFacts, limb: Limb, with_time: bool = False) -> str:
    """Image subline: date + tithi in Devanagari, e.g.
    'गुरुवार, 5 नवंबर 2026 · कार्तिक कृष्ण एकादशी' (+ '(10:37 AM तक)' on Panchang posts)."""
    month = MONTH_HI[lunar_month(day, limb)]
    line = f"{date_hi(day.date, day.weekday)} · {month} {tithi_hi(limb.name)}"
    return f"{line} ({_when_hi(day, limb, day.tithis)})" if with_time else line


def abhijit_applies(day: DayFacts) -> bool:
    # Abhijit muhurat is not considered auspicious on Wednesdays (Drik shows "None").
    return day.abhijit_muhurat is not None and day.weekday != "Wednesday"


def panchang_rows(day: DayFacts) -> list[tuple[str, str]]:
    """(label, value) rows for Panchang captions and the Panchang image table."""
    t0 = day.tithis[0]
    tithi = f"{t0.name} ({limb_timing(day, t0, day.tithis)})"
    if len(day.tithis) > 1:
        tithi += f", phir {day.tithis[1].name}"
    n0 = day.nakshatras[0]
    nak = f"{n0.name} ({limb_timing(day, n0, day.nakshatras)})"
    if len(day.nakshatras) > 1:
        nak += f", phir {day.nakshatras[1].name}"
    rows = [
        ("Tithi", tithi),
        ("Nakshatra", nak),
        ("Yoga", f"{day.yogas[0].name} ({limb_timing(day, day.yogas[0], day.yogas)})"),
        ("Sunrise / Sunset", f"{hm(day.sunrise)} / {hm(day.sunset)}"),
        ("Rahu Kaal", day.rahu_kaal.fmt()),
    ]
    if abhijit_applies(day):
        rows.append(("Abhijit Muhurat", day.abhijit_muhurat.fmt()))
    return rows


NAKSHATRA_HI = {
    "Ashwini": "अश्विनी", "Bharani": "भरणी", "Krittika": "कृत्तिका", "Rohini": "रोहिणी", "Mrigashira": "मृगशिरा",
    "Ardra": "आर्द्रा", "Punarvasu": "पुनर्वसु", "Pushya": "पुष्य", "Ashlesha": "आश्लेषा", "Magha": "मघा",
    "Purva Phalguni": "पूर्वा फाल्गुनी", "Uttara Phalguni": "उत्तरा फाल्गुनी", "Hasta": "हस्त", "Chitra": "चित्रा",
    "Swati": "स्वाति", "Vishakha": "विशाखा", "Anuradha": "अनुराधा", "Jyeshtha": "ज्येष्ठा", "Mula": "मूल",
    "Purva Ashadha": "पूर्वाषाढ़ा", "Uttara Ashadha": "उत्तराषाढ़ा", "Shravana": "श्रवण", "Dhanishta": "धनिष्ठा",
    "Shatabhisha": "शतभिषा", "Purva Bhadrapada": "पूर्वा भाद्रपद", "Uttara Bhadrapada": "उत्तरा भाद्रपद", "Revati": "रेवती",
}
YOGA_HI = {
    "Vishkambha": "विष्कुंभ", "Priti": "प्रीति", "Ayushman": "आयुष्मान", "Saubhagya": "सौभाग्य", "Shobhana": "शोभन",
    "Atiganda": "अतिगंड", "Sukarma": "सुकर्मा", "Dhriti": "धृति", "Shula": "शूल", "Ganda": "गंड", "Vriddhi": "वृद्धि",
    "Dhruva": "ध्रुव", "Vyaghata": "व्याघात", "Harshana": "हर्षण", "Vajra": "वज्र", "Siddhi": "सिद्धि",
    "Vyatipata": "व्यतीपात", "Variyana": "वरीयान", "Parigha": "परिघ", "Shiva": "शिव", "Siddha": "सिद्ध",
    "Sadhya": "साध्य", "Shubha": "शुभ", "Shukla": "शुक्ल", "Brahma": "ब्रह्म", "Indra": "इंद्र", "Vaidhriti": "वैधृति",
}


def time_hi(t: datetime) -> str:
    """'सुबह 7:53' / 'दोपहर 12:05' / 'शाम 5:41' / 'रात 11:40' (nearest minute)."""
    from postgen.models import round_min
    t = round_min(t)
    h = t.hour
    part = "सुबह" if 4 <= h < 12 else "दोपहर" if 12 <= h < 16 else "शाम" if 16 <= h < 19 else "रात"
    return f"{part} {t.strftime('%I:%M').lstrip('0')}"


def span_hi(a: datetime, b: datetime) -> str:
    """'सुबह 7:53 – 9:17'; repeats the part of day only when it changes."""
    ta, tb = time_hi(a), time_hi(b)
    pa, pb = ta.split()[0], tb.split()[0]
    return f"{ta} – {tb.split()[1]}" if pa == pb else f"{ta} – {tb}"


def _limb_hi(day: DayFacts, limbs: list[Limb], name_map) -> str:
    first = limbs[0]
    name = name_map(first.name)
    if len(limbs) == 1:
        return f"{name} (पूरे दिन)"
    when = time_hi(first.end)
    if first.end.date() > day.date:   # "रात 1:09" → "देर रात 1:09", "सुबह 5:10" → "अगली सुबह 5:10"
        when = _next_day_prefix(first.end, day, roman=False) + when.split(" ", 1)[1]
    return f"{name} {when} तक, फिर {name_map(limbs[1].name)}"


def panchang_rows_hi(day: DayFacts) -> list[dict]:
    """Rows for the Panchang image table. `tone`: good / avoid / plain."""
    rows = [
        {"label": "तिथि", "value": _limb_hi(day, day.tithis, tithi_hi), "tone": "plain"},
        {"label": "नक्षत्र", "value": _limb_hi(day, day.nakshatras, NAKSHATRA_HI.get), "tone": "plain"},
        {"label": "योग", "value": _limb_hi(day, day.yogas, YOGA_HI.get), "tone": "plain"},
        {"label": "सूर्योदय / सूर्यास्त", "value": f"{time_hi(day.sunrise)} / {time_hi(day.sunset)}", "tone": "plain"},
        {"label": "राहु काल", "value": span_hi(day.rahu_kaal.start, day.rahu_kaal.end), "tone": "avoid",
         "note": "शुभ कार्य टालें"},
    ]
    if abhijit_applies(day):
        rows.append({"label": "अभिजीत मुहूर्त", "value": span_hi(day.abhijit_muhurat.start, day.abhijit_muhurat.end),
                     "tone": "good", "note": "शुभ कार्य के लिए"})
    return rows


TIMING_LABEL_HI = {
    "Puja Muhurat": "पूजा मुहूर्त", "Chandrodaya": "चंद्रोदय", "Taron ko dekhne ka samay": "तारे देखने का समय",
    "Pratahkala Muhurat": "प्रातःकाल मुहूर्त", "Sandhya Arghya (suryast)": "संध्या अर्घ्य (सूर्यास्त)",
    "Purnima tithi aarambh": "पूर्णिमा तिथि आरंभ",
    "Rahu Kaal": "राहु काल", "Sunrise": "सूर्योदय", "Abhijit Muhurat": "अभिजीत मुहूर्त",
}


def timing_value_hi(value: str) -> str:
    """'5:38 PM – 6:56 PM' → 'शाम 5:38 – 6:56' (curated Drik strings)."""
    parts = [datetime.strptime(v.strip(), "%I:%M %p") for v in value.split("–")]
    return span_hi(*parts) if len(parts) == 2 else time_hi(parts[0])


def timing_rows_hi(timings: list[tuple[str, str]]) -> list[dict]:
    return [{"label": TIMING_LABEL_HI.get(label, label), "value": timing_value_hi(value),
             "tone": "avoid" if label == "Rahu Kaal" else ""} for label, value in timings]
