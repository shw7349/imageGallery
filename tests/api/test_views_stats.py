"""조회수 집계와 관리자 통계(실제 Postgres)."""

import threading
import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.api.ratelimit import RateLimiter
from app.config import Settings
from app.db import create_session_factory
from app.jobs.worker import Worker
from app.main import create_app
from app.models import Post, PostStatus, PostViewDaily, ViewDedupe
from app.services import views
from tests.conftest import AdminClient, _truncate_all
from tests.factories import insert_category, insert_photo, insert_post


@pytest.fixture
def post_id(client: TestClient, engine: Engine) -> int:
    cat = insert_category(engine, "풍경")
    pid = insert_post(engine, cat, title="제주")
    insert_photo(engine, pid)
    return pid


def _view_count(engine: Engine, post_id: int) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(Post.view_count).where(Post.id == post_id)) or 0)


def _daily(engine: Engine, post_id: int) -> dict[date, int]:
    with Session(engine) as s:
        rows = s.execute(
            select(PostViewDaily.view_date, PostViewDaily.count).where(PostViewDaily.post_id == post_id)
        )
        return {d: c for d, c in rows}


def _view(client: TestClient, post_id: int, **headers: str) -> int:
    return client.post(f"/api/posts/{post_id}/views", headers=headers).status_code


# ── 집계 ────────────────────────────────────────────────


def test_same_visitor_counts_once_per_day(client: TestClient, engine: Engine, post_id: int) -> None:
    first = client.post(f"/api/posts/{post_id}/views")
    assert first.status_code == 204
    cookie = first.headers["set-cookie"].lower()
    for attr in ("vid=", "httponly", "secure", "samesite=lax", "path=/api", f"max-age={365 * 24 * 3600}"):
        assert attr in cookie
    for _ in range(9):
        res = client.post(f"/api/posts/{post_id}/views")
        assert res.status_code == 204
        assert "set-cookie" not in res.headers  # 이미 있는 쿠키는 다시 심지 않는다
    assert _view_count(engine, post_id) == 1
    assert list(_daily(engine, post_id).values()) == [1]


def test_different_visitors_count_separately(client: TestClient, engine: Engine, post_id: int) -> None:
    for _ in range(3):
        client.cookies.clear()
        assert _view(client, post_id) == 204
    assert _view_count(engine, post_id) == 3


def test_new_day_counts_again(
    client: TestClient, engine: Engine, post_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "today_in", lambda _tz, now=None: date(2026, 11, 1))
    _view(client, post_id)
    _view(client, post_id)
    monkeypatch.setattr(views, "today_in", lambda _tz, now=None: date(2026, 11, 2))
    _view(client, post_id)
    assert _view_count(engine, post_id) == 2
    assert _daily(engine, post_id) == {date(2026, 11, 1): 1, date(2026, 11, 2): 1}


@pytest.mark.parametrize(
    "ua",
    [
        "Mozilla/5.0 (compatible; Googlebot/2.1)",
        "facebookexternalhit/1.1",
        "curl/8.4.0",
        "python-requests/2.32",
        "",
    ],
)
def test_bots_are_not_counted(client: TestClient, engine: Engine, post_id: int, ua: str) -> None:
    assert _view(client, post_id, **{"User-Agent": ua}) == 204
    assert _view_count(engine, post_id) == 0


def test_invalid_vid_cookie_is_replaced(client: TestClient, engine: Engine, post_id: int) -> None:
    client.cookies.set("vid", "not-a-uuid", path="/api")
    res = client.post(f"/api/posts/{post_id}/views")
    assert res.status_code == 204 and "vid=" in res.headers["set-cookie"]
    new_vid = res.headers["set-cookie"].split("vid=")[1].split(";")[0]
    assert str(uuid.UUID(new_vid)) == new_vid


def test_non_public_post_is_404(client: TestClient, engine: Engine) -> None:
    cat = insert_category(engine, "c")
    draft = insert_post(engine, cat, status=PostStatus.DRAFT)
    insert_photo(engine, draft)
    assert _view(client, draft) == 404
    assert _view(client, 999) == 404
    assert _view(client, draft, **{"User-Agent": "Googlebot"}) == 404  # 봇이어도 같은 응답


def test_visitor_hash_is_stored_not_raw_cookie(client: TestClient, engine: Engine, post_id: int) -> None:
    res = client.post(f"/api/posts/{post_id}/views")
    vid = res.headers["set-cookie"].split("vid=")[1].split(";")[0]
    with Session(engine) as s:
        stored = s.scalar(select(ViewDedupe.visitor_hash))
    assert stored and vid not in stored and len(stored) == 64


