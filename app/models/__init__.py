"""모든 모델을 여기서 import해야 Alembic autogenerate가 메타데이터를 본다."""

from app.models.auth import AdminUser, LoginAttempt
from app.models.base import Base
from app.models.content import Category, Photo, Post
from app.models.enums import JobStatus, PhotoStatus, PostStatus
from app.models.jobs import PhotoJob
from app.models.stats import DownloadLog, PostViewDaily, ViewDedupe

__all__ = [
    "AdminUser",
    "Base",
    "Category",
    "DownloadLog",
    "JobStatus",
    "LoginAttempt",
    "Photo",
    "PhotoJob",
    "PhotoStatus",
    "Post",
    "PostStatus",
    "PostViewDaily",
    "ViewDedupe",
]
