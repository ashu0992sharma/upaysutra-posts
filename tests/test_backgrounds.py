"""Background library rules with a fake provider (no network)."""
import io
import json
from datetime import date

import pytest
from PIL import Image

from postgen import backgrounds as bg


def png(w, h, color=(200, 120, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class FakeProvider:
    def __init__(self, name="fake", fail=0, quota=False, size=(896, 1120)):
        self.name, self.fail, self.quota, self.size, self.calls = name, fail, quota, size, []

    def generate(self, prompt, aspect):
        self.calls.append((prompt, aspect))
        if self.fail:
            self.fail -= 1
            raise bg.ProviderError(f"{self.name} down", quota=self.quota)
        return png(*self.size)


@pytest.fixture
def lib_factory(tmp_path):
    def make(*providers):
        return bg.Library(list(providers), root=tmp_path / "bg", used_file=tmp_path / "used.json",
                          log_file=tmp_path / "log.jsonl", sleep=lambda s: None)
    return make


def test_to_post_size_crops_and_scales():
    for w, h in [(896, 1120), (1536, 2048), (2048, 2048), (512, 512)]:
        assert bg.to_post_size(png(w, h)).size == (1080, 1350)


def test_prompt_uses_festival_subject_else_tag():
    p = bg.build_prompt("lakshmi", "dhanteras")
    assert "gold coins and a gleaming brass kalash" in p and "No text" in p and "lower 40%" in p
    assert "golden lotus with gold coins" in bg.build_prompt("lakshmi", "some-unlisted-festival")
    assert bg.build_prompt("shiv", variation=0) != bg.build_prompt("shiv", variation=1)


def test_each_new_image_gets_the_next_variation(lib_factory):
    prov = FakeProvider()
    lib_factory(prov).bootstrap(["shiv"], 3, report=lambda m: None)
    prompts = [p for p, _ in prov.calls]
    assert len(set(prompts)) == 3


def test_festival_always_generates_fresh(lib_factory):
    lib = lib_factory(FakeProvider())
    c = lib.choose(date(2026, 11, 6), "lakshmi", "dhanteras", priority=1)
    assert c.status == "generated" and c.path.name.startswith("dhanteras_")
    meta = json.loads(c.path.with_suffix(".json").read_text())
    assert meta["occasion"] == "dhanteras" and meta["model"] == "fake"


def test_reuse_respects_four_week_window_and_tops_up(lib_factory):
    prov = FakeProvider()
    lib = lib_factory(prov)
    lib.bootstrap(["vishnu"], 3)
    assert len(prov.calls) == 3
    c1 = lib.choose(date(2026, 11, 5), "vishnu", "rama-ekadashi", priority=2)
    assert c1.status == "reused" and len(lib.images("vishnu")) == 3   # 3 unused: no top-up yet
    c2 = lib.choose(date(2026, 11, 20), "vishnu", "x", priority=2)
    # only 2 unused for Nov 20 (< 3) → 2 generated before picking
    assert len(lib.images("vishnu")) == 5
    assert c2.path != c1.path                       # c1 is inside the 28-day window
    assert c1.path not in lib.unused("vishnu", date(2026, 11, 20))
    assert c1.path in lib.unused("vishnu", date(2026, 12, 10))  # 35 days later: free again


def test_falls_back_to_secondary_model(lib_factory):
    primary, secondary = FakeProvider("primary", fail=99), FakeProvider("secondary")
    lib = lib_factory(primary, secondary)
    c = lib.choose(date(2026, 11, 8), "lakshmi", "diwali", priority=1)
    assert c.status == "generated" and len(primary.calls) == 3   # 3 retries, then secondary
    assert json.loads(c.path.with_suffix(".json").read_text())["model"] == "secondary"


def test_generation_down_falls_back_to_library_then_cosmic_then_gradient(lib_factory):
    ok = FakeProvider()
    lib = lib_factory(ok)
    lib.bootstrap(["cosmic"], 1)
    lib._providers = [FakeProvider(fail=99)]
    c = lib.choose(date(2026, 11, 8), "lakshmi", "diwali", priority=1)
    assert c.status == "fallback" and c.path.parent.name == "cosmic" and "generation failed" in c.note
    empty = lib_factory(FakeProvider(fail=99))
    empty.root = lib.root.parent / "empty"
    c = empty.choose(date(2026, 11, 9), "moon_new", "amavasya", priority=2)
    assert c.status == "fallback" and "plain gradient" in c.note and c.path.exists()


def test_quota_error_stops_bootstrap_and_resumes(lib_factory):
    lib = lib_factory(FakeProvider(fail=99, quota=True))
    made = lib.bootstrap(["vishnu", "shiv"], 3, report=lambda m: None)
    assert made == {"vishnu": 0} and lib.quota_hit
    lib2 = lib_factory(FakeProvider())
    lib2.root = lib.root
    assert lib2.bootstrap(["vishnu", "shiv"], 3, report=lambda m: None) == {"vishnu": 3, "shiv": 3}
    assert lib2.bootstrap(["vishnu", "shiv"], 3, report=lambda m: None) == {"vishnu": 0, "shiv": 0}


def test_reject_moves_out_of_library(lib_factory):
    lib = lib_factory(FakeProvider())
    lib.bootstrap(["shiv"], 2)
    target = lib.images("shiv")[0]
    lib.mark_used(target, date(2026, 11, 7))
    dest = lib.reject(target)
    assert dest.exists() and dest.with_suffix(".json").exists() and target not in lib.images("shiv")


def test_billing_errors_stop_generation():
    from google.genai import errors

    class Models:
        def generate_content(self, **kw):
            raise errors.ClientError(402, {"error": {"code": 402, "status": "RESOURCE_EXHAUSTED",
                                                     "message": "Your prepayment credits are depleted."}})

    prov = bg.GeminiProvider("m", "2K", client=type("C", (), {"models": Models()})())
    with pytest.raises(bg.ProviderError) as e:
        prov.generate("p", "4:5")
    assert e.value.quota and "prepaid" in str(e.value)


def test_rerender_keeps_background_and_never_regenerates(lib_factory):
    prov = FakeProvider()
    lib = lib_factory(prov)
    first = lib.choose(date(2026, 11, 8), "lakshmi", "diwali", priority=1)
    again = lib.choose(date(2026, 11, 8), "lakshmi", "diwali", priority=1)
    assert again.path == first.path and again.status == "generated" and len(prov.calls) == 1


def test_released_image_with_no_dates_can_be_picked(lib_factory):
    lib = lib_factory(FakeProvider())
    lib.bootstrap(["cosmic"], 3, report=lambda m: None)
    first = lib.choose(date(2026, 10, 26), "cosmic", "aaj-ka-panchang", 3)
    lib.release(first.path, date(2026, 10, 26))          # leaves an empty usage list
    assert lib.choose(date(2026, 10, 27), "cosmic", "aaj-ka-panchang", 3).status == "reused"


def test_morning_and_evening_posts_get_different_images(lib_factory):
    lib = lib_factory(FakeProvider())
    lib.bootstrap(["shiv"], 4, report=lambda m: None)
    am = lib.choose(date(2026, 11, 1), "shiv", "x", 2)
    pm = lib.choose(date(2026, 11, 1), "shiv", "kal-ka-din", 3, slot="evening")
    assert am.path != pm.path
    assert lib.choose(date(2026, 11, 1), "shiv", "kal-ka-din", 3, slot="evening").path == pm.path   # re-render keeps it


class FakeQA:
    """Rejects the first `bad` images it sees."""

    def __init__(self, bad=0):
        self.bad, self.seen = bad, []

    def check(self, img, subject):
        from postgen.imageqa import QAVerdict
        self.seen.append(subject)
        if self.bad:
            self.bad -= 1
            return QAVerdict(ok=False, blockers=["text on the wall"], notes=[])
        return QAVerdict(ok=True, blockers=[], notes=[])


def test_auto_review_rejects_and_regenerates(lib_factory):
    prov, qa = FakeProvider(), FakeQA(bad=1)
    lib = lib_factory(prov)
    lib.qa = qa
    path = lib.generate("lakshmi", "dhanteras")
    assert len(prov.calls) == 2 and path.exists()
    assert "gold coins and a gleaming brass kalash" in qa.seen[0]          # reviewed against its subject
    rejected = list((lib.root / "_rejected").glob("*.json"))
    assert len(rejected) == 1 and "text on the wall" in rejected[0].read_text()


def test_auto_review_gives_up_and_falls_back(lib_factory):
    lib = lib_factory(FakeProvider())
    lib.bootstrap(["cosmic"], 1, report=lambda m: None)
    lib.qa = FakeQA(bad=99)
    c = lib.choose(date(2026, 11, 8), "lakshmi", "diwali", priority=1)
    assert c.status == "fallback" and "auto-review" in c.note
