"""결정적(seed 고정) 목업 데이터와 조회 함수. 재시작해도 같은 데이터가 나온다."""

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from app.errors import Forbidden, NotFound, ValidationFailed
from app.schemas.pagination import Page
from app.schemas.public import (
    Exif,
    PublicCategory,
    PublicPhoto,
    PublicPostDetail,
    PublicPostSummary,
    PublicSort,
)
from app.schemas.stats import (
    DailyViews,
    DownloadsStatsOut,
    PhotoDownloads,
    StatsPeriod,
    TopPost,
    TopPostsOut,
    ViewsStatsOut,
)

MOCK_IMAGE_COUNT = 12  # seed_mock_images.py가 만드는 이미지 수
MOCK_PREFIX = "mock"
MAX_STATS_DAYS = 366

# seed_mock_images.py와 같은 크기(원본 픽셀). 다양한 비율을 섞었다.
IMAGE_SIZES: list[tuple[int, int]] = [
    (6000, 4000), (4000, 6000), (6000, 6000), (7952, 5304), (5304, 7952), (8000, 3000),
    (6000, 4000), (4000, 5000), (9504, 6336), (6336, 9504), (6000, 3375), (4000, 4000),
]  # fmt: skip

_CATEGORIES = [(1, "풍경"), (2, "인물"), (3, "거리"), (4, "야경")]
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
                category_id=_CATEGORIES[i % len(_CATEGORIES)][0],
                published_at=_BASE_TIME - timedelta(days=i * 2),
                view_count=rng.randint(0, 5000),
                allow_original_download=i % 3 == 0,
                photos=tuple(photos),
            )
        )
    return tuple(posts)


def _image_url(cdn_base_url: str, image: int, name: str) -> str:
    return f"{cdn_base_url.rstrip('/')}/{MOCK_PREFIX}/{image}/{name}"


def _category_name(category_id: int) -> str:
    return dict(_CATEGORIES)[category_id]


def _post(post_id: int) -> _Post:
    for p in _dataset():
        if p.id == post_id:
            return p
    raise NotFound(f"게시물 {post_id}를 찾을 수 없습니다")


# ── 공개 API ────────────────────────────────────────────


def categories() -> list[PublicCategory]:
    return [PublicCategory(id=cid, name=name) for cid, name in _CATEGORIES]


def list_posts(
    cdn_base_url: str, *, category: int | None, q: str | None, page: int, size: int, sort: PublicSort
) -> Page[PublicPostSummary]:
    rows = list(_dataset())
    if category is not None:
        rows = [p for p in rows if p.category_id == category]
    if q:
        needle = q.lower()
        rows = [p for p in rows if needle in p.title.lower() or needle in p.body.lower()]
    if sort is PublicSort.VIEWS:
        rows.sort(key=lambda p: (-p.view_count, -p.id))
    else:
        rows.sort(key=lambda p: (p.published_at, p.id), reverse=True)
    window = rows[(page - 1) * size : page * size]
    items = [
        PublicPostSummary(
            id=p.id,
            title=p.title,
            category_id=p.category_id,
            category_name=_category_name(p.category_id),
            thumb_url=_image_url(cdn_base_url, p.photos[0].image, "thumb.webp"),
            photo_count=len(p.photos),
            view_count=p.view_count,
            published_at=p.published_at,
        )
        for p in window
    ]
    return Page(items=items, page=page, size=size, total=len(rows))


def post_detail(cdn_base_url: str, post_id: int) -> PublicPostDetail:
    p = _post(post_id)
    photos = []
    for ph in p.photos:
        width, height = IMAGE_SIZES[ph.image]
        photos.append(
            PublicPhoto(
                id=ph.id,
                width=width,
                height=height,
                thumb_url=_image_url(cdn_base_url, ph.image, "thumb.webp"),
                preview_url=_image_url(cdn_base_url, ph.image, "preview.webp"),
                dzi_url=_image_url(cdn_base_url, ph.image, "photo.dzi"),
                exif=ph.exif,
                original_download_url=f"/api/photos/{ph.id}/original" if p.allow_original_download else None,
            )
        )
    return PublicPostDetail(
        id=p.id,
        title=p.title,
        body=p.body,
        category_id=p.category_id,
        category_name=_category_name(p.category_id),
        view_count=p.view_count,
        published_at=p.published_at,
        allow_original_download=p.allow_original_download,
        photos=photos,
    )


def record_view(post_id: int) -> None:
    _post(post_id)  # 없는 게시물은 404. 목업은 숫자를 바꾸지 않는다.


def original_download_url(cdn_base_url: str, photo_id: int) -> str:
    """실제로는 원본의 10분짜리 Presigned URL. 목업은 미리보기 이미지로 보낸다."""
    for p in _dataset():
        for ph in p.photos:
            if ph.id == photo_id:
                if not p.allow_original_download:
                    raise Forbidden("이 게시물은 원본 다운로드를 허용하지 않습니다")
                return _image_url(cdn_base_url, ph.image, "preview.webp")
    raise NotFound(f"사진 {photo_id}를 찾을 수 없습니다")


# ── 관리자 통계 ──────────────────────────────────────────


def _daily(day: date) -> int:
    return random.Random(day.toordinal()).randint(20, 400)  # noqa: S311  # 목업


def views_stats(from_date: date, to_date: date) -> ViewsStatsOut:
    if to_date < from_date:
        raise ValidationFailed("to는 from 이후여야 합니다")
    span = (to_date - from_date).days + 1
    if span > MAX_STATS_DAYS:
        raise ValidationFailed(f"기간은 최대 {MAX_STATS_DAYS}일입니다")
    days = [
        DailyViews(date=from_date + timedelta(days=i), views=_daily(from_date + timedelta(days=i)))
        for i in range(span)
    ]
    return ViewsStatsOut(from_date=from_date, to_date=to_date, total=sum(d.views for d in days), days=days)


def top_posts(days: StatsPeriod) -> TopPostsOut:
    rows = sorted(_dataset(), key=lambda p: -p.view_count)[:20]
    scale = int(days) / 90
    return TopPostsOut(
        days=int(days),
        items=[TopPost(post_id=p.id, title=p.title, views=max(1, int(p.view_count * scale))) for p in rows],
    )


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
