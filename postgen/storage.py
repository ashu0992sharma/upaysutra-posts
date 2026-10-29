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
from pathlib import Path

from PIL import Image

from postgen.config import ROOT

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


def sync_down():
    """Restore the working state from R2 before a run."""
    s3, bucket = _client(), os.environ["R2_BUCKET"]
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="state/"):
        for obj in page.get("Contents", []):
            local = ROOT / obj["Key"][len("state/"):]
            if local.exists() and local.stat().st_size == obj["Size"] and not obj["Key"].endswith(".json"):
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(local))


def sync_up():
    """Save the working state to R2 after a run (new/changed files only)."""
    s3, bucket = _client(), os.environ["R2_BUCKET"]
    remote = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="state/"):
        for obj in page.get("Contents", []):
            remote[obj["Key"]] = obj["Size"]
    for d in SYNCED_DIRS:
        for f in (ROOT / d).rglob("*"):
            if not f.is_file() or f.name == ".DS_Store":
                continue
            key = "state/" + str(f.relative_to(ROOT))
            if remote.get(key) == f.stat().st_size and f.suffix != ".json":
                continue
            s3.upload_file(str(f), bucket, key)
