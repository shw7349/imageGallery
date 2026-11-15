"""도메인 이벤트(동기, 프로세스 내). 트랜잭션 커밋 후에 발행한다.

게시물 삭제 시 S3 객체 정리처럼, 다른 단계에서 붙일 후속 처리를 서비스 코드 수정 없이 연결하기 위한 자리다.
핸들러가 없으면 로그만 남는다.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostDeleted:
    post_id: int
    photo_ids: tuple[int, ...]
    # 삭제해야 할 S3 키(원본)와 파생물 접두사. 07단계 이후 핸들러가 지운다.
    original_keys: tuple[str, ...] = field(default=())
    derived_prefixes: tuple[str, ...] = field(default=())


Event = PostDeleted
Handler = Callable[[Event], None]

_handlers: dict[type[Event], list[Handler]] = {}


def subscribe(event_type: type[Event], handler: Handler) -> None:
    _handlers.setdefault(event_type, []).append(handler)


def publish(event: Event) -> None:
    handlers = _handlers.get(type(event), [])
    if not handlers:
        logger.info("domain event without handler", extra={"event": type(event).__name__})
    for handler in handlers:
        handler(event)
