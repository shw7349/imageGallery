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


# ── 제한 우회 방지 ──────────────────────────────────────


def _limited_app(settings: Settings, **limits: int) -> TestClient:
    return TestClient(create_app(settings.model_copy(update=limits)), base_url="https://testserver")


def test_clearing_cookies_does_not_bypass_ip_limit(
    settings: Settings, engine: Engine, storage: S3Storage
) -> None:
    """회귀: 쿠키 없이 요청하면 매번 새 방문자가 되어 제한 없이 내려받을 수 있었다."""
    try:
        with _limited_app(settings, downloads_per_hour=2, downloads_per_hour_per_ip=5) as c:
            photo, _ = _original(engine, storage)
            codes = []
            for _ in range(8):
                c.cookies.clear()
                codes.append(_get(c, photo).status_code)
    finally:
        _truncate_all(engine)
        _clean_buckets(storage)
    assert codes == [302] * 5 + [429] * 3
    assert _logs(engine) == 0  # truncate 확인


def test_daily_total_cap(settings: Settings, engine: Engine, storage: S3Storage) -> None:
    try:
        with _limited_app(settings, downloads_per_day_total=3) as c:
            photo, _ = _original(engine, storage)
            with Session(engine) as s:  # 다른 방문자·다른 IP의 기록 3건(그중 1건은 25시간 전)
                s.add_all(
                    DownloadLog(photo_id=photo, visitor_hash=f"v{i}", ip_hash=f"ip{i}") for i in range(3)
                )
                s.commit()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE download_log SET created_at = now() - interval '25 hours' "
                        "WHERE visitor_hash = 'v0'"
                    )
                )
            first = _get(c, photo)  # 최근 24시간 2건 → 통과(3건째)
            blocked = _get(c, photo)  # 상한 도달
    finally:
        _truncate_all(engine)
        _clean_buckets(storage)
    assert first.status_code == 302
    assert blocked.status_code == 429 and blocked.json()["code"] == "too_many_requests"


def test_ip_is_stored_hashed(client: TestClient, engine: Engine, storage: S3Storage) -> None:
    photo, _ = _original(engine, storage)
    _get(client, photo)
    with Session(engine) as s:
        ip_hash = s.scalar(select(DownloadLog.ip_hash))
    assert ip_hash and len(ip_hash) == 64 and "." not in ip_hash
