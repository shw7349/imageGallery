"""관리자 전용 도메인 제한(ADMIN_HOSTS)과 검색엔진 차단 헤더."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine

from app.api.deps import require_admin_host
from app.config import Environment, Settings
from app.main import create_app
from tests.conftest import AdminCredentials, _truncate_all

ADMIN = "https://admin.example.com"
PUBLIC = "https://www.example.com"


@pytest.fixture
def restricted_app(settings: Settings, engine: Engine) -> Iterator[FastAPI]:
    app = create_app(settings.model_copy(update={"admin_hosts": ["admin.example.com"]}))
    with TestClient(app):  # lifespan 시작
        yield app
    _truncate_all(engine)


def _login(client: TestClient, creds: AdminCredentials) -> int:
    body = {"username": creds.username, "password": creds.password}
    return client.post("/api/admin/auth/login", json=body).status_code


def test_admin_api_works_on_admin_host(restricted_app: FastAPI, admin_credentials: AdminCredentials) -> None:
    client = TestClient(restricted_app, base_url=ADMIN)
    assert _login(client, admin_credentials) == 200
    assert client.get("/api/admin/auth/me").status_code == 200
    assert client.get("/api/admin/posts").status_code == 200


def test_admin_api_is_hidden_on_other_hosts(
    restricted_app: FastAPI, admin_credentials: AdminCredentials
) -> None:
    public = TestClient(restricted_app, base_url=PUBLIC)
    # 올바른 비밀번호여도 공개 도메인에서는 로그인 경로 자체가 없다
    res = public.post(
        "/api/admin/auth/login",
        json={"username": admin_credentials.username, "password": admin_credentials.password},
    )
    assert res.status_code == 404
    assert res.json()["code"] == "not_found"
    assert "set-cookie" not in res.headers
    assert public.get("/api/admin/auth/me").status_code == 404  # 401이 아니라 404(존재를 드러내지 않음)
    assert public.get("/api/admin/posts").status_code == 404
    assert public.get("/api/admin/stats/top").status_code == 404


def test_admin_session_cannot_be_used_from_public_host(
    restricted_app: FastAPI, admin_credentials: AdminCredentials
) -> None:
    admin = TestClient(restricted_app, base_url=ADMIN)
    assert _login(admin, admin_credentials) == 200
    cookie = admin.cookies.get("gallery_admin")
    public = TestClient(restricted_app, base_url=PUBLIC)
    assert public.get("/api/admin/auth/me", headers={"Cookie": f"gallery_admin={cookie}"}).status_code == 404


def test_public_api_works_on_any_host(restricted_app: FastAPI) -> None:
    for base in (ADMIN, PUBLIC):
        client = TestClient(restricted_app, base_url=base)
        assert client.get("/api/posts").status_code == 200
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("host", ["ADMIN.example.com", "admin.example.com:443"])
def test_host_match_ignores_case_and_port(restricted_app: FastAPI, host: str) -> None:
    client = TestClient(restricted_app, base_url=ADMIN)
    assert client.get("/api/admin/auth/me", headers={"Host": host}).status_code == 401  # 통과 후 로그인 필요


@pytest.mark.parametrize("host", ["evil.com", "admin.example.com.evil.com", "xadmin.example.com", ""])
def test_similar_hosts_are_rejected(restricted_app: FastAPI, host: str) -> None:
    client = TestClient(restricted_app, base_url=ADMIN)
    assert client.get("/api/admin/auth/me", headers={"Host": host}).status_code == 404


def test_no_restriction_when_unset(client: TestClient) -> None:
    assert client.get("/api/admin/auth/me").status_code == 401  # 로컬 개발: 제한 없음


def test_every_admin_route_checks_host(app: FastAPI) -> None:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/admin"):
            calls = {d.call for d in route.dependant.dependencies}
            assert require_admin_host in calls, f"{route.path}에 require_admin_host가 없습니다"


def test_prod_requires_admin_hosts(settings: Settings) -> None:
    data = settings.model_dump()
    data.update(env=Environment.PROD, admin_hosts=[])
    with pytest.raises(ValidationError):
        Settings.model_validate(data)
    data.update(admin_hosts=["admin.example.com"])
    assert Settings.model_validate(data).admin_hosts == ["admin.example.com"]


def test_admin_hosts_env_format(settings: Settings) -> None:
    data = settings.model_dump()
    data.update(admin_hosts=" Admin.Example.com , admin2.example.com:8443 ,")
    assert Settings.model_validate(data).admin_hosts == ["admin.example.com", "admin2.example.com"]


def test_admin_responses_have_noindex(client: TestClient, restricted_app: FastAPI) -> None:
    unauth = client.get("/api/admin/auth/me")  # 401
    assert unauth.headers["x-robots-tag"] == "noindex, nofollow"
    hidden = TestClient(restricted_app, base_url=PUBLIC).get("/api/admin/auth/me")  # 404
    assert hidden.headers["x-robots-tag"] == "noindex, nofollow"
    assert "x-robots-tag" not in client.get("/api/posts").headers
