"""DB 엔진. 세션 팩토리·트랜잭션 헬퍼는 02단계에서 추가한다."""

from sqlalchemy import Engine, create_engine

from app.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 5},
    )
