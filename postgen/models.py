"""Day facts. Timings are computed (kalagana), occasion names come from the
curated calendar; nothing downstream (LLM included) may invent or alter them."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


def round_min(t: datetime) -> datetime:
    """Nearest minute, as Drik Panchang displays times (06:28:47 → 06:29)."""
    return (t + timedelta(seconds=30)).replace(second=0, microsecond=0)


def hm(t: datetime) -> str:
    return round_min(t).strftime("%I:%M %p").lstrip("0")


@dataclass
class TimeSpan:
    start: datetime
    end: datetime

    def fmt(self) -> str:
        return f"{hm(self.start)} – {hm(self.end)}"


@dataclass
class Limb:
    """One panchang element (tithi/nakshatra/yoga/karana) active during the day."""
    name: str
    start: datetime
    end: datetime


@dataclass
class DayFacts:
    date: date
    location: str
    weekday: str
    tithis: list[Limb]       # sunrise → next sunrise; [0] is the tithi at sunrise
    nakshatras: list[Limb]
    yogas: list[Limb]
    karanas: list[Limb]
    paksha: str
    sunrise: datetime
    sunset: datetime
    rahu_kaal: TimeSpan
    abhijit_muhurat: Optional[TimeSpan] = None
    moonrise: Optional[datetime] = None
    hindu_month: Optional[str] = None  # purnimanta (North Indian)
    festivals: list[str] = field(default_factory=list)  # names from config/calendar
    timings: list[tuple[str, str]] = field(default_factory=list)  # curated, from config/calendar

    @property
    def tithi(self) -> str:
        return self.tithis[0].name

    @property
    def nakshatra(self) -> str:
        return self.nakshatras[0].name

    @property
    def yoga(self) -> str:
        return self.yogas[0].name


@dataclass
class Occasion:
    slug: str            # stable slug for filenames, e.g. "rama-ekadashi"
    kind: str            # config slug, e.g. "ekadashi"
    priority: int
    tag: str             # background tag
    name_en: str         # display name, e.g. "Rama Ekadashi"
    name_hi: str
    source: str          # "calendar:<name>" | "fallback"
    also: list[str] = field(default_factory=list)  # lower-priority matches on the same day
