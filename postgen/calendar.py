"""Curated festival/vrat calendar (config/calendar/<year>.yaml): the only
source of occasion dates."""
from datetime import date
from functools import lru_cache

import yaml

from postgen.config import CONFIG_DIR

CALENDAR_DIR = CONFIG_DIR / "calendar"


class CalendarGap(Exception):
    """A requested date isn't covered by any calendar file."""


@lru_cache(maxsize=None)
def _load_year(year: int) -> dict:
    path = CALENDAR_DIR / f"{year}.yaml"
    if not path.exists():
        return {"covers": None, "days": {}, "timings": {}}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {"covers": (data["covers"]["from"], data["covers"]["to"]), "days": data.get("days") or {},
            "timings": data.get("timings") or {}}


def festivals_on(day: date) -> list[str]:
    cal = _load_year(day.year)
    covers = cal["covers"]
    if not covers or not covers[0] <= day <= covers[1]:
        raise CalendarGap(
            f"{day} is not covered by config/calendar/{day.year}.yaml. Add that month from "
            "drikpanchang.com (New Delhi) before generating posts for it."
        )
    return list(cal["days"].get(day, []))


def all_entries(year: int) -> dict[date, list[str]]:
    return dict(_load_year(year)["days"])


def timings_on(day: date) -> list[tuple[str, str]]:
    """Curated (label, value) timings for a festival day, e.g. ("Chandrodaya", "8:17 PM")."""
    festivals_on(day)  # same coverage check
    return [tuple(t) for t in _load_year(day.year)["timings"].get(day, [])]
