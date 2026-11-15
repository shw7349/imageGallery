"""DB 엔진·세션.

- API: `get_session` 의존성이 요청마다 세션을 주고, 요청이 끝나면 닫는다(커밋하지 않은 변경은 버려진다).
- 서비스: 쓰기 작업은 `transaction(session)`으로 감싸 트랜잭션 경계를 서비스가 정한다.
- 워커·CLI: `session_scope(factory)`로 세션 + 트랜잭션을 한 번에 연다.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

SessionFactory = sessionmaker[Session]


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 5},
    )


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI 의존성: 요청 단위 세션."""
    factory: SessionFactory = request.app.state.session_factory
    with factory() as session:
        yield session


@contextmanager
def transaction(session: Session) -> Iterator[Session]:
    """서비스용 트랜잭션 경계. 정상 종료 시 커밋, 예외 시 롤백 후 다시 던진다."""
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise


@contextmanager
def session_scope(factory: SessionFactory) -> Iterator[Session]:
    """워커·CLI용: 세션을 열고 트랜잭션으로 감싼 뒤 닫는다."""
    with factory() as session, transaction(session):
        yield session
