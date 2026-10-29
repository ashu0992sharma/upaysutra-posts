from datetime import date

import pytest

from postgen import checks
from postgen.calendar import CalendarGap, festivals_on
from postgen.config import location_from_env
from postgen.occasions import OccasionPicker
from postgen.panchang import PanchangSource


@pytest.fixture(scope="module")
def source():
    return PanchangSource(location_from_env())


def test_calendar_2026_consistent_with_computed_tithis(source):
    problems, checked = checks.check_calendar(2026, source, OccasionPicker())
    assert checked > 40 and problems == []


def test_check_catches_shifted_date(source, monkeypatch):
    # Rama Ekadashi moved one day late (onto Dhanteras' Trayodashi) must be flagged.
    monkeypatch.setattr(checks, "all_entries", lambda year: {date(2026, 11, 6): ["Rama Ekadashi"]})
    problems, _ = checks.check_calendar(2026, source, OccasionPicker())
    assert len(problems) == 1 and "Rama Ekadashi" in problems[0]


def test_uncovered_date_fails_loudly():
    with pytest.raises(CalendarGap):
        festivals_on(date(2027, 1, 5))
    with pytest.raises(CalendarGap):
        festivals_on(date(2026, 9, 30))


def test_day_facts_from_kalagana(source):
    d = source.day(date(2026, 10, 25))
    assert d.festivals[0] == "Sharad Purnima"
    assert d.tithi == "Shukla Chaturdashi" and d.nakshatra == "Revati" and d.yoga == "Harshana"
    # Drik Panchang, New Delhi 2026-10-25: sunrise 06:28, Rahu kaal 16:18–17:42, Abhijit 11:42–12:27
    assert d.sunrise.strftime("%H:%M") == "06:28"
    assert d.abhijit_muhurat.start.strftime("%H:%M") == "11:42"
    assert abs(d.rahu_kaal.start.hour * 60 + d.rahu_kaal.start.minute - (16 * 60 + 18)) <= 1


def test_after_midnight_times_are_unambiguous(source):
    from postgen import names
    d = source.day(date(2026, 10, 28))           # Tritiya ends 1:09 AM on the 29th
    assert names.panchang_rows_hi(d)[0]["value"].startswith("कृष्ण तृतीया देर रात 1:09 तक")
    assert "der raat 1:09 AM tak" in names.panchang_rows(d)[0][1]
    assert "कल" not in str(names.panchang_rows_hi(d))
