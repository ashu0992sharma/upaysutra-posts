"""Cloudflare R2 (S3-compatible) storage for the automated runs.

Two buckets:
- R2_MEDIA_BUCKET (PUBLIC): media/… JPEGs that Instagram/Facebook fetch when publishing
- R2_BUCKET (PRIVATE):      state/… the working state GitHub Actions needs between runs
                            (background library, ledgers, generated outputs)

Env (GitHub Actions secrets): R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_BUCKET, R2_MEDIA_BUCKET, MEDIA_BASE_URL (public URL of the media bucket).
Without them, storage is "off": local runs work as before and publishing is dry-run only.
"""
import io
import os
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

from postgen.config import ROOT, STATE_DIR

SYNCED_DIRS = ["backgrounds", "state", "output"]


def enabled() -> bool:
    return all(os.getenv(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"))


def _client():
    import boto3
    return boto3.client("s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")


def to_jpeg(png: Path) -> bytes:
    """Instagram's API only accepts JPEG."""
    buf = io.BytesIO()
    Image.open(png).convert("RGB").save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def media_url(rel: str) -> str:
    return f"{os.environ['MEDIA_BASE_URL'].rstrip('/')}/{rel}"


def upload_media(png: Path, key: str) -> str:
    """Upload a slide as a public JPEG; returns its URL."""
    _client().put_object(Bucket=os.environ["R2_MEDIA_BUCKET"], Key=key, Body=to_jpeg(png), ContentType="image/jpeg",
                         CacheControl="public, max-age=31536000")
    return media_url(key)


def upload_video(mp4: Path, key: str) -> str:
    """Upload a reel as-is (H.264/AAC MP4); returns its public URL."""
    _client().put_object(Bucket=os.environ["R2_MEDIA_BUCKET"], Key=key, Body=mp4.read_bytes(), ContentType="video/mp4",
                         CacheControl="public, max-age=31536000")
    return media_url(key)


def light_keep(key: str, today: date) -> bool:
    """What publishing, previews and pauses need: the schedule JSON files, plus the
    rendered images for yesterday to the day after tomorrow (a few MB, not the library)."""
    rel = key[len("state/"):]
    if rel.startswith("state/") and rel.count("/") == 1 and rel.endswith(".json"):   # manifest, ledgers, pauses, usage
        return True
    if rel.startswith("output/"):
        name = rel.split("/")[1] if rel.count("/") >= 1 else ""
        try:
            d = date.fromisoformat(name[:10])
        except ValueError:
            return False
        return today - timedelta(days=1) <= d <= today + timedelta(days=2)
    return False


def sync_down(light: bool = False):
    """Restore the working state from R2 before a run. light=True fetches only what publishing needs."""
    s3, bucket = _client(), os.environ["R2_BUCKET"]
    today = date.today()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="state/"):
        for obj in page.get("Contents", []):
            if light and not light_keep(obj["Key"], today):
                continue
            local = ROOT / obj["Key"][len("state/"):]
            if local.exists() and local.stat().st_size == obj["Size"] and not obj["Key"].endswith(".json"):
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(local))


def sync_up(light: bool = False):
    """Save the working state to R2 after a run (new/changed files only). light=True saves the JSON state only."""
    s3, bucket = _client(), os.environ["R2_BUCKET"]
    remote = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="state/"):
        for obj in page.get("Contents", []):
            remote[obj["Key"]] = obj["Size"]
    for d in (["state"] if light else SYNCED_DIRS):
        base = ROOT / d if d != "state" else STATE_DIR
        for f in base.rglob("*"):
            if not f.is_file() or f.name == ".DS_Store":
                continue
            if light and f.suffix != ".json":
                continue
            key = "state/" + str(f.relative_to(ROOT if d != "state" else STATE_DIR)) if d != "state" \
                else "state/state/" + str(f.relative_to(STATE_DIR))
            if remote.get(key) == f.stat().st_size and f.suffix != ".json":
                continue
            s3.upload_file(str(f), bucket, key)
