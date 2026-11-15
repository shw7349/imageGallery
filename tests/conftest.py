"""테스트 공통 픽스처.

- DB·S3 모킹 금지(.claude/CLAUDE.md). `make up`으로 띄운 Postgres·MinIO를 사용한다.
- 테스트 DB는 TEST_DATABASE_URL, 없으면 DATABASE_URL의 DB 이름 뒤에 `_test`를 붙인 DB를 쓴다.
  세션 시작 시 해당 DB를 지우고 새로 만든다(개발 DB는 건드리지 않는다).
"""

import os
import secrets
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Environment, Settings
from app.main import create_app


def _test_database_url(base: Settings) -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    url = make_url(base.database_url)
    if url.database is None:
        raise RuntimeError("DATABASE_URL에 DB 이름이 없습니다")
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


def _recreate_database(url: str) -> None:
    target = make_url(url)
    name = target.database
    if not name or not name.endswith("_test"):
        raise RuntimeError(f"테스트 DB 이름은 _test로 끝나야 합니다: {name!r}")
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            quoted = conn.dialect.identifier_preparer.quote(name)
            conn.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def settings() -> Settings:
    base = Settings()  # 필수 값은 .env·환경변수에서 주입
    test_url = _test_database_url(base)
    _recreate_database(test_url)
    return base.model_copy(
        update={
            "env": Environment.TEST,
            "database_url": test_url,
            "s3_originals_bucket": "gallery-test-originals",
            "s3_derived_bucket": "gallery-test-derived",
            "session_secret": SecretStr(secrets.token_urlsafe(48)),
        }
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
