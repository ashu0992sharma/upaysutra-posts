"""Publishing to Instagram and the Facebook Page via Meta's Graph API.

Instagram (Content Publishing API): create a media container from a public
JPEG URL, wait until it's FINISHED, then media_publish. Carousels are built
from child containers; stories use media_type=STORIES (no link stickers via API).
Facebook Page: photos are uploaded by URL; multi-photo posts attach unpublished
photos to a feed post; stories go through photo_stories.

Credentials come from the environment (GitHub Actions secrets):
  META_ACCESS_TOKEN  system-user token with pages_manage_posts, pages_read_engagement,
                     pages_show_list, instagram_basic, instagram_content_publish
  META_PAGE_ID       Facebook Page id
  META_IG_USER_ID    Instagram professional account id linked to the Page
"""
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from postgen.config import load_yaml


class MetaError(Exception):
    pass


class MetaClient:
    def __init__(self, session=None, sleep=time.sleep):
        cfg = load_yaml("settings")["automation"]
        self.base = f"https://graph.facebook.com/{cfg['graph_api_version']}"
        self.token = _env("META_ACCESS_TOKEN")
        self.page_id = _env("META_PAGE_ID")
        self.ig_id = _env("META_IG_USER_ID")
        self.http = session or requests.Session()
        self.sleep = sleep
        self._page_token: Optional[str] = None

    # ---------- plumbing ----------
    def _req(self, method: str, path: str, token: str, **params) -> dict:
        resp = self.http.request(method, f"{self.base}/{path}", data=params if method == "POST" else None,
                                 params=None if method == "POST" else params,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=60)
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "error" in body:
            err = body.get("error", {})
            raise MetaError(f"{method} {path}: {resp.status_code} {err.get('message', body)}")
        return body

    @property
    def page_token(self) -> str:
        if self._page_token is None:
            self._page_token = self._req("GET", self.page_id, self.token, fields="access_token")["access_token"]
        return self._page_token

    # ---------- Instagram ----------
    def _ig_container(self, **params) -> str:
        return self._req("POST", f"{self.ig_id}/media", self.token, **params)["id"]

    def _ig_wait(self, container: str, tries: int = 20):
        for _ in range(tries):
            status = self._req("GET", container, self.token, fields="status_code").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise MetaError(f"Instagram container {container} {status}")
            self.sleep(3)
        raise MetaError(f"Instagram container {container} not ready after {tries * 3}s")

    def _ig_publish(self, container: str) -> str:
        self._ig_wait(container)
        return self._req("POST", f"{self.ig_id}/media_publish", self.token, creation_id=container)["id"]

    def ig_image(self, url: str, caption: str) -> str:
        return self._ig_publish(self._ig_container(image_url=url, caption=caption))

    def ig_carousel(self, urls: list[str], caption: str) -> str:
        children = [self._ig_container(image_url=u, is_carousel_item="true") for u in urls]
        for c in children:
            self._ig_wait(c)
        return self._ig_publish(self._ig_container(media_type="CAROUSEL", children=",".join(children), caption=caption))

    def ig_story(self, url: str) -> str:
        return self._ig_publish(self._ig_container(image_url=url, media_type="STORIES"))

    def recent_posts(self, platform: str, around: datetime, window_min: int = 60) -> list[str]:
        """Posts already on the account within `window_min` of `around`: a manual post,
        or one from an earlier run. Returns their ids (empty = nothing there)."""
        lo, hi = around - timedelta(minutes=window_min), around + timedelta(minutes=window_min)
        if platform == "instagram":
            rows = self._req("GET", f"{self.ig_id}/media", self.token, fields="id,timestamp", limit=50).get("data", [])
            rows += self._req("GET", f"{self.ig_id}/stories", self.token, fields="id,timestamp").get("data", [])
            stamp = lambda r: r.get("timestamp")
        elif platform == "facebook":
            rows = self._req("GET", f"{self.page_id}/posts", self.page_token, fields="id,created_time", limit=50).get("data", [])
            stamp = lambda r: r.get("created_time")
        else:
            raise ValueError(platform)
        hits = []
        for r in rows:
            t = stamp(r)
            if t and lo <= datetime.fromisoformat(t.replace("+0000", "+00:00")) <= hi:
                hits.append(r["id"])
        return hits


    # ---------- Facebook Page ----------
    def _fb_photo(self, url: str, published: bool, message: str = "") -> str:
        params = {"url": url, "published": "true" if published else "false"}
        if message:
            params["message"] = message
        body = self._req("POST", f"{self.page_id}/photos", self.page_token, **params)
        return body.get("post_id") or body["id"]

    def fb_image(self, url: str, message: str) -> str:
        return self._fb_photo(url, published=True, message=message)

    def fb_carousel(self, urls: list[str], message: str) -> str:
        ids = [self._fb_photo(u, published=False) for u in urls]
        media = json.dumps([{"media_fbid": i} for i in ids])
        return self._req("POST", f"{self.page_id}/feed", self.page_token, message=message, attached_media=media)["id"]

    def fb_story(self, url: str) -> str:
        photo = self._fb_photo(url, published=False)
        body = self._req("POST", f"{self.page_id}/photo_stories", self.page_token, photo_id=photo)
        return body.get("post_id") or body.get("id", "")


def _env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise MetaError(f"{name} is not set (add it to GitHub Actions secrets / .env)")
    return v
