"""Pick one occasion per day from the calendar names, using config/occasions.yaml."""
import re
from typing import Optional

from postgen.config import load_yaml
from postgen.models import DayFacts, Occasion


def _pattern(terms: list[str]) -> Optional[re.Pattern]:
    if not terms:
        return None
    alts = "|".join(r"\s+".join(map(re.escape, t.lower().split())) for t in terms)
    return re.compile(rf"\b(?:{alts})\b", re.IGNORECASE)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class OccasionPicker:
    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or load_yaml("occasions")
        self.fallback = cfg["fallback"]
        self.rules = []
        for order, o in enumerate(cfg["occasions"]):
            self.rules.append({
                **o,
                "order": order,
                "_match": _pattern(o.get("match", [])),
                "_exclude": _pattern(o.get("exclude", [])),
            })

    def rule_for(self, name: str) -> Optional[dict]:
        """Highest-priority rule matching one calendar name."""
        hits = [r for r in self.rules if r["_match"] and r["_match"].search(name)
                and not (r["_exclude"] and r["_exclude"].search(name))]
        return min(hits, key=lambda r: (r["priority"], r["order"]), default=None)

    def pick(self, day: DayFacts) -> Occasion:
        found = []
        for fest in day.festivals:
            rule = self.rule_for(fest)
            if rule:
                found.append((rule, fest.strip() if rule.get("keep_name") else rule["name_en"], fest))
        found.sort(key=lambda c: (c[0]["priority"], c[0]["order"]))
        if not found:
            fb = self.fallback
            return Occasion(slug=fb["slug"], kind=fb["slug"], priority=fb["priority"], tag=fb["tag"],
                            name_en=fb["name_en"], name_hi=fb["name_hi"], source="fallback")
        rule, name, fest = found[0]
        return Occasion(
            slug=slugify(name), kind=rule["slug"], priority=rule["priority"], tag=rule["tag"],
            name_en=name, name_hi=rule["name_hi"], source=f"calendar:{fest}",
            also=[n for _, n, _ in found[1:] if n != name],
        )
