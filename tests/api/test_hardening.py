"""운영 전 하드닝: 500 응답 형식, 요청 본문 크기 제한, 운영 설정 검증."""

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Environment, Settings
from app.main import create_app
from tests.conftest import AdminClient

MB = 1024 * 1024


# ── 처리되지 않은 예외 ────────────────────────────────────


def test_unhandled_exception_is_problem_json_without_details(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    logging.getLogger().addHandler(caplog.handler)  # create_app이 루트 핸들러를 교체하므로 다시 붙인다
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        res = c.get("/boom", headers={"X-Request-ID": "trace-500"})
    assert res.status_code == 500
    assert res.headers["x-request-id"] == "trace-500"
    assert res.headers["content-type"] == "application/problem+json"
    body = res.json()
    assert body["code"] == "internal_error" and body["instance"] == "/boom"
    assert "secret internal detail" not in res.text  # 내부 정보는 응답에 없다
    errors = [r for r in caplog.records if r.message == "unhandled error"]
    assert errors and errors[0].exc_info  # 로그에는 원인이 남고
    assert getattr(errors[0], "request_id", None) == "trace-500"  # 요청 ID로 추적할 수 있다


# ── 요청 본문 크기 ────────────────────────────────────────


def test_large_body_rejected_by_content_length(admin: AdminClient) -> None:
    res = admin.http.post(
        "/api/admin/categories",
        content=json.dumps({"name": "x" * (2 * MB)}),
        headers={**admin.headers(), "content-type": "application/json"},
    )
    assert res.status_code == 413
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json()["code"] == "payload_too_large"


def test_large_chunked_body_rejected_while_streaming(admin: AdminClient) -> None:
    sent = 0

    def chunks() -> Iterator[bytes]:
        nonlocal sent
        yield b'{"name": "'
        for _ in range(64):  # Content-Length 없이 64MB를 흘려보내려 한다
            sent += 1
            yield b"x" * MB
        yield b'"}'

    res = admin.http.post(
        "/api/admin/categories",
        content=chunks(),
        headers={**admin.headers(), "content-type": "application/json"},
    )
    assert res.status_code == 413
    assert res.json()["code"] == "payload_too_large"


def test_lying_content_length_is_still_limited(client: TestClient) -> None:
    # Content-Length는 작게, 실제 본문은 크게 보내는 경우는 서버(uvicorn)가 선언한 길이까지만 읽는다.
    res = client.post(
        "/api/admin/auth/login",
        content=b'{"username": "a", "password": "b"}',
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 401  # 정상 크기 요청은 영향 없음


def test_largest_legitimate_request_fits(admin: AdminClient, settings: Settings) -> None:
    """파트 1만 개(S3 최대)의 업로드 완료 요청이 한도 안에 들어가야 한다."""
    parts = [{"partNumber": n, "etag": '"' + "a" * 32 + '-1"'} for n in range(1, 10_001)]
    body = json.dumps({"parts": parts})
    assert len(body) < settings.max_request_body_bytes
    res = admin.http.post(
        "/api/admin/photos/999/complete",
        content=body,
        headers={**admin.headers(), "content-type": "application/json"},
    )
    assert res.status_code == 404  # 본문은 통과하고 사진이 없어서 404


# ── 운영 설정 ────────────────────────────────────────────


def _prod(settings: Settings, **over: object) -> dict[str, object]:
    data = settings.model_dump()
    data.update(env=Environment.PROD, admin_hosts=["admin.example.com"], **over)
    return data


@pytest.mark.parametrize(
    "secret",
    ["change-me-local-dev-only-change-me-local-dev-only", "CHANGE-ME-" + "x" * 40, "a" * 48, "abcabcabc" * 6],
)
def test_prod_rejects_placeholder_or_trivial_secret(settings: Settings, secret: str) -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings.model_validate(_prod(settings, session_secret=secret))


def test_prod_accepts_random_secret(settings: Settings) -> None:
    assert Settings.model_validate(_prod(settings)).env is Environment.PROD


def test_local_allows_example_secret(settings: Settings) -> None:
    data = settings.model_dump()
    data.update(session_secret="change-me-local-dev-only-change-me-local-dev-only")  # noqa: S106  # 공개된 예시값
    assert Settings.model_validate(data)
