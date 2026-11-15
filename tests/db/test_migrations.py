"""마이그레이션이 왕복(up → down → up) 가능하고 모델과 어긋나지 않는지 검사한다."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.models import Base
from tests.conftest import alembic_config, recreate_database


@pytest.fixture
def scratch_db_url(settings: Settings) -> Iterator[str]:
    url = make_url(settings.database_url)
    scratch = url.set(database=f"{url.database}_mig_test").render_as_string(hide_password=False)
    recreate_database(scratch)
    yield scratch


def _tables(url: str) -> set[str]:
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


def test_upgrade_downgrade_roundtrip(scratch_db_url: str) -> None:
    cfg = alembic_config(scratch_db_url)
    command.upgrade(cfg, "head")
    assert {t.name for t in Base.metadata.sorted_tables} <= _tables(scratch_db_url)

    command.downgrade(cfg, "base")
    assert _tables(scratch_db_url) <= {"alembic_version"}

    command.upgrade(cfg, "head")
    assert {t.name for t in Base.metadata.sorted_tables} <= _tables(scratch_db_url)


def test_models_match_migrations(settings: Settings) -> None:
    """모델을 바꾸고 리비전을 안 만들면 실패한다."""
    eng = create_engine(settings.database_url)
    try:
        with eng.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        eng.dispose()
    assert diff == []


def test_pg_trgm_extension_installed(settings: Settings) -> None:
    eng = create_engine(settings.database_url)
    try:
        with eng.connect() as conn:
            found = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).scalar()
    finally:
        eng.dispose()
    assert found == 1
