"""멀티파트 업로드 (실제 MinIO). 파트 크기 5MB(테스트 설정), 파일 11MB → 파트 3개(5, 5, 1MB)."""

import os
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import JobStatus, Photo, PhotoJob
from app.storage.s3 import PartInfo, S3Storage
from tests.conftest import AdminClient

MB = 1024 * 1024
FILE_SIZE = 11 * MB
PART = 5 * MB


@pytest.fixture
def post_id(admin: AdminClient) -> int:
    cat = admin.http.post("/api/admin/categories", json={"name": "풍경"}, headers=admin.headers()).json()
    post = admin.http.post(
        "/api/admin/posts", json={"categoryId": cat["id"], "title": "t"}, headers=admin.headers()
    ).json()
    pid: int = post["id"]
    return pid


@pytest.fixture(scope="module")
def payload() -> bytes:
    return os.urandom(FILE_SIZE)


@dataclass
class Upload:
    photo_id: int
    part_count: int
    part_size: int


def _start(admin: AdminClient, post_id: int, filename: str = "DSC0001.ARW", size: int = FILE_SIZE) -> Any:
    return admin.http.post(
        f"/api/admin/posts/{post_id}/photos",
        json={"filename": filename, "sizeBytes": size, "contentType": "application/octet-stream"},
        headers=admin.headers(),
    )


def _begin(admin: AdminClient, post_id: int, **kw: Any) -> Upload:
    res = _start(admin, post_id, **kw)
    assert res.status_code == 201, res.text
    body = res.json()
    return Upload(body["photoId"], body["partCount"], body["partSize"])


def _urls(admin: AdminClient, photo_id: int, numbers: list[int]) -> dict[int, str]:
    res = admin.http.post(
        f"/api/admin/photos/{photo_id}/parts", json={"partNumbers": numbers}, headers=admin.headers()
    )
    assert res.status_code == 200, res.text
    return {u["partNumber"]: u["url"] for u in res.json()["urls"]}


def _put(url: str, data: bytes, origin: str | None = None) -> httpx.Response:
    headers = {"Origin": origin} if origin else {}
    res = httpx.put(url, content=data, headers=headers, timeout=30)
    assert res.status_code == 200, res.text
    return res


def _chunk(payload: bytes, n: int, part_size: int = PART) -> bytes:
    return payload[(n - 1) * part_size : n * part_size]


def _upload_parts(admin: AdminClient, up: Upload, payload: bytes, numbers: list[int]) -> dict[int, str]:
    urls = _urls(admin, up.photo_id, numbers)
    return {n: _put(urls[n], _chunk(payload, n)).headers["ETag"] for n in numbers}


def _complete(admin: AdminClient, photo_id: int, etags: dict[int, str]) -> Any:
    return admin.http.post(
        f"/api/admin/photos/{photo_id}/complete",
        json={"parts": [{"partNumber": n, "etag": e} for n, e in sorted(etags.items())]},
        headers=admin.headers(),
    )


def _jobs(engine: Engine, photo_id: int) -> list[PhotoJob]:
    with Session(engine) as s:
        return list(s.scalars(select(PhotoJob).where(PhotoJob.photo_id == photo_id)))


# ── 정상 흐름 ────────────────────────────────────────────


def test_full_upload(
    admin: AdminClient, post_id: int, payload: bytes, engine: Engine, storage: S3Storage
) -> None:
    up = _begin(admin, post_id)
    assert (up.part_count, up.part_size) == (3, PART)

    etags = _upload_parts(admin, up, payload, [1, 2, 3])
    res = _complete(admin, up.photo_id, etags)
    assert res.status_code == 200, res.text
    photo = res.json()
    assert photo["status"] == "UPLOADED"
    assert photo["sizeBytes"] == FILE_SIZE
    assert photo["originalFilename"] == "DSC0001.ARW"
    assert photo["contentType"] == "image/x-sony-arw"  # 확장자로 결정(클라이언트 값 무시)

    key = f"originals/{post_id}/{up.photo_id}/original.arw"
    assert storage.head_object(storage.originals_bucket, key) == FILE_SIZE

    jobs = _jobs(engine, up.photo_id)
    assert [j.status for j in jobs] == [JobStatus.PENDING]

    status = admin.http.get(f"/api/admin/photos/{up.photo_id}").json()
    assert status["status"] == "UPLOADED"


