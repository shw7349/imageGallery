"""프론트 개발용 목업 모드(MOCK_API=true). 응답이 계약 스키마를 통과하는지와 켜고 끄는 동작을 검사한다."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Environment, Settings
from app.main import create_app
from tests.conftest import AdminClient


@pytest.fixture
def mock_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings.model_copy(update={"mock_api": True})), base_url="https://testserver"
    ) as c:
        yield c


def test_mock_cannot_be_enabled_in_prod(settings: Settings) -> None:
    data = settings.model_dump()
    data.update(env=Environment.PROD, mock_api=True)
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


def test_public_endpoints_return_mock_data(mock_client: TestClient, settings: Settings) -> None:
    cats = mock_client.get("/api/categories")
    assert cats.status_code == 200 and cats.headers["x-mock"] == "true"
    assert [c["name"] for c in cats.json()] == ["풍경", "인물", "거리", "야경"]

    page = mock_client.get("/api/posts", params={"size": 5}).json()
    assert page["total"] == 36 and len(page["items"]) == 5
    first = page["items"][0]
    assert first["thumbUrl"].startswith(settings.cdn_base_url)
    dates = [p["publishedAt"] for p in page["items"]]
    assert dates == sorted(dates, reverse=True)

    detail = mock_client.get(f"/api/posts/{first['id']}").json()
    assert detail["photos"], "사진이 있어야 한다"
    photo = detail["photos"][0]
    assert photo["dziUrl"].endswith("/photo.dzi")
    assert photo["exif"]["cameraMake"]


def test_mock_list_filters(mock_client: TestClient) -> None:
    by_cat = mock_client.get("/api/posts", params={"category": 2, "size": 100}).json()
    assert by_cat["total"] == 9 and {p["categoryId"] for p in by_cat["items"]} == {2}

    search = mock_client.get("/api/posts", params={"q": "한강"}).json()
    assert search["total"] == 3 and all("한강" in p["title"] for p in search["items"])

    views = mock_client.get("/api/posts", params={"sort": "views", "size": 100}).json()["items"]
    counts = [p["viewCount"] for p in views]
    assert counts == sorted(counts, reverse=True)

    beyond = mock_client.get("/api/posts", params={"page": 99}).json()
    assert beyond["items"] == [] and beyond["total"] == 36


def test_mock_not_found_and_views(mock_client: TestClient) -> None:
    assert mock_client.get("/api/posts/9999").status_code == 404
    assert mock_client.post("/api/posts/1/views").status_code == 204
    assert mock_client.post("/api/posts/9999/views").status_code == 404


def test_mock_original_download(mock_client: TestClient) -> None:
    allowed = mock_client.get("/api/posts/1").json()  # 1번 게시물은 다운로드 허용
    denied = mock_client.get("/api/posts/2").json()
    url = allowed["photos"][0]["originalDownloadUrl"]
    assert url and denied["photos"][0]["originalDownloadUrl"] is None

    res = mock_client.get(url, follow_redirects=False)
    assert res.status_code == 302 and res.headers["location"].endswith("preview.webp")
    denied_res = mock_client.get(f"/api/photos/{denied['photos'][0]['id']}/original", follow_redirects=False)
    assert denied_res.status_code == 403
    assert mock_client.get("/api/photos/1/original", follow_redirects=False).status_code == 404


def test_mock_admin_stats(admin: AdminClient, mock_client: TestClient) -> None:
    mock_client.cookies = admin.http.cookies
    views = mock_client.get("/api/admin/stats/views", params={"from": "2026-11-01", "to": "2026-11-07"})
    assert views.status_code == 200 and views.headers["x-mock"] == "true"
    body = views.json()
    assert len(body["days"]) == 7 and body["total"] == sum(d["views"] for d in body["days"])
    assert (body["from"], body["to"]) == ("2026-11-01", "2026-11-07")

    too_long = mock_client.get("/api/admin/stats/views", params={"from": "2025-01-01", "to": "2026-11-07"})
    assert too_long.status_code == 422

    assert len(mock_client.get("/api/admin/stats/top", params={"days": 30}).json()["items"]) == 20
    assert mock_client.get("/api/admin/stats/downloads").json()["items"]


def test_mock_admin_stats_still_require_login(mock_client: TestClient) -> None:
    assert mock_client.get("/api/admin/stats/top").status_code == 401


def test_mock_off_returns_501(client: TestClient) -> None:
    res = client.get("/api/posts")
    assert res.status_code == 501 and "x-mock" not in res.headers
