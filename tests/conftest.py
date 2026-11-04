import pytest


@pytest.fixture(autouse=True)
def _no_cloud_storage(monkeypatch):
    """Tests never touch R2, even when .env has real credentials."""
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_MEDIA_BUCKET"):
        monkeypatch.delenv(k, raising=False)