def test_photos_are_appended_in_order(admin: AdminClient, post_id: int) -> None:
    first = _begin(admin, post_id, filename="a.jpg", size=1)
    second = _begin(admin, post_id, filename="b.jpg", size=1)
    detail = admin.http.get(f"/api/admin/posts/{post_id}").json()
    assert [(p["id"], p["sortOrder"]) for p in detail["photos"]] == [
        (first.photo_id, 0),
        (second.photo_id, 1),
    ]


def test_single_small_file(admin: AdminClient, post_id: int) -> None:
    up = _begin(admin, post_id, filename="tiny.png", size=10)
    assert up.part_count == 1
    url = _urls(admin, up.photo_id, [1])[1]
    etag = _put(url, b"0123456789").headers["ETag"]
    assert _complete(admin, up.photo_id, {1: etag}).json()["status"] == "UPLOADED"


# ── 이어 올리기 ──────────────────────────────────────────


def test_resume_with_uploaded_parts(admin: AdminClient, post_id: int, payload: bytes) -> None:
    up = _begin(admin, post_id)
    first = _upload_parts(admin, up, payload, [1])  # 여기서 끊겼다고 가정

    res = admin.http.get(f"/api/admin/photos/{up.photo_id}/parts")
    assert res.status_code == 200
    body = res.json()
    assert (body["partCount"], body["partSize"]) == (3, PART)
    assert body["parts"] == [{"partNumber": 1, "etag": first[1], "sizeBytes": PART}]

    have = {p["partNumber"]: p["etag"] for p in body["parts"]}
    missing = [n for n in range(1, body["partCount"] + 1) if n not in have]
    have |= _upload_parts(admin, up, payload, missing)
    assert _complete(admin, up.photo_id, have).json()["status"] == "UPLOADED"


def test_reupload_same_part_overwrites(admin: AdminClient, post_id: int, payload: bytes) -> None:
    up = _begin(admin, post_id)
    url = _urls(admin, up.photo_id, [1])[1]
    _put(url, b"x" * PART)  # 잘못된 내용
    etags = _upload_parts(admin, up, payload, [1, 2, 3])  # 다시 올림
    assert _complete(admin, up.photo_id, etags).status_code == 200


# ── 완료 검증 ────────────────────────────────────────────


def test_wrong_etag_is_rejected(admin: AdminClient, post_id: int, payload: bytes) -> None:
    up = _begin(admin, post_id)
    etags = _upload_parts(admin, up, payload, [1, 2, 3])
    etags[2] = '"00000000000000000000000000000000"'
    res = _complete(admin, up.photo_id, etags)
    assert res.status_code == 422
    assert res.json()["mismatched_etag_part_numbers"] == [2]
    assert admin.http.get(f"/api/admin/photos/{up.photo_id}").json()["status"] == "UPLOADING"


def test_missing_part_is_rejected(admin: AdminClient, post_id: int, payload: bytes) -> None:
    up = _begin(admin, post_id)
    etags = _upload_parts(admin, up, payload, [1, 3])
    res = _complete(admin, up.photo_id, etags)
    assert res.status_code == 422
    assert res.json()["missing_part_numbers"] == [2]


def test_wrong_part_size_is_rejected(admin: AdminClient, post_id: int, payload: bytes) -> None:
    up = _begin(admin, post_id)
    urls = _urls(admin, up.photo_id, [1, 2, 3])
    etags = {1: _put(urls[1], _chunk(payload, 1)[:-1]).headers["ETag"]}  # 1바이트 모자람
    etags |= {n: _put(urls[n], _chunk(payload, n)).headers["ETag"] for n in (2, 3)}
    res = _complete(admin, up.photo_id, etags)
    assert res.status_code == 422
    assert res.json()["wrong_size_part_numbers"] == [1]


