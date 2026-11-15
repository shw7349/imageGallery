"""방문자용 공개 API(/api). 로그인 없음."""

from fastapi import APIRouter

from app.api.public import categories, posts, views


def build_public_router() -> APIRouter:
    root = APIRouter(prefix="/api", tags=["public"])
    root.include_router(categories.router)
    root.include_router(posts.router)
    root.include_router(views.router)
    return root
