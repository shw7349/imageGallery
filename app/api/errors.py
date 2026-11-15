"""도메인 예외·검증 오류·HTTP 오류를 RFC 7807 `application/problem+json`으로 변환한다."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import (
    AuthenticationRequired,
    Conflict,
    DomainError,
    Forbidden,
    InvalidCredentials,
    NotFound,
    NotImplementedYet,
    TooManyRequests,
    ValidationFailed,
)

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"

_STATUS: dict[type[DomainError], int] = {
    NotFound: 404,
    Conflict: 409,
    ValidationFailed: 422,
    AuthenticationRequired: 401,
    InvalidCredentials: 401,
    Forbidden: 403,
    TooManyRequests: 429,
    NotImplementedYet: 501,
}


def _status_for(exc: DomainError) -> int:
    for cls, status in _STATUS.items():
        if isinstance(exc, cls):
            return status
    return 400


def problem(
    status: int,
    *,
    code: str,
    title: str,
    detail: str | None = None,
    instance: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,  # Any: problem+json 확장 필드(errors 등)
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://gallery.example/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    body.update(extra)
    return JSONResponse(jsonable_encoder(body), status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101  # 등록 타입 보장
    status = _status_for(exc)
    headers = None
    if isinstance(exc, TooManyRequests):
        headers = {"Retry-After": str(exc.retry_after_seconds)}
    return problem(
        status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail if exc.detail != exc.title else None,
        instance=request.url.path,
        headers=headers,
        **exc.extra,
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return problem(
        422,
        code=ValidationFailed.code,
        title=ValidationFailed.title,
        instance=request.url.path,
        errors=errors,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "Error"
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
    return problem(
        exc.status_code,
        code=title.lower().replace(" ", "_"),
        title=title,
        detail=detail,
        instance=request.url.path,
        headers=dict(exc.headers) if exc.headers else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)


ProblemExample = dict[str, Any]  # Any: OpenAPI 응답 정의(JSON)


def problem_responses(*statuses: int) -> dict[int | str, ProblemExample]:
    """라우트 `responses=`용: 지정한 상태 코드들을 problem+json 응답으로 문서화한다."""
    descriptions = {
        400: "잘못된 요청",
        401: "로그인 필요 / 인증 실패",
        403: "권한 없음 (CSRF 토큰 누락·불일치 포함)",
        404: "대상 없음",
        409: "상태 충돌",
        422: "입력값 검증 실패",
        429: "요청 과다 (Retry-After 헤더)",
        501: "아직 구현되지 않음",
    }
    return {
        s: {
            "description": descriptions.get(s, HTTPStatus(s).phrase),
            "content": {PROBLEM_JSON: {"schema": {"$ref": "#/components/schemas/Problem"}}},
        }
        for s in statuses
    }
