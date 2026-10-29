"""Evening 'Kal ka din' post: previews tomorrow, same guardrails, code-built timings."""
from datetime import date

import pytest

from postgen.captions import CaptionWriter
from postgen.config import location_from_env
from postgen.evening import EveningWriter
from postgen.occasions import OccasionPicker
from postgen.panchang import PanchangSource
from tests.test_captions import FakeClient

KARWA = {"hook": "Kal Karwa Chauth hai — aaj raat se hi taiyari shuru karein 🌙",
         "title_hi": "कल है करवा चौथ",
         "points_hi": ["पूजा की थाली और करवा तैयार रखें", "सरगी की तैयारी आज रात कर लें"],
         "lines": ["Kal suhagin striyan Karwa Mata ki puja karengi.", "Aaj raat puja ka saman ek jagah rakh lein."],
         "upay": "Kal shaam Karwa Mata ke samne ghee ka diya jalayein.",
         "hashtags": ["#Bhakti", "#HinduDharma"]}


@pytest.fixture(scope="module")
def eve():
    src, picker = PanchangSource(location_from_env()), OccasionPicker()
    today, tomorrow = src.day(date(2026, 10, 28)), src.day(date(2026, 10, 29))
    occ = picker.pick(tomorrow)
    return today, tomorrow, occ, next(r for r in picker.rules if r["slug"] == occ.kind)


def test_previews_tomorrow_with_curated_timings(eve):
    today, tomorrow, occ, rule = eve
    client = FakeClient([KARWA])
    ev = EveningWriter(CaptionWriter(client)).write(today, tomorrow, occ, rule)
    sent = client.sent[0]
    assert "EVENING post" in sent["system"] and '"tomorrow_occasion": "Karwa Chauth"' in sent["messages"][0]["content"]
    assert "📅 Kal: Guruvar, 29 Oct 2026 · Kartik Krishna Chaturthi" in ev.caption
    assert "🕉️ Chandrodaya: 8:17 PM" in ev.caption and ev.for_date == "2026-10-29"
    assert ev.hashtags[0] == "#KarwaChauth" and "Kal ka upay" in ev.caption


@pytest.mark.parametrize("field, value, reason", [
    ("title_hi", "कल का दिन", "करवा चौथ"),                     # must name tomorrow's festival
    ("upay", "Kal 6:30 baje diya jalayein.", "6:30"),           # no AI-written times
    ("lines", ["Kal vrat se pakka fal milega.", "Shubh din."], "pakka"),
    ("points_hi", ["सिर्फ़ एक"], "2–3 items"),
])
def test_bad_evening_draft_rejected(eve, field, value, reason):
    today, tomorrow, occ, rule = eve
    client = FakeClient([{**KARWA, field: value}, KARWA])
    ev = EveningWriter(CaptionWriter(client)).write(today, tomorrow, occ, rule)
    assert ev.attempts == 2 and reason in client.sent[1]["messages"][0]["content"]


def test_ordinary_tomorrow_uses_weekday_deity_and_rahu_kaal():
    src, picker = PanchangSource(location_from_env()), OccasionPicker()
    today, tomorrow = src.day(date(2026, 10, 27)), src.day(date(2026, 10, 28))   # Wednesday, no festival
    occ = picker.pick(tomorrow)
    draft = {**KARWA, "hook": "Kal Budhvar hai — Ganesh ji ke smaran se din shuru karein 🙏",
             "title_hi": "कल बुधवार: गणेश जी का दिन", "points_hi": ["सुबह गणेश जी को दूर्वा अर्पित करें", "दिन की शुरुआत शांत मन से करें"],
             "lines": ["Budhvar Ganesh ji ka din maana jaata hai.", "Aaj raat kal ke kaam ki list bana lein."],
             "upay": "Kal subah Ganesh ji ko durva arpit karein."}
    client = FakeClient([draft])
    ev = EveningWriter(CaptionWriter(client)).write(today, tomorrow, occ, None)
    assert '"weekday_deity": "Ganesh ji"' in client.sent[0]["messages"][0]["content"]
    assert [t[0] for t in ev.times] == ["Rahu Kaal", "Sunrise"]
