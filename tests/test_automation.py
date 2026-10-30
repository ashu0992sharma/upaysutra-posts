"""Unattended publishing: timing, idempotency, pauses, staleness, Meta API calls."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from postgen import automation, notify
from postgen import meta as meta_mod

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def env(tmp_path, monkeypatch):
    out, state = tmp_path / "output", tmp_path / "state"
    monkeypatch.setattr(automation, "OUTPUT_DIR", out)
    monkeypatch.setattr(automation, "MANIFEST", state / "manifest.json")
    monkeypatch.setattr(automation, "LEDGER", state / "published.json")
    monkeypatch.setattr(automation, "PAUSES", state / "pauses.json")
    alerts = []
    monkeypatch.setattr(notify, "alert", lambda p, d="": alerts.append((p, d)))
    day = out / "2026-11-02_Mon"
    for sub, files in {"1_post_6AM": ["1_panchang.png", "2_mesh.png", "3_app.png"], "2_story_6AM": ["story.png"],
                       "3_post_8PM": ["1_kal-ka-din.png"], "4_story_8PM": ["story.png"]}.items():
        (day / sub).mkdir(parents=True)
        for f in files:
            Image.new("RGB", (10, 10)).save(day / sub / f)
        if "post" in sub:
            (day / sub / "caption_instagram.txt").write_text("IG caption (link in bio)")
            (day / sub / "caption_facebook.txt").write_text("FB caption https://play.google.com/x")
    from datetime import date
    automation.update_manifest([date(2026, 11, 2)])
    return alerts


class FakeMeta:
    def __init__(self, fail=(), existing=()):
        self.calls, self.fail, self.existing = [], set(fail), list(existing)

    def recent_posts(self, platform, around, window_min=60):
        return [e[1] for e in self.existing if e[0] == platform]

    def __getattr__(self, name):
        def call(*a):
            if name in self.fail:
                raise meta_mod.MetaError(f"{name} boom")
            self.calls.append((name, a))
            return f"{name}-id"
        return call


def at(h, m):
    return datetime(2026, 11, 2, h, m, tzinfo=IST)


def test_manifest_from_folders(env):
    items = {i["id"]: i for i in automation._load(automation.MANIFEST, [])}
    assert set(items) == {"2026-11-02_post_am", "2026-11-02_story_am", "2026-11-02_post_pm", "2026-11-02_story_pm"}
    assert items["2026-11-02_post_am"]["kind"] == "carousel" and items["2026-11-02_post_am"]["publish_at"].startswith("2026-11-02T06:00")
    assert items["2026-11-02_story_am"]["kind"] == "story" and items["2026-11-02_post_pm"]["kind"] == "image"
    assert [f.split("/")[-1] for f in items["2026-11-02_post_am"]["files"]] == ["1_panchang.png", "2_mesh.png", "3_app.png"]


def test_publishes_only_due_posts_once_with_platform_captions(env):
    m = FakeMeta()
    automation.publish_due(now=at(5, 59), client=m)
    assert m.calls == []                                       # nothing due yet
    r = automation.publish_due(now=at(6, 3), client=m)
    assert [c[0] for c in m.calls] == ["ig_carousel", "fb_carousel"]
    assert m.calls[0][1][1] == "IG caption (link in bio)" and "play.google.com" in m.calls[1][1][1]
    assert len(m.calls[0][1][0]) == 3                           # 3 slides in order
    automation.publish_due(now=at(6, 20), client=m)             # story now due; post not repeated
    assert [c[0] for c in m.calls] == ["ig_carousel", "fb_carousel", "ig_story", "fb_story"]
    assert len(r["published"]) == 2


def test_failed_platform_retried_alone_and_alerted(env):
    alerts = env
    m = FakeMeta(fail={"fb_carousel"})
    r = automation.publish_due(now=at(6, 1), client=m)
    assert r["failed"] and alerts and [c[0] for c in m.calls] == ["ig_carousel"]
    m.fail.clear()
    automation.publish_due(now=at(6, 10), client=m)
    assert [c[0] for c in m.calls] == ["ig_carousel", "fb_carousel"]   # Instagram not posted twice


def test_paused_day_is_not_published(env):
    automation.set_pause("2026-11-02")
    m = FakeMeta()
    r = automation.publish_due(now=at(20, 30), client=m)
    assert m.calls == [] and any("paused" in s for s in r["skipped"])
    automation.set_pause("2026-11-02", pause=False)
    automation.publish_due(now=at(20, 31), client=m)
    assert {c[0] for c in m.calls} == {"ig_image", "fb_image", "ig_story", "fb_story"}  # 6 AM ones are stale by now


def test_stale_posts_never_go_out(env):
    alerts = env
    m = FakeMeta()
    r = automation.publish_due(now=at(9, 0), client=m)          # 6 AM post is 3 h late
    assert m.calls == [] and any("late" in s for s in r["skipped"]) and alerts


class FakeHTTP:
    """Records Graph API requests and returns canned ids."""

    def __init__(self):
        self.reqs, self.n = [], 0

    def request(self, method, url, data=None, params=None, headers=None, timeout=None):
        self.reqs.append((method, url.split("/v25.0/")[1], data or params))
        self.n += 1
        if (params or {}).get("fields") == "status_code":
            body = {"status_code": "FINISHED"}
        elif (params or {}).get("fields") == "access_token":
            body = {"access_token": "PAGE_TOKEN"}
        else:
            body = {"id": f"id{self.n}"}
        return type("R", (), {"status_code": 200, "content": b"x", "json": lambda self_: body})()


@pytest.fixture
def client(monkeypatch):
    for k, v in {"META_ACCESS_TOKEN": "T", "META_PAGE_ID": "PAGE", "META_IG_USER_ID": "IG"}.items():
        monkeypatch.setenv(k, v)
    http = FakeHTTP()
    return meta_mod.MetaClient(session=http, sleep=lambda s: None), http


def test_instagram_carousel_flow(client):
    c, http = client
    c.ig_carousel(["https://m/1.jpg", "https://m/2.jpg"], "cap")
    posts = [(p, d) for m, p, d in http.reqs if m == "POST"]
    assert posts[0] == ("IG/media", {"image_url": "https://m/1.jpg", "is_carousel_item": "true"})
    assert posts[2][0] == "IG/media" and posts[2][1]["media_type"] == "CAROUSEL" and posts[2][1]["caption"] == "cap"
    assert posts[2][1]["children"].count(",") == 1 and posts[-1][0] == "IG/media_publish"


def test_instagram_story_and_facebook_story(client):
    c, http = client
    c.ig_story("https://m/s.jpg")
    assert ("POST", "IG/media", {"image_url": "https://m/s.jpg", "media_type": "STORIES"}) in http.reqs
    c.fb_story("https://m/s.jpg")
    assert any(p == "PAGE/photo_stories" for m, p, d in http.reqs)
    assert ("GET", "PAGE", {"fields": "access_token"}) in http.reqs     # page token derived from system token


def test_missing_credentials_fail_clearly(monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    with pytest.raises(meta_mod.MetaError, match="META_ACCESS_TOKEN"):
        meta_mod.MetaClient()


def test_dry_run_trial_remembers_without_touching_real_ledger(env):
    automation.publish_due(now=at(6, 1), dry_run=True)
    r = automation.publish_due(now=at(6, 20), dry_run=True)
    assert r["published"] == ["2026-11-02_story_am → instagram", "2026-11-02_story_am → facebook"]
    assert not automation.LEDGER.exists()


def test_email_failure_never_crashes_the_run(monkeypatch, capsys):
    import smtplib
    monkeypatch.setenv("ALERT_EMAIL_TO", "a@b.c")
    monkeypatch.setenv("SMTP_USER", "a@b.c")
    monkeypatch.setenv("SMTP_PASSWORD", "x")

    class Broken:
        def __init__(self, *a, **k):
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", Broken)
    notify.send("test", "body")          # must not raise
    assert "email not sent" in capsys.readouterr().out


def test_slot_skipped_when_something_already_posted(env):
    m = FakeMeta(existing=[("instagram", "17900000001")])
    r = automation.publish_due(now=at(6, 3), client=m)
    assert [c[0] for c in m.calls] == ["fb_carousel"]              # Instagram left alone
    assert any("already posted" in s and "17900000001" in s for s in r["skipped"])
    automation.publish_due(now=at(6, 20), client=m)                 # never retried later
    assert [c[0] for c in m.calls].count("ig_carousel") == 0