def test_rate_limit_per_ip(settings: Settings, engine: Engine) -> None:
    app = create_app(settings.model_copy(update={"views_rate_per_minute": 3}))
    with TestClient(app, base_url="https://testserver") as c:
        cat = insert_category(engine, "c")
        pid = insert_post(engine, cat)
        insert_photo(engine, pid)
        codes = [c.post(f"/api/posts/{pid}/views").status_code for _ in range(4)]
        last = c.post(f"/api/posts/{pid}/views")
    _truncate_all(engine)
    assert codes == [204, 204, 204, 429]
    assert last.status_code == 429 and int(last.headers["retry-after"]) >= 1
    assert last.headers["content-type"] == "application/problem+json"


def test_rate_limiter_refills() -> None:
    now = [0.0]
    limiter = RateLimiter(per_minute=2, clock=lambda: now[0])
    assert limiter.check("ip") is None and limiter.check("ip") is None
    assert limiter.check("ip") == 30  # 1개 채우는 데 30초
    assert limiter.check("other") is None  # 키별 독립
    now[0] = 30.0
    assert limiter.check("ip") is None


@pytest.fixture
def factory_db(engine: Engine) -> Iterator[None]:
    yield
    _truncate_all(engine)


def test_concurrent_requests_are_counted_exactly(engine: Engine, factory_db: None) -> None:
    cat = insert_category(engine, "c")
    pid = insert_post(engine, cat)
    insert_photo(engine, pid)
    factory = create_session_factory(engine)
    day = date(2026, 11, 16)
    same = views.visitor_hash("s", "same-visitor")
    barrier = threading.Barrier(16)

    def hit(visitor: str) -> None:
        barrier.wait()
        with factory() as s:
            views.record_view(s, pid, visitor, day)

    threads = [threading.Thread(target=hit, args=(same,)) for _ in range(8)]
    threads += [threading.Thread(target=hit, args=(views.visitor_hash("s", f"v{i}"),)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _view_count(engine, pid) == 9  # 같은 방문자 8번 → 1 + 서로 다른 방문자 8
    assert _daily(engine, pid) == {day: 9}


# ── 관리자 통계 ──────────────────────────────────────────


def _seed_daily(engine: Engine, rows: list[tuple[int, date, int]]) -> None:
    with Session(engine) as s:
        s.add_all(PostViewDaily(post_id=p, view_date=d, count=c) for p, d, c in rows)
        s.commit()


def test_daily_views_fills_missing_days(admin: AdminClient, engine: Engine, post_id: int) -> None:
    _seed_daily(engine, [(post_id, date(2026, 11, 1), 5), (post_id, date(2026, 11, 3), 2)])
    res = admin.http.get("/api/admin/stats/views", params={"from": "2026-11-01", "to": "2026-11-04"})
    assert res.status_code == 200 and "x-mock" not in res.headers
    body = res.json()
    assert body["from"] == "2026-11-01" and body["to"] == "2026-11-04"
    assert [(d["date"], d["views"]) for d in body["days"]] == [
        ("2026-11-01", 5),
        ("2026-11-02", 0),
        ("2026-11-03", 2),
        ("2026-11-04", 0),
    ]
    assert body["total"] == 7


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-11-05", "to": "2026-11-01"},
        {"from": "2025-01-01", "to": "2026-11-01"},
        {"from": "2026-11-01"},
    ],
)
def test_daily_views_validation(admin: AdminClient, params: dict[str, str]) -> None:
    assert admin.http.get("/api/admin/stats/views", params=params).status_code == 422


def test_366_days_is_allowed(admin: AdminClient) -> None:
    res = admin.http.get("/api/admin/stats/views", params={"from": "2025-11-16", "to": "2026-11-16"})
    assert res.status_code == 200 and len(res.json()["days"]) == 366


