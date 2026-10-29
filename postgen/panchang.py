"""Daily panchang facts for a date: timings computed offline by kalagana,
occasion names from the curated calendar.

kalagana's astronomy matches Drik Panchang to within ~3 minutes (see
tests/test_kalagana_validation.py); its festival rules are not used.
"""
from datetime import date, timedelta

from kalagana import Location as KLocation
from kalagana import daily_panchang

from postgen.calendar import festivals_on, timings_on
from postgen.config import Location
from postgen.models import DayFacts, Limb, TimeSpan


def _limbs(items) -> list[Limb]:
    return [Limb(name=x.name, start=x.start, end=x.end) for x in items]


def _span(m: dict, key: str) -> TimeSpan:
    return TimeSpan(m[key].start, m[key].end)


class PanchangSource:
    def __init__(self, loc: Location):
        self.loc = loc
        self._kloc = KLocation(loc.name, loc.lat, loc.lon, loc.tz)

    def day(self, d: date) -> DayFacts:
        p = daily_panchang(d, self._kloc)
        return DayFacts(
            date=d,
            location=self.loc.name,
            weekday=p.vara_en,
            tithis=_limbs(p.tithi),
            nakshatras=_limbs(p.nakshatra),
            yogas=_limbs(p.yoga),
            karanas=_limbs(p.karana),
            paksha=p.paksha,
            hindu_month=p.masa_purnimanta,
            sunrise=p.sunrise,
            sunset=p.sunset,
            moonrise=p.moonrise,
            rahu_kaal=_span(p.muhurta, "rahu_kalam"),
            abhijit_muhurat=_span(p.muhurta, "abhijit"),
            festivals=festivals_on(d),
            timings=timings_on(d),
        )

    def range(self, start: date, days: int) -> list[DayFacts]:
        return [self.day(start + timedelta(days=i)) for i in range(days)]
