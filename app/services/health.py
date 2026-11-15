"""헬스 체크 로직."""

import logging

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def check_database(engine: Engine) -> bool:
    """DB에 `SELECT 1`을 보내 연결 가능 여부를 반환한다. 실패 원인은 로그로 남긴다."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("database health check failed")
        return False
    return True
