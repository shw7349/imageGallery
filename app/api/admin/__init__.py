"""관리자 API 조립.

모든 관리자 라우터는 `protected`에 등록한다. `protected`는 라우터 수준에서 require_admin(세션 + CSRF)을
걸기 때문에 개별 라우트에서 인증을 빠뜨릴 수 없다. 인증 없이 열리는 것은 로그인(`auth.public_router`)뿐이다.
"""

from fastapi import APIRouter, Depends

from app.api.admin import auth, categories, posts
from app.api.deps import require_admin


def build_admin_router() -> APIRouter:
    root = APIRouter(prefix="/api/admin")
    root.include_router(auth.public_router)

    protected = APIRouter(dependencies=[Depends(require_admin)])
    protected.include_router(auth.router)
    protected.include_router(categories.router)
    protected.include_router(posts.router)
    root.include_router(protected)
    return root
