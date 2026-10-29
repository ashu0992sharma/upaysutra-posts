"""Paths, .env and YAML config loading."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CACHE_DIR = ROOT / "cache"
STATE_DIR = ROOT / "state"
OUTPUT_DIR = ROOT / "output"          # one folder per post day: output/2026-10-26_Mon/
DATA_DIR = OUTPUT_DIR / "_data"       # working files: captions.json + POSTING_PLAN.txt per week, review sheets
BACKGROUNDS_DIR = ROOT / "backgrounds"
FIXTURES_DIR = ROOT / "tests" / "fixtures"

load_dotenv(ROOT / ".env")
# Empty keys from the .env template would shadow other credential sources.
for _var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
    if os.environ.get(_var) == "":
        del os.environ[_var]


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float
    tz: str

    @property
    def key(self) -> str:
        return f"{self.lat:.4f}_{self.lon:.4f}"


def location_from_env() -> Location:
    return Location(
        name=os.getenv("LOCATION_NAME", "New Delhi"),
        lat=float(os.getenv("LOCATION_LAT", "28.6139")),
        lon=float(os.getenv("LOCATION_LON", "77.2090")),
        tz=os.getenv("LOCATION_TZ", "Asia/Kolkata"),
    )


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
