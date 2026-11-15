"""목업 데이터(MOCK_API=true). 관리자 통계는 seed 고정 가짜 데이터, 조회수·원본 다운로드는 실제 DB 기준.

목업 이미지 크기·이름은 scripts/seed_mock_images.py, 데모 게시물은 scripts/seed_demo_data.py가 쓴다.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Forbidden, NotFound
from app.models import Photo, PhotoStatus, Post, PostStatus
from app.schemas.public import Exif
from app.schemas.stats import (
    DownloadsStatsOut,
    PhotoDownloads,
    StatsPeriod,
)
from app.services.urls import cdn_url

MOCK_IMAGE_COUNT = 12  # seed_mock_images.py가 만드는 이미지 수
MOCK_PREFIX = "mock"
MAX_STATS_DAYS = 366

# seed_mock_images.py와 같은 크기(원본 픽셀). 다양한 비율을 섞었다.
IMAGE_SIZES: list[tuple[int, int]] = [
    (6000, 4000), (4000, 6000), (6000, 6000), (7952, 5304), (5304, 7952), (8000, 3000),
    (6000, 4000), (4000, 5000), (9504, 6336), (6336, 9504), (6000, 3375), (4000, 4000),
]  # fmt: skip

CATEGORIES = [(1, "풍경"), (2, "인물"), (3, "거리"), (4, "야경")]
_TITLES = [
    "제주 오름 일출", "을지로 골목", "한강 야경", "설악산 운해", "부산 감천마을", "서울숲의 오후",
    "남해 다랭이논", "광장시장 사람들", "인왕산에서 본 서울", "비 오는 종로", "가을 경주", "새벽 어시장",
]  # fmt: skip
_CAMERAS = [
    ("SONY", "ILCE-7RM5", "FE 24-70mm F2.8 GM II"),
    ("FUJIFILM", "GFX100S", "GF32-64mmF4 R LM WR"),
    ("Canon", "EOS R5", "RF24-105mm F4 L IS USM"),
    ("NIKON CORPORATION", "NIKON Z 8", "NIKKOR Z 24-120mm f/4 S"),
]
_BASE_TIME = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Photo:
    id: int
    image: int  # 목업 이미지 번호(0..11)
    exif: Exif


@dataclass(frozen=True)
class _Post:
    id: int
    title: str
    body: str
    category_id: int
    published_at: datetime
    view_count: int
    allow_original_download: bool
    photos: tuple[_Photo, ...]


@lru_cache
def _dataset() -> tuple[_Post, ...]:
    rng = random.Random(20261116)  # noqa: S311  # 목업 데이터용, 보안 용도 아님
    posts: list[_Post] = []
    photo_id = 1000
    for i in range(36):
        photos = []
        for _ in range(rng.randint(1, 5)):
            make, model, lens = rng.choice(_CAMERAS)
            photos.append(
                _Photo(
                    id=photo_id,
                    image=rng.randrange(MOCK_IMAGE_COUNT),
                    exif=Exif(
                        camera_make=make,
                        camera_model=model,
                        lens=lens,
                        f_number=rng.choice([2.8, 4.0, 5.6, 8.0, 11.0]),
                        exposure_time=rng.choice(["1/30", "1/125", "1/250", "1/1000", "2"]),
                        iso=rng.choice([64, 100, 400, 1600, 6400]),
                        focal_length_mm=float(rng.choice([24, 35, 50, 85, 120])),
                        taken_at=_BASE_TIME - timedelta(days=i * 2 + 1, hours=rng.randint(0, 12)),
                    ),
                )
            )
            photo_id += 1
        title = f"{_TITLES[i % len(_TITLES)]} #{i // len(_TITLES) + 1}"
        posts.append(
            _Post(
                id=i + 1,
                title=title,
                body=f"{title}. 목업 본문입니다. 실제 데이터가 아닙니다.\n\n두 번째 문단.",
                category_id=CATEGORIES[i % len(CATEGORIES)][0],
                published_at=_BASE_TIME - timedelta(days=i * 2),
                view_count=rng.randint(0, 5000),
                allow_original_download=i % 3 == 0,
                photos=tuple(photos),
            )
        )
    return tuple(posts)


def demo_posts() -> tuple["DemoPost", ...]:
    """scripts/seed_demo_data.py가 DB에 넣는 데모 게시물(목업 이미지 사용)."""
    return _dataset()


DemoPost = _Post


# ── 공개 API (10·11단계 구현 전까지) ──────────────────────────
# 목록·상세는 09단계에서 실제 구현됐으므로 조회수·원본 다운로드 목업도
# 실제 DB의 공개 게시물 기준으로 동작한다.


def original_download_url(session: Session, cdn_base_url: str, photo_id: int) -> str:
    """실제로는 원본의 10분짜리 Presigned URL. 목업은 같은 사진의 미리보기 이미지로 보낸다."""
    row = session.execute(
        select(Photo, Post)
        .join(Post, Post.id == Photo.post_id)
        .where(Photo.id == photo_id, Photo.status == PhotoStatus.READY, Post.status == PostStatus.PUBLISHED)
    ).first()
    if row is None:
        raise NotFound(f"사진 {photo_id}를 찾을 수 없습니다")
    photo, post = row
    if not photo.preview_key:
        raise NotFound(f"사진 {photo_id}를 찾을 수 없습니다")
    if not post.allow_original_download:
        raise Forbidden("이 게시물은 원본 다운로드를 허용하지 않습니다")
    return cdn_url(cdn_base_url, photo.preview_key)


# ── 관리자 통계 ──────────────────────────────────────────


def downloads_stats(days: StatsPeriod) -> DownloadsStatsOut:
    rng = random.Random(int(days))  # noqa: S311  # 목업
    items = [
        PhotoDownloads(
            photo_id=ph.id,
            post_id=p.id,
            post_title=p.title,
            original_filename=f"DSC{ph.id:05d}.ARW",
            downloads=rng.randint(1, int(days) * 3),
        )
        for p in _dataset()
        if p.allow_original_download
        for ph in p.photos
    ]
    items.sort(key=lambda d: (-d.downloads, d.photo_id))
    return DownloadsStatsOut(days=int(days), items=items[:50])