def test_complete_is_idempotent(admin: AdminClient, post_id: int, payload: bytes, engine: Engine) -> None:
    up = _begin(admin, post_id)
    etags = _upload_parts(admin, up, payload, [1, 2, 3])
    first = _complete(admin, up.photo_id, etags)
    second = _complete(admin, up.photo_id, etags)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(_jobs(engine, up.photo_id)) == 1


def test_complete_recovers_when_s3_completed_but_db_did_not(
    admin: AdminClient, post_id: int, payload: bytes, engine: Engine, storage: S3Storage
) -> None:
    """S3 완료 직후 커밋 전에 서버가 죽은 상황을 재현: S3만 완료하고 API로 다시 완료 요청."""
    up = _begin(admin, post_id)
    etags = _upload_parts(admin, up, payload, [1, 2, 3])
    with Session(engine) as s:
        photo = s.get(Photo, up.photo_id)
        assert photo is not None and photo.s3_upload_id
        parts = storage.list_parts(photo.original_key, photo.s3_upload_id)
        storage.complete_multipart_upload(photo.original_key, photo.s3_upload_id, parts)

    res = _complete(admin, up.photo_id, etags)
    assert res.status_code == 200
    assert res.json()["status"] == "UPLOADED"
    assert len(_jobs(engine, up.photo_id)) == 1


# ── 입력 검증 · 상태 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "size"),
    [("photo.gif", 10), ("noext", 10), ("photo.jpg", 1024**3 + 1)],
)
def test_start_validation(admin: AdminClient, post_id: int, filename: str, size: int) -> None:
    res = _start(admin, post_id, filename=filename, size=size)
    assert res.status_code == 422
    assert res.json()["code"] == "validation_failed"


def test_start_unknown_post_is_404(admin: AdminClient) -> None:
    assert _start(admin, 999).status_code == 404


def test_path_like_filename_is_kept_only_in_db(admin: AdminClient, post_id: int, engine: Engine) -> None:
    up = _begin(admin, post_id, filename="../../etc/passwd.jpg", size=1)
    with Session(engine) as s:
        photo = s.get(Photo, up.photo_id)
        assert photo is not None
        assert photo.original_key == f"originals/{post_id}/{up.photo_id}/original.jpg"
        assert photo.original_filename == "../../etc/passwd.jpg"


def test_part_number_out_of_range(admin: AdminClient, post_id: int) -> None:
    up = _begin(admin, post_id)
    res = admin.http.post(
        f"/api/admin/photos/{up.photo_id}/parts", json={"partNumbers": [1, 4]}, headers=admin.headers()
    )
    assert res.status_code == 422


def test_parts_endpoints_need_uploading_state(admin: AdminClient, post_id: int, payload: bytes) -> None:
    up = _begin(admin, post_id)
    _complete(admin, up.photo_id, _upload_parts(admin, up, payload, [1, 2, 3]))
    assert admin.http.get(f"/api/admin/photos/{up.photo_id}/parts").status_code == 409
    res = admin.http.post(
        f"/api/admin/photos/{up.photo_id}/parts", json={"partNumbers": [1]}, headers=admin.headers()
    )
    assert res.status_code == 409


def test_unknown_photo_is_404(admin: AdminClient) -> None:
    assert admin.http.get("/api/admin/photos/999").status_code == 404
    assert admin.http.get("/api/admin/photos/999/parts").status_code == 404
    assert _complete(admin, 999, {1: '"x"'}).status_code == 404
    assert admin.http.delete("/api/admin/photos/999", headers=admin.headers()).status_code == 404


# ── 중단 · 삭제 ──────────────────────────────────────────