def test_top_posts_window_and_order(
    admin: AdminClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = date(2026, 11, 16)
    monkeypatch.setattr(views, "today_in", lambda _tz, now=None: today)
    cat = insert_category(engine, "c")
    a, b, c = (insert_post(engine, cat, title=t) for t in ("A", "B", "C"))
    _seed_daily(
        engine,
        [
            (a, today, 3),
            (a, today - timedelta(days=6), 4),  # 7일 창 안(오늘 포함 7일)
            (b, today - timedelta(days=1), 10),
            (c, today - timedelta(days=7), 100),  # 7일 창 밖
        ],
    )
    week = admin.http.get("/api/admin/stats/top", params={"days": 7}).json()
    assert week["days"] == 7
    assert [(i["title"], i["views"]) for i in week["items"]] == [("B", 10), ("A", 7)]
    month = admin.http.get("/api/admin/stats/top", params={"days": 30}).json()
    assert [i["title"] for i in month["items"]] == ["C", "B", "A"]


def test_top_posts_limited_to_20(admin: AdminClient, engine: Engine) -> None:
    cat = insert_category(engine, "c")
    today = views.today_in("Asia/Seoul")
    _seed_daily(engine, [(insert_post(engine, cat, title=f"p{i}"), today, i + 1) for i in range(25)])
    items = admin.http.get("/api/admin/stats/top").json()["items"]
    assert len(items) == 20 and items[0]["views"] == 25


def test_stats_require_login(client: TestClient) -> None:
    assert (
        client.get("/api/admin/stats/views", params={"from": "2026-11-01", "to": "2026-11-02"}).status_code
        == 401
    )
    assert client.get("/api/admin/stats/top").status_code == 401


def test_admin_post_list_sorts_by_views(admin: AdminClient, engine: Engine, post_id: int) -> None:
    cat = insert_category(engine, "다른")
    popular = insert_post(engine, cat, title="인기", views=500)
    titles = [
        p["title"] for p in admin.http.get("/api/admin/posts", params={"sort": "views"}).json()["items"]
    ]
    assert titles[0] == "인기" and popular


# ── 정리 ────────────────────────────────────────────────


def test_purge_old_dedupe_rows(engine: Engine, post_id: int) -> None:
    today = date(2026, 11, 16)
    with Session(engine) as s:
        for days_ago in (0, 1, 29, 30, 31, 90):
            s.add(
                ViewDedupe(
                    post_id=post_id, visitor_hash=f"h{days_ago}", view_date=today - timedelta(days=days_ago)
                )
            )
        s.commit()
    with Session(engine) as s:
        assert views.purge_dedupe(s, today, retention_days=30) == 2
    with Session(engine) as s:
        kept = sorted((today - d).days for d in s.scalars(select(ViewDedupe.view_date)))
    assert kept == [0, 1, 29, 30]


def test_worker_runs_maintenance_once_per_day(engine: Engine, settings: Settings, post_id: int) -> None:
    old = views.today_in(settings.stats_timezone) - timedelta(days=40)
    with Session(engine) as s:
        s.add(ViewDedupe(post_id=post_id, visitor_hash="old", view_date=old))
        s.commit()
    worker = Worker(create_session_factory(engine), settings, lambda _pid: None, name="t")
    assert worker.maintenance(now=1000.0) is True
    assert worker.maintenance(now=1000.0 + 3600) is False  # 24시간 안에는 다시 안 함
    assert worker.maintenance(now=1000.0 + 24 * 3600) is True
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(ViewDedupe)) == 0
    _ = text  # 미사용 import 방지


# ── 게시물별 조회수 추이 (postId) ─────────────────────────


def test_daily_views_for_single_post(admin: AdminClient, engine: Engine, post_id: int) -> None:
    cat = insert_category(engine, "다른")
    other = insert_post(engine, cat, title="다른 글")
    _seed_daily(
        engine,
        [
            (post_id, date(2026, 11, 1), 5),
            (post_id, date(2026, 11, 3), 2),
            (other, date(2026, 11, 1), 100),
            (other, date(2026, 11, 2), 7),
        ],
    )
    params = {"from": "2026-11-01", "to": "2026-11-03"}

    one = admin.http.get("/api/admin/stats/views", params={**params, "postId": post_id}).json()
    assert [d["views"] for d in one["days"]] == [5, 0, 2]
    assert one["total"] == 7

    everything = admin.http.get("/api/admin/stats/views", params=params).json()
    assert [d["views"] for d in everything["days"]] == [105, 7, 2]  # postId 없으면 기존대로 전체


def test_daily_views_post_id_validation(admin: AdminClient) -> None:
    params = {"from": "2026-11-01", "to": "2026-11-03"}
    assert admin.http.get("/api/admin/stats/views", params={**params, "postId": 999}).status_code == 404
    assert admin.http.get("/api/admin/stats/views", params={**params, "postId": 0}).status_code == 422
    assert admin.http.get("/api/admin/stats/views", params={**params, "postId": "abc"}).status_code == 422


def test_daily_views_works_for_unpublished_post(admin: AdminClient, engine: Engine) -> None:
    cat = insert_category(engine, "c")
    hidden = insert_post(engine, cat, status=PostStatus.HIDDEN)
    _seed_daily(engine, [(hidden, date(2026, 11, 1), 3)])
    res = admin.http.get(
        "/api/admin/stats/views", params={"from": "2026-11-01", "to": "2026-11-01", "postId": hidden}
    )
    assert res.json()["total"] == 3  # 숨긴 글의 과거 조회수도 관리자는 볼 수 있다
