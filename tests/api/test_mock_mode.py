"""프론트 개발용 목업 모드(MOCK_API=true). 아직 구현 전인 엔드포인트만 목업이다."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine

from app.config import Environment, Settings
from app.main import create_app
from tests.conftest import AdminClient, _truncate_all
from tests.factories import insert_category, insert_photo, insert_post


@pytest.fixture
def mock_client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    app = create_app(settings.model_copy(update={"mock_api": True}))
    with TestClient(app, base_url="https://testserver") as c:
        yield c
    _truncate_all(engine)


def test_mock_cannot_be_enabled_in_prod(settings: Settings) -> None:
    data = settings.model_dump()
    data.update(env=Environment.PROD, mock_api=True)
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


def test_implemented_endpoints_are_real_even_in_mock_mode(mock_client: TestClient, engine: Engine) -> None:
    cat = insert_category(engine, "풍경")
    post = insert_post(engine, cat)
    insert_photo(engine, post)
    res = mock_client.get("/api/posts")
    assert res.status_code == 200 and "x-mock" not in res.headers
    assert [p["id"] for p in res.json()["items"]] == [post]


def test_original_mock_redirects_to_preview(
    mock_client: TestClient, engine: Engine, settings: Settings
) -> None:
    cat = insert_category(engine, "풍경")
    allowed = insert_post(engine, cat, allow_download=True)
    denied = insert_post(engine, cat, allow_download=False)
    ok_photo = insert_photo(engine, allowed)
    no_photo = insert_photo(engine, denied)

    res = mock_client.get(f"/api/photos/{ok_photo}/original", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == f"{settings.cdn_base_url}/derived/{allowed}/{ok_photo}/preview.webp"
    assert mock_client.get(f"/api/photos/{no_photo}/original", follow_redirects=False).status_code == 403
    assert mock_client.get("/api/photos/999/original", follow_redirects=False).status_code == 404


def test_mock_download_stats(admin: AdminClient, mock_client: TestClient) -> None:
    mock_client.cookies = admin.http.cookies
    res = mock_client.get("/api/admin/stats/downloads")
    assert res.status_code == 200 and res.headers["x-mock"] == "true" and res.json()["items"]


def test_mock_admin_stats_still_require_login(mock_client: TestClient) -> None:
    assert mock_client.get("/api/admin/stats/downloads").status_code == 401


def test_mock_off_returns_501(client: TestClient) -> None:
    res = client.get("/api/photos/1/original", follow_redirects=False)
    assert res.status_code == 501 and "x-mock" not in res.headers
