from datetime import datetime

from postgen import reel
from postgen.reel import ReelWriter, spoken_span, spoken_time


def test_spoken_time_words():
    assert spoken_time(datetime(2026, 11, 5, 7, 53)) == "सुबह सात बजकर तिरपन मिनट"
    assert spoken_time(datetime(2026, 11, 5, 18, 0)) == "शाम छह बजे"


def test_spoken_span_drops_repeated_part_of_day():
    assert spoken_span(datetime(2026, 11, 5, 13, 26), datetime(2026, 11, 5, 14, 48)) == \
        "दोपहर एक बजकर छब्बीस मिनट से दो बजकर अड़तालीस मिनट तक"
    assert spoken_span(datetime(2026, 11, 6, 18, 2), datetime(2026, 11, 6, 19, 57)).endswith("रात सात बजकर सत्तावन मिनट तक")


class _Base:
    def guardrail_problems(self, flat, allowed, extra):
        return []


class _Day:
    festivals, tithis = [], []


def _script(**over):
    s = {"hook": {"say": "आज रमा एकादशी है।", "show": "रमा एकादशी"},
         "meaning": {"say": "माना जाता है कि यह शुभ है।", "show": "शुभ दिन"},
         "upay": {"say": "तुलसी के पास दीपक जलाएँ।", "show": "तुलसी पर दीपक"}}
    s.update(over)
    return s


def test_script_rules():
    w = ReelWriter(_Base())
    assert w.problems(_script(), _Day(), {}) == []
    assert any("digits" in p for p in w.problems(_script(upay={"say": "शाम 6 बजे दीपक जलाएँ।", "show": "दीपक"}), _Day(), {}))
    assert any("follow" in p for p in w.problems(_script(hook={"say": "UpaySutra को फॉलो करें।", "show": "फॉलो"}), _Day(), {}))
    assert any("Devanagari" in p for p in w.problems(_script(meaning={"say": "Very auspicious day", "show": "शुभ"}), _Day(), {}))


def test_pronunciation_map_only_changes_voice_text():
    from postgen.reel import for_voice
    cfg = {"pronounce": {"UpaySutra": "उपाय सूत्र", "रमा एकादशी": "रामा एकादशी"}}
    assert for_voice("आज रमा एकादशी है, UpaySutra को फॉलो करें।", cfg) == "आज रामा एकादशी है, उपाय सूत्र को फॉलो करें।"


def test_panchang_reel_reads_each_rashi_from_the_carousel_slides():
    slides = [{"rashi": "Mesh", "symbol": "♈", "name_hi": "मेष", "band": "Good", "band_hi": "शुभ",
               "text_hi": "आज धैर्य से काम लें, दिन संतुलित रहेगा।"}]
    (ln,) = reel.rashi_lines({"rashifal_slides": slides})
    assert ln.say == "मेष राशि — शुभ। आज धैर्य से काम लें, दिन संतुलित रहेगा।"
    assert ln.show == slides[0]["text_hi"] and ln.rashi == slides[0] and ln.source == "code"
    assert reel.rashi_lines({}) == []
