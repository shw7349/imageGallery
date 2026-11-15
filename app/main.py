"""FastAPI 앱 팩토리. 실행: `uvicorn --factory app.main:create_app`."""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.admin import build_admin_router
from app.api.errors import problem_responses, register_error_handlers
from app.api.public import build_public_router
from app.api.ratelimit import RateLimiter
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory
from app.logging import request_id_var, setup_logging
from app.schemas.problem import Problem
from app.storage.s3 import S3Storage

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _request_id_from(request: Request) -> str:
    """신뢰할 수 있는 형식이면 들어온 요청 ID를 쓰고, 아니면 새로 만든다(로그 주입 방지)."""
    incoming = request.headers.get(REQUEST_ID_HEADER)
    if incoming and _VALID_REQUEST_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


def _register_problem_schema(app: FastAPI) -> None:
    """problem_responses()가 참조하는 #/components/schemas/Problem을 OpenAPI에 추가한다."""
    original = app.openapi

    def openapi() -> dict[str, Any]:  # Any: OpenAPI 문서(JSON)
        if app.openapi_schema:
            return app.openapi_schema
        schema = original()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components["Problem"] = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
        # FastAPI가 자동으로 넣는 422(HTTPValidationError, application/json)를 실제 응답 형식으로 바꾼다.
        problem_422 = problem_responses(422)[422]
        for item in schema.get("paths", {}).values():
            for op in item.values():
                if "422" in op.get("responses", {}):
                    op["responses"]["422"] = problem_422
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]  # FastAPI 공식 커스터마이즈 방식


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_db_engine(settings)
        app.state.session_factory = create_session_factory(app.state.engine)
        app.state.storage = S3Storage(settings)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(title="Gallery Backend", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.view_limiter = RateLimiter(settings.views_rate_per_minute)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _request_id_from(request)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            if request.url.path.startswith("/api/admin"):
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Robots-Tag"] = "noindex, nofollow"  # 검색엔진 노출 차단
            # 쿼리스트링은 남기지 않는다(서명·토큰이 포함될 수 있음).
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    # CORS는 미들웨어 중 가장 바깥에서 처리되도록 마지막에 등록한다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    register_error_handlers(app)
    _register_problem_schema(app)
    app.include_router(health.router)
    app.include_router(build_admin_router())
    app.include_router(build_public_router())
    return app
