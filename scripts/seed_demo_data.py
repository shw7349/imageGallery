"""데모 데이터: 목업 이미지(make mock-images)를 쓰는 공개 게시물 36개를 실제 DB에 넣는다(로컬 전용).

    uv run python scripts/seed_demo_data.py           # 넣기(이미 있으면 건너뜀)
    uv run python scripts/seed_demo_data.py --reset   # 데모 게시물 삭제 후 다시 넣기

- 데모 게시물은 제목이 "[데모] "로 시작한다. --reset은 이 게시물만 지운다.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # app·scripts 패키지 경로

from sqlalchemy import delete, select  # noqa: E402

from app.config import Environment, get_settings  # noqa: E402
from app.db import create_db_engine, create_session_factory, session_scope  # noqa: E402
from app.models import Category, Photo, PhotoStatus, Post, PostStatus  # noqa: E402
from scripts.demo_dataset import CATEGORIES, IMAGE_SIZES, MOCK_PREFIX, demo_posts  # noqa: E402

DEMO_PREFIX = "[데모] "


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.env is Environment.PROD:
        sys.exit("운영 환경에서는 실행할 수 없습니다")
    factory = create_session_factory(create_db_engine(settings))

    with session_scope(factory) as s:
        existing = s.scalar(select(Post.id).where(Post.title.startswith(DEMO_PREFIX)).limit(1))
        if existing and not args.reset:
            print("데모 데이터가 이미 있습니다(--reset으로 다시 만들기)")
            return 0
        if args.reset:
            s.execute(delete(Post).where(Post.title.startswith(DEMO_PREFIX)))

        cat_ids: dict[int, int] = {}
        for mock_id, name in CATEGORIES:
            cat = s.scalar(select(Category).where(Category.name == name))
            if cat is None:
                cat = Category(name=name, sort_order=mock_id)
                s.add(cat)
                s.flush()
            cat_ids[mock_id] = cat.id

        photo_count = 0
        for demo in reversed(demo_posts()):  # 오래된 것부터 넣어 id 순서와 공개일 순서를 맞춘다
            post = Post(
                category_id=cat_ids[demo.category_id],
                title=DEMO_PREFIX + demo.title,
                body=demo.body,
                status=PostStatus.PUBLISHED,
                view_count=demo.view_count,
                published_at=demo.published_at,
            )
            s.add(post)
            s.flush()
            for order, ph in enumerate(demo.photos):
                width, height = IMAGE_SIZES[ph.image]
                base = f"{MOCK_PREFIX}/{ph.image}/"
                photo = Photo(
                    post_id=post.id,
                    sort_order=order,
                    original_key="",
                    original_filename=f"DSC{ph.id:05d}.jpg",
                    size_bytes=1,
                    content_type="image/webp",
                    status=PhotoStatus.READY,
                    width=width,
                    height=height,
                    thumb_key=base + "thumb.webp",
                    preview_key=base + "preview.webp",
                    dzi_key=base + "photo.dzi",
                    exif=ph.exif.model_dump(mode="json"),
                )
                s.add(photo)
                s.flush()
                photo.original_key = f"originals/demo/{photo.id}.webp"  # 데모 사진은 원본 파일이 없다
                if order == 0:
                    post.cover_photo_id = photo.id
                photo_count += 1
    print(f"데모 게시물 {len(demo_posts())}개, 사진 {photo_count}장을 넣었습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
