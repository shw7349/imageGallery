"""원본 다운로드와 다운로드 통계(실제 Postgres + MinIO)."""

import os
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import DownloadLog, PhotoStatus, PostStatus
from app.storage.s3 import S3Storage
from tests.conftest import AdminClient, _clean_buckets, _truncate_all
from tests.factories import insert_category, insert_photo, insert_post

KOREAN_NAME = "제주 일출 #1 (최종).jpg"


def _original(
    engine: Engine, storage: S3Storage, *, allow: bool = True, **post_kw: object
) -> tuple[int, bytes]:
    cat = insert_category(engine, f"c{os.urandom(3).hex()}")
    post = insert_post(engine, cat, allow_download=allow, **post_kw)  # type: ignore[arg-type]  # 테스트 편의 인자
    photo = insert_photo(engine, post, filename=KOREAN_NAME)
    data = os.urandom(4096)
    storage.client.put_object(
        Bucket=storage.originals_bucket, Key=f"originals/{post}/factory/original.jpg", Body=data
    )
    return photo, data


def _get(client: TestClient, photo_id: int) -> httpx.Response:
    return client.get(f"/api/photos/{photo_id}/original", follow_redirects=False)


def _logs(engine: Engine) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(func.count()).select_from(DownloadLog)) or 0)


def test_allowed_download_redirects_to_presigned_url(
    client: TestClient, engine: Engine, storage: S3Storage
) -> None:
    photo, data = _original(engine, storage)
    res = _get(client, photo)
    assert res.status_code == 302
    assert res.headers["cache-control"] == "no-store"
    assert "vid=" in res.headers["set-cookie"]
    location = res.headers["location"]
    assert "X-Amz-Signature" in location and "X-Amz-Expires=600" in location  # 10분

    s3 = httpx.get(location, timeout=10)
    assert s3.status_code == 200 and s3.content == data
    disposition = s3.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f"filename*=UTF-8''{quote(KOREAN_NAME)}" in disposition  # RFC 5987
    assert _logs(engine) == 1


def test_not_allowed_is_403(client: TestClient, engine: Engine, storage: S3Storage) -> None:
    photo, _ = _original(engine, storage, allow=False)
    res = _get(client, photo)
    assert res.status_code == 403 and res.headers["content-type"] == "application/problem+json"
    assert _logs(engine) == 0


def test_non_public_or_unready_is_404(client: TestClient, engine: Engine, storage: S3Storage) -> None:
    draft_photo, _ = _original(engine, storage, status=PostStatus.DRAFT)
    hidden_photo, _ = _original(engine, storage, status=PostStatus.HIDDEN)
    cat = insert_category(engine, "x")
    post = insert_post(engine, cat, allow_download=True)
    processing = insert_photo(engine, post, status=PhotoStatus.PROCESSING)
    for pid in (draft_photo, hidden_photo, processing, 999):
        assert _get(client, pid).status_code == 404
    assert _logs(engine) == 0


def test_hourly_limit_per_visitor(settings: Settings, engine: Engine, storage: S3Storage) -> None:
    app = create_app(settings.model_copy(update={"downloads_per_hour": 3}))
    try:
        with TestClient(app, base_url="https://testserver") as c:
            photo, _ = _original(engine, storage)
            codes = [_get(c, photo).status_code for _ in range(3)]
            blocked = _get(c, photo)
            c.cookies.clear()  # 다른 방문자
            other = _get(c, photo)
    finally:
        _truncate_all(engine)
        _clean_buckets(storage)
    assert codes == [302, 302, 302]
    assert blocked.status_code == 429 and 0 < int(blocked.headers["retry-after"]) <= 3600
    assert other.status_code == 302


def test_old_downloads_do_not_count(settings: Settings, engine: Engine, storage: S3Storage) -> None:
    app = create_app(settings.model_copy(update={"downloads_per_hour": 1}))
    try:
        with TestClient(app, base_url="https://testserver") as c:
            photo, _ = _original(engine, storage)
            assert _get(c, photo).status_code == 302
            with engine.begin() as conn:
                conn.execute(text("UPDATE download_log SET created_at = now() - interval '61 minutes'"))
            assert _get(c, photo).status_code == 302
    finally:
        _truncate_all(engine)
        _clean_buckets(storage)


# ── 관리자 통계 ──────────────────────────────────────────


def test_download_stats(admin: AdminClient, engine: Engine, storage: S3Storage) -> None:
    popular, _ = _original(engine, storage)
    rare, _ = _original(engine, storage)
    old, _ = _original(engine, storage)
    with Session(engine) as s:
        s.add_all([DownloadLog(photo_id=popular, visitor_hash=f"v{i}") for i in range(3)])
        s.add(DownloadLog(photo_id=rare, visitor_hash="v"))
        s.add(DownloadLog(photo_id=old, visitor_hash="v"))
        s.commit()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE download_log SET created_at = now() - interval '40 days' WHERE photo_id = :p"),
            {"p": old},
        )

    res = admin.http.get("/api/admin/stats/downloads", params={"days": 30})
    assert res.status_code == 200 and "x-mock" not in res.headers
    body = res.json()
    assert body["days"] == 30
    assert [(i["photoId"], i["downloads"]) for i in body["items"]] == [(popular, 3), (rare, 1)]
    assert body["items"][0]["originalFilename"] == KOREAN_NAME
    quarter = admin.http.get("/api/admin/stats/downloads", params={"days": 90}).json()
    assert {i["photoId"] for i in quarter["items"]} == {popular, rare, old}


def test_download_stats_requires_login(client: TestClient) -> None:
    assert client.get("/api/admin/stats/downloads").status_code == 401


@pytest.mark.parametrize("name", ["plain.jpg", "공백 있는 이름.ARW", 'quote"and;semi.jpg'])
def test_content_disposition_is_safe(storage: S3Storage, name: str) -> None:
    url = storage.presign_get(storage.originals_bucket, "originals/x.jpg", download_filename=name)
    disposition = httpx.URL(url).params["response-content-disposition"]
    assert disposition.startswith('attachment; filename="')
    ascii_part = disposition.split('filename="')[1].split('"')[0]
    assert ascii_part.isascii() and '"' not in ascii_part
    assert f"filename*=UTF-8''{quote(name)}" in disposition