def test_abort_uploading_photo(admin: AdminClient, post_id: int, payload: bytes, storage: S3Storage) -> None:
    up = _begin(admin, post_id)
    _upload_parts(admin, up, payload, [1])
    assert admin.http.delete(f"/api/admin/photos/{up.photo_id}", headers=admin.headers()).status_code == 204
    assert admin.http.get(f"/api/admin/photos/{up.photo_id}").status_code == 404
    uploads = storage.client.list_multipart_uploads(Bucket=storage.originals_bucket).get("Uploads", [])
    assert uploads == []


def test_delete_uploaded_photo_removes_objects(
    admin: AdminClient, post_id: int, payload: bytes, storage: S3Storage, engine: Engine
) -> None:
    up = _begin(admin, post_id)
    _complete(admin, up.photo_id, _upload_parts(admin, up, payload, [1, 2, 3]))
    derived_key = f"derived/{post_id}/{up.photo_id}/thumb.webp"
    storage.client.put_object(Bucket=storage.derived_bucket, Key=derived_key, Body=b"x")

    assert admin.http.delete(f"/api/admin/photos/{up.photo_id}", headers=admin.headers()).status_code == 204
    key = f"originals/{post_id}/{up.photo_id}/original.arw"
    assert storage.head_object(storage.originals_bucket, key) is None
    assert storage.head_object(storage.derived_bucket, derived_key) is None
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(PhotoJob)) == 0  # cascade


# ── S3 연동 ─────────────────────────────────────────────


def test_browser_can_read_etag_via_cors(admin: AdminClient, post_id: int, payload: bytes) -> None:
    """브라우저 JS가 응답 ETag를 읽으려면 Access-Control-Expose-Headers에 ETag가 있어야 한다."""
    up = _begin(admin, post_id)
    url = _urls(admin, up.photo_id, [1])[1]
    res = _put(url, _chunk(payload, 1), origin="http://localhost:5173")
    assert res.headers.get("access-control-allow-origin") in {"http://localhost:5173", "*"}
    exposed = {h.strip().lower() for h in res.headers.get("access-control-expose-headers", "").split(",")}
    assert "etag" in exposed


def test_storage_outage_is_503(settings: Settings, admin: AdminClient, post_id: int) -> None:
    broken = settings.model_copy(update={"s3_endpoint_url": "http://127.0.0.1:1"})
    with TestClient(create_app(broken), base_url="https://testserver") as c:
        c.cookies = admin.http.cookies
        res = c.post(
            f"/api/admin/posts/{post_id}/photos",
            json={"filename": "a.jpg", "sizeBytes": 10, "contentType": "image/jpeg"},
            headers=admin.headers(),
        )
    assert res.status_code == 503
    assert res.json()["code"] == "storage_unavailable"
    assert "127.0.0.1" not in res.text  # 내부 정보 노출 없음
    detail = admin.http.get(f"/api/admin/posts/{post_id}").json()
    assert detail["photos"] == []  # 롤백되어 레코드가 남지 않는다


def test_list_parts_paginates(storage: S3Storage, monkeypatch: pytest.MonkeyPatch) -> None:
    """list_parts는 1000개 단위 페이지를 모두 이어 붙인다(대용량 파일)."""
    pages = [
        {
            "Parts": [{"PartNumber": 1, "ETag": '"a"', "Size": 1}],
            "IsTruncated": True,
            "NextPartNumberMarker": 1,
        },
        {"Parts": [{"PartNumber": 2, "ETag": '"b"', "Size": 1}], "IsTruncated": False},
    ]
    calls: list[int] = []

    def fake_list_parts(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["PartNumberMarker"])
        return pages[len(calls) - 1]

    monkeypatch.setattr(storage.client, "list_parts", fake_list_parts)
    parts = storage.list_parts("k", "u")
    assert parts == [PartInfo(1, '"a"', 1), PartInfo(2, '"b"', 1)]
    assert calls == [0, 1]
