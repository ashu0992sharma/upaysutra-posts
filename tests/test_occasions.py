"""Occasion picker rules, using real Drik Panchang names."""
from datetime import date, datetime

import pytest

from postgen.models import DayFacts, Limb, TimeSpan
from postgen.occasions import OccasionPicker


def day(festivals=(), tithi="Shukla Tritiya"):
    t = datetime(2026, 11, 1, 6, 30)
    limb = [Limb(tithi, t, t)]
    return DayFacts(date=date(2026, 11, 1), location="New Delhi", weekday="Sunday", tithis=limb,
                    nakshatras=limb, yogas=limb, karanas=limb, paksha="Shukla", sunrise=t, sunset=t,
                    rahu_kaal=TimeSpan(t, t), festivals=list(festivals))


@pytest.fixture(scope="module")
def picker():
    return OccasionPicker()


def test_ordinary_day_falls_back_to_panchang(picker):
    occ = picker.pick(day())
    assert occ.slug == "aaj-ka-panchang" and occ.priority == 3 and occ.tag == "cosmic"


def test_never_derived_from_tithi(picker):
    assert picker.pick(day(tithi="Purnima")).slug == "aaj-ka-panchang"
    assert picker.pick(day(tithi="Shukla Ekadashi")).slug == "aaj-ka-panchang"


def test_ekadashi_keeps_specific_name(picker):
    occ = picker.pick(day(["Rama Ekadashi", "Govatsa Dwadashi"]))
    assert (occ.kind, occ.name_en, occ.slug, occ.tag) == ("ekadashi", "Rama Ekadashi", "rama-ekadashi", "vishnu")


def test_parana_is_not_ekadashi(picker):
    assert picker.pick(day(["Ekadashi Parana"])).slug == "aaj-ka-panchang"


def test_diwali_outranks_same_day_festivals(picker):
    occ = picker.pick(day(["Diwali", "Lakshmi Puja", "Narak Chaturdashi"]))
    assert occ.kind == "diwali" and occ.also == ["Narak Chaturdashi"]


def test_festival_beats_vrat(picker):
    occ = picker.pick(day(["Dhanteras", "Shukra Pradosh Vrat"]))
    assert occ.kind == "dhanteras" and occ.also == ["Shukra Pradosh Vrat"]


@pytest.mark.parametrize("names, kind", [
    (["Kartika Purnima", "Dev Diwali", "Guru Nanak Jayanti"], "kartik-purnima"),  # not Diwali
    (["Tula Sankranti", "Saraswati Puja"], "sankranti"),                          # not Basant Panchami
    (["Kalashtami", "Masik Krishna Janmashtami"], "kalashtami"),                  # not Janmashtami
    (["Gopashtami", "Masik Durgashtami"], "ashtami"),                             # not Durga Ashtami
    (["Sharad Purnima", "Kojagara Puja", "Ashwina Purnima Vrat"], "sharad-purnima"),
    (["Holika Dahan"], "holika-dahan"),                                           # not Holi
    (["Devutthana Ekadashi"], "dev-uthani-ekadashi"),
])
def test_real_drik_names(picker, names, kind):
    assert picker.pick(day(names)).kind == kind


def test_whole_word_match(picker):
    assert picker.pick(day(["Rakhiyan Mela"])).slug == "aaj-ka-panchang"
