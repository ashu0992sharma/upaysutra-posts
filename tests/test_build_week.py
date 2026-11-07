import json
from argparse import Namespace
from datetime import date, timedelta

import pytest

from postgen import automation, cli, notify

MON = date(2026, 11, 9)
DAYS = [MON + timedelta(days=i) for i in range(2)]


@pytest.fixture
def week(tmp_path, monkeypatch):
    """build-week with every paid step faked; `calls` records what would have run."""
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "_data")
    monkeypatch.setattr(cli, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(automation, "calendar_problems", lambda days: [])
    monkeypatch.setattr(automation, "update_manifest", lambda days: [])
    monkeypatch.setattr(automation, "cfg", lambda: {"am_post": "reel"})
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    calls = []

    def captions(a):
        calls.append(("caption", a.slot, a.start))
        path = tmp_path / "_data" / str(a.week) / ("captions.json" if a.slot == "morning" else "evening.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        caps = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps([c for c in caps if c["date"] != str(a.start)] + [{"date": str(a.start), "llm": {}}]))

    def render(a):
        calls.append(("render", a.date))
        for folder in cli.SLOTS.values():
            out = cli.day_folder(a.date, a.date.strftime("%A")) / folder
            out.mkdir(parents=True, exist_ok=True)
            (out / "x.png").write_bytes(b"")

    def reel(d, start):
        calls.append(("reel", d))
        out = cli.day_folder(d, d.strftime("%A")) / "1_reel_6AM"
        out.mkdir(parents=True, exist_ok=True)
        (out / "reel.mp4").write_bytes(b"")
        (out / "cover.png").write_bytes(b"")

    monkeypatch.setattr(cli, "cmd_captions", captions)
    monkeypatch.setattr(cli, "cmd_render", render)
    monkeypatch.setattr(cli, "build_reel", reel)
    return calls


def build(force=False):
    cli.cmd_build_week(Namespace(start=MON, days=len(DAYS), force=force))


def test_second_build_regenerates_nothing(week):
    build()
    assert len(week) == 2 * 2 + 2 + 2          # captions (2 slots x 2 days), renders, reels
    week.clear()
    build()
    assert week == []


def test_only_missing_pieces_are_rebuilt(week):
    build()
    week.clear()
    (cli.day_folder(DAYS[0], "Monday") / "1_reel_6AM" / "reel.mp4").unlink()
    (cli.day_folder(DAYS[1], "Tuesday") / "4_story_8PM" / "x.png").unlink()
    build()
    assert week == [("render", DAYS[1]), ("reel", DAYS[0])]


def test_new_caption_rerenders_that_day_and_its_reel(week, tmp_path):
    build()
    week.clear()
    path = tmp_path / "_data" / str(MON) / "captions.json"
    path.write_text(json.dumps([{"date": str(DAYS[0]), "error": "LLM failed"}, {"date": str(DAYS[1]), "llm": {}}]))
    build()
    assert week == [("caption", "morning", DAYS[0]), ("render", DAYS[0]), ("reel", DAYS[0])]


def test_force_rebuilds_everything(week):
    build()
    week.clear()
    build(force=True)
    assert len(week) == 8
