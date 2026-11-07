"""Caption validation and assembly, with a scripted fake Anthropic client."""
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from postgen import rashifal
from postgen.captions import CaptionError, CaptionWriter
from postgen.config import location_from_env
from postgen.occasions import OccasionPicker
from postgen.panchang import PanchangSource


class FakeClient:
    """Returns the scripted drafts in order; records what was sent."""

    def __init__(self, drafts):
        self.drafts, self.sent = list(drafts), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.sent.append(kw)
        draft = self.drafts.pop(0)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None,
                               parsed_output=kw["output_format"](**draft),
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=300))


GOOD = {
    "hook": "Aaj Rama Ekadashi hai — Shri Hari ki kripa ke liye ye ek upay zaroor karein 🙏",
    "body": ["Kartik maas ki is Ekadashi ko Maa Lakshmi ke Rama naam se joda jaata hai.",
             "Maana jaata hai ki is din Vishnu ji ki pooja se man ko shanti milti hai."],
    "upay": "Shaam ko tulsi ke paas ghee ka ek diya jalayein aur 'Om Namo Bhagavate Vasudevaya' ka 11 baar jaap karein.",
    "hashtags": ["#Ekadashi", "#Vishnu", "#UpaySutra"],
    "name_hi": "रमा एकादशी",
    "image_tagline": "श्री हरि की कृपा बनी रहे",
    "upay_hi": "शाम को तुलसी के पास घी का दीपक जलाएँ",
}


@pytest.fixture(scope="module")
def rama():
    day = PanchangSource(location_from_env()).day(date(2026, 11, 5))
    picker = OccasionPicker()
    occ = picker.pick(day)
    return day, occ, next(r for r in picker.rules if r["slug"] == occ.kind)


def test_good_draft_is_assembled_with_code_facts(rama):
    day, occ, rule = rama
    client = FakeClient([GOOD])
    cap = CaptionWriter(client).write(day, occ, rule)
    assert cap.attempts == 1
    # timings come from code, never from the LLM facts
    sent = client.sent[0]["messages"][0]["content"]
    assert not re.search(r"\d{1,2}:\d{2}|\bAM\b|\bPM\b|2026", sent)
    assert "· Kartik Krishna Ekadashi\n" in cap.caption and "10:37" not in cap.caption  # no computed times
    assert cap.image_subline == "गुरुवार, 5 नवंबर 2026 · कार्तिक कृष्ण एकादशी"
    assert cap.image_headline == "रमा एकादशी"
    assert cap.hashtags[0] == "#RamaEkadashi" and len(cap.hashtags) <= 5
    # upay and timings come before the significance lines (visible before "See more")
    c = cap.caption
    assert c.index("Aaj ka upay") < c.index("📅") < c.index(GOOD["body"][0]) < c.index(cap.cta)


@pytest.mark.parametrize("field, value, reason", [
    ("body", ["Shubh muhurat neeche diya hai.", "Shubh din."], "neeche"),
    ("upay", "Ye upay karne se paise pakka aayenge.", "pakka"),
    ("body", ["Is din vrat se har rog door hota hai.", "Shubh din."], "rog"),
    ("hook", "Aaj Rama Ekadashi — warna saal bhar dukh rahega", "warna"),
    ("upay", "Shaam 6:30 baje diya jalayein.", "6:30"),
    ("body", ["Aaj Purnima bhi hai.", "Shubh din."], "purnima"),          # not today's tithi
    ("name_hi", "राम एकादसी", "एकादशी"),
    ("hook", "Aaj Rama Ekadashi hai " + "🙏" * 120, "characters"),
])
def test_bad_draft_is_rejected_then_fixed(rama, field, value, reason):
    day, occ, rule = rama
    client = FakeClient([{**GOOD, field: value}, GOOD])
    cap = CaptionWriter(client).write(day, occ, rule)
    assert cap.attempts == 2
    retry_msg = client.sent[1]["messages"][0]["content"]
    assert "rejected" in retry_msg and reason in retry_msg


def test_gives_up_after_three_attempts(rama):
    day, occ, rule = rama
    bad = {**GOOD, "upay": "100% result milega."}
    with pytest.raises(CaptionError):
        CaptionWriter(FakeClient([bad, bad, bad])).write(day, occ, rule)


@pytest.fixture
def rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(rashifal, "ROTATION_FILE", tmp_path / "rotation.json")


def panchang_draft(readings):
    return {"hook": "Aaj ka Panchang: Budhvar ko Ganesh ji ka smaran karke din shuru karein 🙏",
            "thought": "Shant man se kiya gaya har kaam shubh hota hai.",
            "hashtags": ["#AajKaPanchang", "#Panchang"], "image_tagline": "शुभ हो आपका दिन",
            "rashifal": readings}


def readings_for(rashis):
    return [{"rashi": r.roman, "line": f"{r.favourable[0]['graha']} ka saath hai, aaj dhairya se kaam lein.",
             "slide_hi": "आज धैर्य से काम लें, दिन संतुलित रहेगा।"} for r in rashis]


