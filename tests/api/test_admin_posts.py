from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.models import PhotoStatus
from app.storage.s3 import S3Storage
from tests.conftest import AdminClient
from tests.factories import insert_photo

POSTS = "/api/admin/posts"


@pytest.fixture
def category_id(admin: AdminClient) -> int:
    res = admin.http.post("/api/admin/categories", json={"name": "풍경"}, headers=admin.headers())
    cid: int = res.json()["id"]
    return cid


def _create(admin: AdminClient, category_id: int, title: str = "제목", **extra: Any) -> dict[str, Any]:
    res = admin.http.post(
        POSTS, json={"categoryId": category_id, "title": title, **extra}, headers=admin.headers()
    )
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


# ── 생성·조회·수정·삭제 ─────────────────────────────────────


def test_create_defaults_to_draft(admin: AdminClient, category_id: int) -> None:
    post = _create(admin, category_id, body="본문")
    assert post["status"] == "DRAFT"
    assert post["categoryName"] == "풍경"
    assert post["body"] == "본문"
    assert "allowOriginalDownload" not in post
    assert post["publishedAt"] is None
    assert post["photos"] == []
    assert post["photoCount"] == 0


def test_create_with_unknown_category_is_422(admin: AdminClient) -> None:
    res = admin.http.post(POSTS, json={"categoryId": 999, "title": "t"}, headers=admin.headers())
    assert res.status_code == 422
    assert res.json()["errors"][0]["loc"] == ["body", "categoryId"]


@pytest.mark.parametrize(
    "body",
    [{}, {"categoryId": 1}, {"categoryId": 1, "title": ""}, {"categoryId": 1, "title": "x" * 201}],
)
def test_create_validation(admin: AdminClient, body: dict[str, Any]) -> None:
    assert admin.http.post(POSTS, json=body, headers=admin.headers()).status_code == 422


def test_get_and_404(admin: AdminClient, category_id: int, engine: Engine) -> None:
    post = _create(admin, category_id)
    insert_photo(engine, post["id"], sort_order=1, filename="b.jpg")
    insert_photo(engine, post["id"], sort_order=0, filename="a.jpg")
    res = admin.http.get(f"{POSTS}/{post['id']}")
    assert res.status_code == 200
    assert [p["originalFilename"] for p in res.json()["photos"]] == ["a.jpg", "b.jpg"]
    assert res.json()["photoCount"] == 2
    assert admin.http.get(f"{POSTS}/999").status_code == 404


def test_update_replaces_fields(admin: AdminClient, category_id: int) -> None:
    post = _create(admin, category_id, body="old")
    other = admin.http.post("/api/admin/categories", json={"name": "인물"}, headers=admin.headers()).json()
    res = admin.http.put(
        f"{POSTS}/{post['id']}", json={"categoryId": other["id"], "title": "새 제목"}, headers=admin.headers()
    )
    assert res.status_code == 200
    body = res.json()
    assert (body["title"], body["categoryName"], body["body"]) == ("새 제목", "인물", "")  # PUT은 전체 교체


def test_delete_removes_photos_and_s3_objects(
    admin: AdminClient, category_id: int, engine: Engine, storage: S3Storage
) -> None:
    post = _create(admin, category_id)
    photo_id = insert_photo(engine, post["id"])
    key = f"originals/{post['id']}/factory/original.jpg"
    derived = f"derived/{post['id']}/{photo_id}/thumb.webp"
    storage.client.put_object(Bucket=storage.originals_bucket, Key=key, Body=b"x")
    storage.client.put_object(Bucket=storage.derived_bucket, Key=derived, Body=b"x")

    assert admin.http.delete(f"{POSTS}/{post['id']}", headers=admin.headers()).status_code == 204
    assert admin.http.get(f"{POSTS}/{post['id']}").status_code == 404
    assert storage.head_object(storage.originals_bucket, key) is None
    assert storage.head_object(storage.derived_bucket, derived) is None


def test_delete_not_found(admin: AdminClient) -> None:
    assert admin.http.delete(f"{POSTS}/999", headers=admin.headers()).status_code == 404


# ── 목록 ────────────────────────────────────────────────


def test_list_filters_and_pagination(admin: AdminClient, category_id: int, engine: Engine) -> None:
    other = admin.http.post("/api/admin/categories", json={"name": "인물"}, headers=admin.headers()).json()
    ids = [_create(admin, category_id, title=f"p{i}")["id"] for i in range(5)]
    _create(admin, other["id"], title="other")
    insert_photo(engine, ids[0])
    admin.http.patch(f"{POSTS}/{ids[0]}/status", json={"status": "PUBLISHED"}, headers=admin.headers())

    res = admin.http.get(POSTS, params={"size": 2, "page": 1})
    assert res.json()["total"] == 6
    assert len(res.json()["items"]) == 2

    last = admin.http.get(POSTS, params={"size": 4, "page": 2}).json()
    assert len(last["items"]) == 2

    beyond = admin.http.get(POSTS, params={"size": 4, "page": 9}).json()
    assert beyond["items"] == [] and beyond["total"] == 6

    by_cat = admin.http.get(POSTS, params={"categoryId": other["id"]}).json()
    assert [p["title"] for p in by_cat["items"]] == ["other"]

    published = admin.http.get(POSTS, params={"status": "PUBLISHED"}).json()
    assert [p["id"] for p in published["items"]] == [ids[0]]
    assert published["items"][0]["photoCount"] == 1


