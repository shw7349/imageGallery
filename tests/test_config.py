"""설정 로딩. 새로 받은 저장소는 .env.example을 그대로 .env로 쓰므로 예시 파일이 항상 읽혀야 한다."""

from pathlib import Path

import pytest

from app.config import Environment, Settings

ROOT = Path(__file__).resolve().parents[1]


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_env_example_loads_as_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """회귀: 쉼표 구분 값(JOB_RETRY_DELAYS_SECONDS)을 해석하지 못해 새로 받은 저장소가 시작되지 않았다."""
    _clear_env(monkeypatch)
    settings = Settings(_env_file=ROOT / ".env.example")  # type: ignore[call-arg]  # pydantic-settings 인자
    assert settings.env is Environment.LOCAL
    assert settings.job_retry_delays_seconds == [60, 300, 900]
    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:5173"]
    assert settings.admin_hosts == []
    assert settings.s3_endpoint_url == "http://localhost:9000"


def test_every_setting_is_documented_in_env_example() -> None:
    """설정을 추가하면 .env.example에도 적는다(필수 값이 빠지면 새 환경이 시작되지 않는다)."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0] for line in text.splitlines() if "=" in line and not line.startswith("#")
    }
    required = {name.upper() for name, field in Settings.model_fields.items() if field.is_required()}
    assert required <= documented, f".env.example에 없는 필수 설정: {sorted(required - documented)}"


def test_comma_separated_lists_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("JOB_RETRY_DELAYS_SECONDS", "5, 10")
    monkeypatch.setenv("ADMIN_HOSTS", "Admin.Example.com")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example,https://b.example")
    settings = Settings(_env_file=ROOT / ".env.example")  # type: ignore[call-arg]  # pydantic-settings 인자
    assert settings.job_retry_delays_seconds == [5, 10]
    assert settings.admin_hosts == ["admin.example.com"]
    assert settings.cors_origins == ["https://a.example", "https://b.example"]
