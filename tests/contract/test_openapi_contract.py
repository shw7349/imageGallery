"""API 계약 테스트.

- 계약에 있는 모든 경로가 존재하는지
- docs/openapi.json·docs/api.md가 코드와 같은지(스냅샷). 의도한 변경이면 `make openapi`로 갱신 후 커밋한다.
- 관리자 경로가 전부 보호되는지, 미구현 엔드포인트가 501 problem+json인지
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.deps import require_admin
from tests.conftest import AdminClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_api_md  # noqa: E402

CONTRACT: set[tuple[str, str]] = {
    ("POST", "/api/admin/auth/login"),
    ("POST", "/api/admin/auth/logout"),
    ("GET", "/api/admin/auth/me"),
    ("GET", "/api/admin/categories"),
    ("POST", "/api/admin/categories"),
    ("PUT", "/api/admin/categories/{category_id}"),
    ("DELETE", "/api/admin/categories/{category_id}"),
    ("GET", "/api/admin/posts"),
    ("POST", "/api/admin/posts"),
    ("GET", "/api/admin/posts/{post_id}"),
    ("PUT", "/api/admin/posts/{post_id}"),
    ("DELETE", "/api/admin/posts/{post_id}"),
    ("PATCH", "/api/admin/posts/{post_id}/status"),
    ("PUT", "/api/admin/posts/{post_id}/photos/order"),
    ("POST", "/api/admin/posts/{post_id}/photos"),
    ("POST", "/api/admin/photos/{photo_id}/parts"),
    ("GET", "/api/admin/photos/{photo_id}/parts"),
    ("POST", "/api/admin/photos/{photo_id}/complete"),
    ("GET", "/api/admin/photos/{photo_id}"),
    ("DELETE", "/api/admin/photos/{photo_id}"),
    ("POST", "/api/admin/photos/{photo_id}/retry"),
    ("GET", "/api/admin/stats/views"),
    ("GET", "/api/admin/stats/top"),
    ("GET", "/api/admin/stats/downloads"),
    ("GET", "/api/categories"),
    ("GET", "/api/posts"),
    ("GET", "/api/posts/{post_id}"),
    ("POST", "/api/posts/{post_id}/views"),
    ("GET", "/api/photos/{photo_id}/original"),
    ("GET", "/healthz"),
}

PUBLIC_ADMIN_PATHS = {("POST", "/api/admin/auth/login")}


def _operations(spec: dict[str, Any]) -> set[tuple[str, str]]:
    return {(m.upper(), path) for path, item in spec["paths"].items() for m in item}


def test_every_contract_path_exists(app: FastAPI) -> None:
    assert _operations(app.openapi()) == CONTRACT


def test_openapi_snapshot_is_up_to_date(app: FastAPI) -> None:
    committed = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    assert app.openapi() == committed, "API 계약이 바뀌었습니다. 의도한 변경이면 `make openapi` 후 커밋하세요"


def test_api_md_is_up_to_date(app: FastAPI) -> None:
    committed = (ROOT / "docs/api.md").read_text(encoding="utf-8")
    assert gen_api_md.render(app.openapi()) == committed, "`make openapi`로 docs/api.md를 갱신하세요"


def test_all_errors_documented_as_problem_json(app: FastAPI) -> None:
    spec = app.openapi()
    assert "Problem" in spec["components"]["schemas"]
    for path, item in spec["paths"].items():
        for method, op in item.items():
            for code, resp in op["responses"].items():
                if code.startswith(("4", "5")) and code != "503":
                    assert "application/problem+json" in resp.get("content", {}), (method, path, code)


def test_every_admin_route_requires_admin(app: FastAPI) -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/admin"):
            continue
        for method in route.methods:
            if (method, route.path) in PUBLIC_ADMIN_PATHS:
                continue
            calls = {d.call for d in route.dependant.dependencies}
            assert require_admin in calls, f"{method} {route.path}에 require_admin이 없습니다"


# 미구현(501) 엔드포인트: 유효한 요청을 보내면 501 problem+json
NOT_IMPLEMENTED_ADMIN: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/admin/stats/views?from=2026-11-01&to=2026-11-30", None),
    ("GET", "/api/admin/stats/top?days=7", None),
    ("GET", "/api/admin/stats/downloads?days=30", None),
]
# 인증 확인용: 구현 여부와 무관하게 로그인 없이는 401
ADMIN_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    (
        "POST",
        "/api/admin/posts/1/photos",
        {"filename": "a.jpg", "sizeBytes": 10, "contentType": "image/jpeg"},
    ),
    ("POST", "/api/admin/photos/1/parts", {"partNumbers": [1, 2]}),
    ("GET", "/api/admin/photos/1/parts", None),
    ("POST", "/api/admin/photos/1/complete", {"parts": [{"partNumber": 1, "etag": '"abc"'}]}),
    ("GET", "/api/admin/photos/1", None),
    ("DELETE", "/api/admin/photos/1", None),
    ("POST", "/api/admin/photos/1/retry", None),
    *NOT_IMPLEMENTED_ADMIN,
]
NOT_IMPLEMENTED_PUBLIC: list[tuple[str, str]] = [
    ("POST", "/api/posts/1/views"),
    ("GET", "/api/photos/1/original"),
]


@pytest.mark.parametrize(("method", "path", "body"), NOT_IMPLEMENTED_ADMIN)
def test_admin_stubs_return_501(
    admin: AdminClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    res = admin.http.request(method, path, json=body, headers=admin.headers())
    assert res.status_code == 501, res.text
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json()["code"] == "not_implemented"


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
def test_admin_endpoints_require_login(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize(("method", "path"), NOT_IMPLEMENTED_PUBLIC)
def test_public_stubs_return_501(client: TestClient, method: str, path: str) -> None:
    res = client.request(method, path, follow_redirects=False)
    assert res.status_code == 501
    assert res.json()["code"] == "not_implemented"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/admin/posts/1/photos", {"filename": "", "sizeBytes": 10, "contentType": "image/jpeg"}),
        ("/api/admin/posts/1/photos", {"filename": "a.jpg", "sizeBytes": 0, "contentType": "image/jpeg"}),
        ("/api/admin/photos/1/parts", {"partNumbers": []}),
        ("/api/admin/photos/1/parts", {"partNumbers": [1, 1]}),
        ("/api/admin/photos/1/parts", {"partNumbers": [0]}),
        ("/api/admin/photos/1/complete", {"parts": []}),
    ],
)
def test_contract_validation_runs_before_stub(admin: AdminClient, path: str, body: dict[str, Any]) -> None:
    assert admin.http.post(path, json=body, headers=admin.headers()).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/stats/top?days=5",
        "/api/admin/stats/views?from=2026-11-01",
        "/api/posts?size=101",
        "/api/posts?sort=created",
        "/api/posts?q=",
    ],
)
def test_contract_query_validation(admin: AdminClient, path: str) -> None:
    assert admin.http.get(path).status_code == 422
