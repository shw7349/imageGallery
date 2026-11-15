"""로컬 개발용 데모 데이터 정의(seed 고정, 재실행해도 같다).

- scripts/seed_mock_images.py: IMAGE_SIZES 크기의 목업 이미지를 MinIO derived 버킷 mock/{n}/에 생성
- scripts/seed_demo_data.py: demo_posts()를 실제 DB에 공개 게시물로 넣음(이미지는 위 목업 이미지)
운영 코드(app/)에서는 쓰지 않는다.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from app.schemas.public import Exif

MOCK_IMAGE_COUNT = 12  # seed_mock_images.py가 만드는 이미지 수
MOCK_PREFIX = "mock"

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
                photos=tuple(photos),
            )
        )
    return tuple(posts)


def demo_posts() -> tuple["DemoPost", ...]:
    """scripts/seed_demo_data.py가 DB에 넣는 데모 게시물(목업 이미지 사용)."""
    return _dataset()


DemoPost = _Post
