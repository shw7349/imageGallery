"""JSON 구조 로그 설정과 요청 ID 전파."""

import logging
import sys
from contextvars import ContextVar
from typing import Any

from pythonjsonlogger.json import JsonFormatter

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


_base_factory = logging.getLogRecordFactory()


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:  # Any: LogRecordFactory 시그니처 그대로
    """모든 로그 레코드에 현재 요청 ID를 붙인다 (핸들러와 무관하게 적용)."""
    record = _base_factory(*args, **kwargs)
    record.request_id = request_id_var.get()
    return record


logging.setLogRecordFactory(_record_factory)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "{asctime}{levelname}{name}{message}{request_id}",
            style="{",
            rename_fields={"asctime": "time", "levelname": "level", "name": "logger"},
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # uvicorn 자체 핸들러를 제거하고 루트(JSON)로 보낸다.
    # 접근 로그(uvicorn.access)는 쿼리스트링(Presigned 서명 등)을 남기므로 끈다.
    # 요청 로그는 app.main의 미들웨어가 경로만 남긴다.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
