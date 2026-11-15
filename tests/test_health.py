import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_healthz_ok(client: TestClient) -> None:
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "ok"}
    assert res.headers["cache-control"] == "no-store"


def test_healthz_returns_503_when_db_unreachable(settings: Settings) -> None:
    broken = settings.model_copy(update={"database_url": "postgresql+psycopg://nobody:x@127.0.0.1:1/none"})
    with TestClient(create_app(broken)) as c:
        res = c.get("/healthz")
    assert res.status_code == 503
    assert res.json() == {"status": "error", "db": "error"}


def test_request_id_is_generated(client: TestClient) -> None:
    res = client.get("/healthz")
    assert len(res.headers["x-request-id"]) == 32


def test_valid_request_id_is_echoed(client: TestClient) -> None:
    res = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert res.headers["x-request-id"] == "abc-123"


def test_malformed_request_id_is_replaced(client: TestClient) -> None:
    res = client.get("/healthz", headers={"X-Request-ID": "bad id\ninjected"})
    assert res.headers["x-request-id"] != "bad id\ninjected"
    assert len(res.headers["x-request-id"]) == 32


def test_cors_allows_configured_origin_only(settings: Settings) -> None:
    configured = settings.model_copy(update={"cors_origins": ["https://gallery.example"]})
    with TestClient(create_app(configured)) as c:
        ok = c.get("/healthz", headers={"Origin": "https://gallery.example"})
        bad = c.get("/healthz", headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://gallery.example"
    assert "access-control-allow-origin" not in bad.headers


def test_request_log_contains_request_id(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.request"):
        client.get("/healthz", headers={"X-Request-ID": "log-check-1"})
    records = [r for r in caplog.records if r.name == "app.request"]
    assert records, "요청 로그가 없습니다"
    assert getattr(records[-1], "request_id", None) == "log-check-1"
    assert getattr(records[-1], "path", None) == "/healthz"
