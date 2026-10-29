"""Sanity-check the curated calendar against computed tithis."""
import re

from postgen.calendar import all_entries
from postgen.occasions import OccasionPicker
from postgen.panchang import PanchangSource


def check_calendar(year: int, source: PanchangSource, picker: OccasionPicker) -> tuple[list[str], int]:
    """Return (problems, entries checked). An entry fails when the tithi its
    rule expects doesn't occur anywhere in that day (sunrise → next sunrise)."""
    problems, checked = [], 0
    for d, names in sorted(all_entries(year).items()):
        tithis = [t.name for t in source.day(d).tithis]
        for name in names:
            rule = picker.rule_for(name)
            want = rule and rule.get("tithi")
            if not want:
                continue
            checked += 1
            pat = re.compile(rf"\b{re.escape(want)}\b", re.IGNORECASE)
            if not any(pat.search(t) for t in tithis):
                problems.append(f"{d}  {name:<28} expects {want:<20} but day has {', '.join(tithis)}")
    return problems, checked
