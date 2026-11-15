"""방문자 공개 API(실제 DB)."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, inspect

from app.config import Settings
from app.models import PhotoStatus, PostStatus
from tests.factories import insert_category, insert_photo, insert_post

POSTS = "/api/posts"


@pytest.fixture
def cat(client: TestClient, engine: Engine) -> int:
    return insert_category(engine, "풍경")


def _ids(res: object) -> list[int]:
    return [p["id"] for p in res.json()["items"]]  # type: ignore[attr-defined]  # httpx Response


@contextmanager
def count_queries(app: FastAPI) -> Iterator[list[str]]:
    statements: list[str] = []
    engine: Engine = app.state.engine

    def before(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


# ── 공개 조건 ────────────────────────────────────────────


def test_only_published_posts_with_ready_photos(client: TestClient, engine: Engine, cat: int) -> None:
    visible = insert_post(engine, cat, title="공개")
    insert_photo(engine, visible)
    draft = insert_post(engine, cat, title="작성중", status=PostStatus.DRAFT)
    insert_photo(engine, draft)
    hidden = insert_post(engine, cat, title="숨김", status=PostStatus.HIDDEN)
    insert_photo(engine, hidden)
    no_ready = insert_post(engine, cat, title="처리중")
    insert_photo(engine, no_ready, status=PhotoStatus.PROCESSING)
    insert_photo(engine, no_ready, status=PhotoStatus.FAILED)

    res = client.get(POSTS)
    assert res.status_code == 200
    assert _ids(res) == [visible]
    assert res.json()["total"] == 1
    for pid in (draft, hidden, no_ready, 999):
        assert client.get(f"{POSTS}/{pid}").status_code == 404


def test_summary_fields_and_cover_thumbnail(
    client: TestClient, engine: Engine, cat: int, settings: Settings
) -> None:
    post = insert_post(engine, cat, title="제주", views=42)
    insert_photo(engine, post, status=PhotoStatus.FAILED, sort_order=0)
    first_ready = insert_photo(engine, post, sort_order=1)
    insert_photo(engine, post, sort_order=2)

    item = client.get(POSTS).json()["items"][0]
    assert item["photoCount"] == 2  # READY만 센다
    assert item["viewCount"] == 42
    assert item["categoryName"] == "풍경"
    assert item["thumbUrl"] == f"{settings.cdn_base_url}/derived/{post}/{first_ready}/thumb.webp"


def test_cover_photo_is_used_when_ready(
    client: TestClient, engine: Engine, cat: int, settings: Settings
) -> None:
    post = insert_post(engine, cat)
    insert_photo(engine, post, sort_order=0)
    cover = insert_photo(engine, post, sort_order=1)
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE post SET cover_photo_id = %s WHERE id = %s", (cover, post))
    assert client.get(POSTS).json()["items"][0]["thumbUrl"].endswith(f"/{cover}/thumb.webp")


def test_detail(client: TestClient, engine: Engine, cat: int, settings: Settings) -> None:
    post = insert_post(engine, cat, title="상세", body="본문", allow_download=True)
    second = insert_photo(engine, post, sort_order=1)
    insert_photo(engine, post, status=PhotoStatus.FAILED, sort_order=2)
    first = insert_photo(engine, post, sort_order=0)

    res = client.get(f"{POSTS}/{post}")
    assert res.status_code == 200
    body = res.json()
    assert (body["title"], body["body"], body["categoryName"]) == ("상세", "본문", "풍경")
    assert [p["id"] for p in body["photos"]] == [first, second]
    photo = body["photos"][0]
    base = f"{settings.cdn_base_url}/derived/{post}/{first}/"
    assert photo["thumbUrl"] == base + "thumb.webp"
    assert photo["previewUrl"] == base + "preview.webp"
    assert photo["dziUrl"] == base + "photo.dzi"
    assert (photo["width"], photo["height"]) == (6000, 4000)
    assert photo["exif"]["cameraMake"] == "SONY" and photo["exif"]["iso"] == 100
    assert photo["exif"]["takenAt"].startswith("2026-11-01T06:12:30")
    assert photo["originalDownloadUrl"] == f"/api/photos/{first}/original"
    assert "originals/" not in res.text  # 원본 키 비노출


def test_download_url_null_when_not_allowed(client: TestClient, engine: Engine, cat: int) -> None:
    post = insert_post(engine, cat, allow_download=False)
    insert_photo(engine, post)
    detail = client.get(f"{POSTS}/{post}").json()
    assert detail["allowOriginalDownload"] is False
    assert detail["photos"][0]["originalDownloadUrl"] is None


# ── 목록 기능 ────────────────────────────────────────────


def test_category_filter_and_unknown_category(client: TestClient, engine: Engine, cat: int) -> None:
    other = insert_category(engine, "인물")
    a = insert_post(engine, cat)
    b = insert_post(engine, other)
    for p in (a, b):
        insert_photo(engine, p)
    assert _ids(client.get(POSTS, params={"category": other})) == [b]
    unknown = client.get(POSTS, params={"category": 999}).json()
    assert unknown["items"] == [] and unknown["total"] == 0


def test_search_title_and_body_case_insensitive(client: TestClient, engine: Engine, cat: int) -> None:
    title_hit = insert_post(engine, cat, title="Jeju 오름 일출")
    body_hit = insert_post(engine, cat, title="다른 글", body="제주 JEJU 바다")
    miss = insert_post(engine, cat, title="서울 야경")
    for p in (title_hit, body_hit, miss):
        insert_photo(engine, p)
    assert sorted(_ids(client.get(POSTS, params={"q": "jeju"}))) == sorted([title_hit, body_hit])
    assert _ids(client.get(POSTS, params={"q": "오름"})) == [title_hit]


def test_search_escapes_like_wildcards(client: TestClient, engine: Engine, cat: int) -> None:
    literal = insert_post(engine, cat, title="할인 100% 이벤트")
    other = insert_post(engine, cat, title="100 이벤트")
    for p in (literal, other):
        insert_photo(engine, p)
    assert _ids(client.get(POSTS, params={"q": "100%"})) == [literal]
    assert _ids(client.get(POSTS, params={"q": "_"})) == []


def test_sorting(client: TestClient, engine: Engine, cat: int) -> None:
    old_popular = insert_post(engine, cat, views=100, published_days_ago=10)
    new_quiet = insert_post(engine, cat, views=1, published_days_ago=1)
    for p in (old_popular, new_quiet):
        insert_photo(engine, p)
    assert _ids(client.get(POSTS)) == [new_quiet, old_popular]
    assert _ids(client.get(POSTS, params={"sort": "views"})) == [old_popular, new_quiet]


def test_pagination_edges(client: TestClient, engine: Engine, cat: int) -> None:
    ids = []
    for i in range(5):
        p = insert_post(engine, cat, published_days_ago=i)
        insert_photo(engine, p)
        ids.append(p)
    page2 = client.get(POSTS, params={"size": 2, "page": 2}).json()
    assert [p["id"] for p in page2["items"]] == ids[2:4]
    assert page2["total"] == 5
    last = client.get(POSTS, params={"size": 2, "page": 3}).json()
    assert [p["id"] for p in last["items"]] == ids[4:]
    beyond = client.get(POSTS, params={"size": 2, "page": 4}).json()
    assert beyond["items"] == [] and beyond["total"] == 5
    for bad in ({"size": 0}, {"size": 101}, {"page": 0}, {"q": ""}, {"q": "x" * 101}, {"sort": "created"}):
        assert client.get(POSTS, params=bad).status_code == 422, bad


def test_categories(client: TestClient, engine: Engine) -> None:
    insert_category(engine, "인물", 2)
    insert_category(engine, "풍경", 1)
    res = client.get("/api/categories")
    assert [c["name"] for c in res.json()] == ["풍경", "인물"]
    assert res.headers["cache-control"] == "public, max-age=60"


# ── 캐시 · 성능 ──────────────────────────────────────────


def test_cache_headers(client: TestClient, engine: Engine, cat: int) -> None:
    post = insert_post(engine, cat)
    insert_photo(engine, post)
    assert client.get(POSTS).headers["cache-control"] == "public, max-age=60"
    assert client.get(f"{POSTS}/{post}").headers["cache-control"] == "public, max-age=60"
    assert "public" not in client.get(f"{POSTS}/999").headers.get("cache-control", "")


def test_query_count_does_not_grow_with_data(
    client: TestClient, app: FastAPI, engine: Engine, cat: int
) -> None:
    def make(n_posts: int, n_photos: int) -> int:
        last = 0
        for _ in range(n_posts):
            last = insert_post(engine, cat)
            for i in range(n_photos):
                insert_photo(engine, last, sort_order=i)
        return last

    small = make(1, 1)
    with count_queries(app) as q_small_list:
        client.get(POSTS)
    with count_queries(app) as q_small_detail:
        client.get(f"{POSTS}/{small}")

    big = make(20, 8)
    with count_queries(app) as q_big_list:
        assert len(client.get(POSTS, params={"size": 50}).json()["items"]) == 21
    with count_queries(app) as q_big_detail:
        assert len(client.get(f"{POSTS}/{big}").json()["photos"]) == 8

    assert len(q_big_list) == len(q_small_list) <= 3
    assert len(q_big_detail) == len(q_small_detail) <= 3


def test_trigram_indexes_exist(engine: Engine, client: TestClient) -> None:
    names = {ix["name"] for ix in inspect(engine).get_indexes("post")}
    assert {"ix_post_title_trgm", "ix_post_body_trgm"} <= names
