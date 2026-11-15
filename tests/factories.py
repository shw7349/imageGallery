"""테스트 데이터 생성 헬퍼. 업로드 API(06단계) 없이 사진 레코드를 직접 만든다."""

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import Photo, PhotoStatus


def insert_photo(
    engine: Engine,
    post_id: int,
    *,
    status: PhotoStatus = PhotoStatus.READY,
    sort_order: int = 0,
    filename: str = "DSC0001.jpg",
) -> int:
    with Session(engine) as s:
        photo = Photo(
            post_id=post_id,
            sort_order=sort_order,
            original_key=f"originals/{post_id}/factory/original.jpg",
            original_filename=filename,
            size_bytes=1024,
            content_type="image/jpeg",
            status=status,
        )
        s.add(photo)
        s.commit()
        return photo.id