def test_list_sorting(admin: AdminClient, category_id: int, engine: Engine) -> None:
    a = _create(admin, category_id, title="a")
    b = _create(admin, category_id, title="b")
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE post SET view_count = 10 WHERE id = %s", (a["id"],))

    created = [p["title"] for p in admin.http.get(POSTS).json()["items"]]
    assert created == ["b", "a"]  # 최신순
    views = [p["title"] for p in admin.http.get(POSTS, params={"sort": "views"}).json()["items"]]
    assert views == ["a", "b"]

    insert_photo(engine, a["id"])
    admin.http.patch(f"{POSTS}/{a['id']}/status", json={"status": "PUBLISHED"}, headers=admin.headers())
    published = [p["title"] for p in admin.http.get(POSTS, params={"sort": "published"}).json()["items"]]
    assert published == ["a", "b"]  # 공개일 없는 글은 뒤로
    assert b  # 미사용 경고 방지


@pytest.mark.parametrize(
    "params", [{"size": 101}, {"size": 0}, {"page": 0}, {"sort": "random"}, {"status": "BOGUS"}]
)
def test_list_validation(admin: AdminClient, params: dict[str, Any]) -> None:
    assert admin.http.get(POSTS, params=params).status_code == 422


# ── 상태 전환 ────────────────────────────────────────────


def test_publish_requires_ready_photo(admin: AdminClient, category_id: int, engine: Engine) -> None:
    post = _create(admin, category_id)
    insert_photo(engine, post["id"], status=PhotoStatus.PROCESSING)
    url = f"{POSTS}/{post['id']}/status"
    res = admin.http.patch(url, json={"status": "PUBLISHED"}, headers=admin.headers())
    assert res.status_code == 409
    assert res.json()["code"] == "conflict"


def test_publish_sets_published_at_and_cover_once(
    admin: AdminClient, category_id: int, engine: Engine
) -> None:
    post = _create(admin, category_id)
    insert_photo(engine, post["id"], status=PhotoStatus.FAILED, sort_order=0)
    ready_id = insert_photo(engine, post["id"], sort_order=1)
    url = f"{POSTS}/{post['id']}/status"

    first = admin.http.patch(url, json={"status": "PUBLISHED"}, headers=admin.headers()).json()
    assert first["status"] == "PUBLISHED"
    assert first["publishedAt"] is not None
    assert first["coverPhotoId"] == ready_id

    hidden = admin.http.patch(url, json={"status": "HIDDEN"}, headers=admin.headers()).json()
    assert hidden["status"] == "HIDDEN"
    again = admin.http.patch(url, json={"status": "PUBLISHED"}, headers=admin.headers()).json()
    assert again["publishedAt"] == first["publishedAt"]  # 재공개 시 최초 공개일 유지


def test_status_validation_and_404(admin: AdminClient) -> None:
    bogus = admin.http.patch(f"{POSTS}/1/status", json={"status": "BOGUS"}, headers=admin.headers())
    missing = admin.http.patch(f"{POSTS}/999/status", json={"status": "HIDDEN"}, headers=admin.headers())
    assert bogus.status_code == 422
    assert missing.status_code == 404


# ── 사진 순서 ────────────────────────────────────────────


def test_reorder_photos_sets_cover(admin: AdminClient, category_id: int, engine: Engine) -> None:
    post = _create(admin, category_id)
    p1 = insert_photo(engine, post["id"], sort_order=0)
    p2 = insert_photo(engine, post["id"], sort_order=1)
    p3 = insert_photo(engine, post["id"], sort_order=2)
    res = admin.http.put(
        f"{POSTS}/{post['id']}/photos/order", json={"photoIds": [p3, p1, p2]}, headers=admin.headers()
    )
    assert res.status_code == 200
    assert [p["id"] for p in res.json()["photos"]] == [p3, p1, p2]
    assert res.json()["coverPhotoId"] == p3


def test_reorder_rejects_partial_duplicate_or_foreign_ids(
    admin: AdminClient, category_id: int, engine: Engine
) -> None:
    post = _create(admin, category_id)
    other = _create(admin, category_id, title="other")
    p1 = insert_photo(engine, post["id"])
    p2 = insert_photo(engine, post["id"])
    foreign = insert_photo(engine, other["id"])
    url = f"{POSTS}/{post['id']}/photos/order"

    partial = admin.http.put(url, json={"photoIds": [p1]}, headers=admin.headers())
    assert partial.status_code == 422
    assert partial.json()["missing_photo_ids"] == [p2]

    assert admin.http.put(url, json={"photoIds": [p1, p1, p2]}, headers=admin.headers()).status_code == 422
    foreign_res = admin.http.put(url, json={"photoIds": [p1, p2, foreign]}, headers=admin.headers())
    assert foreign_res.status_code == 422
    assert foreign_res.json()["unknown_photo_ids"] == [foreign]


# ── 권한 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", POSTS),
        ("POST", POSTS),
        ("GET", f"{POSTS}/1"),
        ("PUT", f"{POSTS}/1"),
        ("DELETE", f"{POSTS}/1"),
        ("PATCH", f"{POSTS}/1/status"),
        ("PUT", f"{POSTS}/1/photos/order"),
    ],
)
def test_requires_login(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", POSTS), ("PUT", f"{POSTS}/1"), ("DELETE", f"{POSTS}/1"), ("PATCH", f"{POSTS}/1/status")],
)
def test_requires_csrf(admin: AdminClient, method: str, path: str) -> None:
    assert admin.http.request(method, path, json={}).status_code == 403