def test_panchang_day_template(rotation):
    day = PanchangSource(location_from_env()).day(date(2026, 10, 28))  # Wednesday
    occ = OccasionPicker().pick(day)
    rashis = [rashifal.rashi_facts(i, day.date, "Asia/Kolkata") for i in rashifal.rashis_for(day.date)]
    cap = CaptionWriter(FakeClient([panchang_draft(readings_for(rashis))])).write(day, occ, None)
    assert "⚠️ Rahu Kaal: 12:05 PM – 1:28 PM" in cap.caption
    assert "Abhijit" not in cap.caption          # not shown on Wednesdays
    assert cap.hashtags == ["#AajKaPanchang", "#Panchang"]  # occasion tag deduped
    assert cap.image_headline == "आज का पंचांग"
    assert cap.image_subline.endswith("तक)")  # Panchang subline keeps its computed time
    # rashifal: 3 rotating rashis, band labels from code, app CTA instead of the 1-in-3 mention
    assert [sl["rashi"] for sl in cap.rashifal_slides] == [r.roman for r in rashis]
    assert f"{rashis[0].symbol} {rashis[0].roman} ({rashis[0].band_roman}):" in cap.caption
    assert "Baaki rashiyon ka rashifal" in cap.caption and "Apni kundli" not in cap.caption


def test_rashifal_rotation_covers_all_12_and_is_stable(rotation):
    days = [date(2026, 10, 26 + i) for i in range(4)]
    picked = [rashifal.rashis_for(d) for d in days]
    assert sorted(i for p in picked for i in p) == list(range(12))
    assert rashifal.rashis_for(days[1]) == picked[1]   # regen keeps the same rashis


def test_rashifal_rotation_mixes_trios(rotation):
    days = [date(2026, 10, 26) + timedelta(days=i) for i in range(12)]
    picked = [rashifal.rashis_for(d) for d in days]
    for c in range(0, 12, 4):                          # each 4-day cycle still covers all 12
        assert sorted(i for p in picked[c:c + 4] for i in p) == list(range(12))
    assert len({tuple(p) for p in picked}) == 12       # trios change between cycles
    for a, b in zip(picked, picked[1:]):               # no rashi two days running
        assert not set(a) & set(b)


@pytest.mark.parametrize("mutate, reason", [
    (lambda rd: rd[0].update(line="Aaj Ketu aur Shani dono ka prabhav hai."), "isn't in its transits"),
    (lambda rd: rd[1].update(line="Aaj paise pakka aayenge."), "pakka"),
    (lambda rd: rd.reverse(), "in this order"),
    (lambda rd: rd[2].update(slide_hi="aaj accha din hai"), "Devanagari"),
    (lambda rd: rd[0].update(slide_hi="चंद्र और राहु का साथ, सूर्य से सावधान।"), "don't list planets"),
])
def test_rashifal_readings_validated(rotation, mutate, reason):
    day = PanchangSource(location_from_env()).day(date(2026, 10, 28))
    occ = OccasionPicker().pick(day)
    rashis = [rashifal.rashi_facts(i, day.date, "Asia/Kolkata") for i in rashifal.rashis_for(day.date)]
    good = readings_for(rashis)
    bad = [dict(r) for r in good]
    mutate(bad)
    client = FakeClient([panchang_draft(bad), panchang_draft(good)])
    cap = CaptionWriter(client).write(day, occ, None)
    assert cap.attempts == 2 and reason in client.sent[1]["messages"][0]["content"]


def test_cta_rotates_and_app_mention_one_in_three():
    days = [date(2026, 10, 26 + i) for i in range(6)]
    picks = [(d.toordinal() % 3, d.toordinal() % 3 == 0) for d in days]
    assert sorted(p[0] for p in picks) == [0, 0, 1, 1, 2, 2]
    assert sum(p[1] for p in picks) == 2


def test_muhurat_claim_rejected_when_none_shown(rama):
    day, occ, rule = rama  # Rama Ekadashi: no curated timings
    client = FakeClient([{**GOOD, "body": ["Shubh muhurat neeche diya hai.", "Shubh din."]}, GOOD])
    cap = CaptionWriter(client).write(day, occ, rule)
    assert cap.attempts == 2 and "muhurat" in client.sent[1]["messages"][0]["content"]


def test_curated_timings_shown_and_offered_to_llm():
    day = PanchangSource(location_from_env()).day(date(2026, 10, 29))
    picker = OccasionPicker()
    occ = picker.pick(day)
    draft = {**GOOD, "hook": "Karwa Chauth ka pavan vrat aaj hai 🌙", "name_hi": "करवा चौथ",
             "body": ["Shaam ko Karwa Mata ki puja ki jaati hai.", "Shubh din."],
             "hashtags": ["#Bhakti", "#HinduDharma"]}
    client = FakeClient([draft])
    cap = CaptionWriter(client).write(day, occ, next(r for r in picker.rules if r["slug"] == occ.kind))
    assert '"Chandrodaya"' in client.sent[0]["messages"][0]["content"]
    assert "🕉️ Puja Muhurat: 5:38 PM – 6:56 PM\n🕉️ Chandrodaya: 8:17 PM" in cap.caption
    assert cap.attempts == 1


def test_facebook_caption_gets_clickable_link(rotation):
    day = PanchangSource(location_from_env()).day(date(2026, 10, 28))
    occ = OccasionPicker().pick(day)
    rashis = [rashifal.rashi_facts(i, day.date, "Asia/Kolkata") for i in rashifal.rashis_for(day.date)]
    cap = CaptionWriter(FakeClient([panchang_draft(readings_for(rashis))])).write(day, occ, None)
    assert "(link in bio)" in cap.caption and "play.google.com" not in cap.caption
    assert "(link in bio)" not in cap.caption_facebook
    assert "play.google.com/store/apps/details?id=com.astro.upaysutra" in cap.caption_facebook
