"""도메인 예외. 서비스는 이 예외만 던지고, HTTP 변환은 app/api/errors.py가 한다(서비스는 HTTP를 모른다)."""

from typing import Any


class DomainError(Exception):
    """모든 도메인 예외의 기반. `code`는 기계가 읽는 식별자, `detail`은 사람이 읽는 설명."""

    code: str = "error"
    title: str = "오류가 발생했습니다"

    def __init__(self, detail: str | None = None, **extra: Any) -> None:  # Any: problem+json 확장 필드
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra


class NotFound(DomainError):
    code = "not_found"
    title = "대상을 찾을 수 없습니다"


class Conflict(DomainError):
    code = "conflict"
    title = "현재 상태에서 처리할 수 없습니다"


class ValidationFailed(DomainError):
    code = "validation_failed"
    title = "입력값이 올바르지 않습니다"


class AuthenticationRequired(DomainError):
    code = "authentication_required"
    title = "로그인이 필요합니다"


class InvalidCredentials(DomainError):
    code = "invalid_credentials"
    title = "아이디 또는 비밀번호가 올바르지 않습니다"


class Forbidden(DomainError):
    code = "forbidden"
    title = "권한이 없습니다"


class TooManyRequests(DomainError):
    code = "too_many_requests"
    title = "요청이 너무 많습니다"

    def __init__(self, detail: str | None = None, *, retry_after_seconds: int, **extra: Any) -> None:
        super().__init__(detail, **extra)
        self.retry_after_seconds = retry_after_seconds


class NotImplementedYet(DomainError):
    code = "not_implemented"
    title = "아직 구현되지 않은 기능입니다"
