"""관리자 사진 업로드·상태 API. 파일 바이트는 받지 않는다(브라우저 → S3 직행)."""

from fastapi import APIRouter, Response, status

from app.api.deps import AppSettings, DbSession, Storage
from app.api.errors import problem_responses
from app.schemas.photos import (
    CompleteIn,
    PartUrl,
    PartUrlsIn,
    PartUrlsOut,
    UploadedPart,
    UploadedPartsOut,
    UploadStartIn,
    UploadStartOut,
)
from app.schemas.posts import AdminPhotoOut
from app.services import processing
from app.services import uploads as service

router = APIRouter(tags=["admin-photos"])


@router.post(
    "/posts/{post_id}/photos",
    response_model=UploadStartOut,
    status_code=status.HTTP_201_CREATED,
    summary="업로드 시작",
    description="S3 멀티파트 업로드를 만들고 사진 레코드(UPLOADING)를 생성한다. "
    "파일 바이트는 이 API로 보내지 않는다.",
    responses=problem_responses(401, 403, 404, 409, 422, 503),
)
def start_upload(
    post_id: int, body: UploadStartIn, db: DbSession, storage: Storage, settings: AppSettings
) -> UploadStartOut:
    limits = service.UploadLimits(max_bytes=settings.upload_max_bytes, part_size=settings.upload_part_size)
    started = service.start_upload(
        db, storage, limits, post_id=post_id, filename=body.filename, size_bytes=body.size_bytes
    )
    return UploadStartOut(
        photo_id=started.photo.id,
        upload_id=started.upload_id,
        part_size=started.part_size,
        part_count=started.part_count,
    )


@router.post(
    "/photos/{photo_id}/parts",
    response_model=PartUrlsOut,
    summary="파트 업로드 URL 발급",
    description="요청한 파트 번호마다 Presigned PUT URL(1시간)을 발급한다. 번호는 1..partCount.",
    responses=problem_responses(401, 403, 404, 409, 422, 503),
)
def presign_parts(photo_id: int, body: PartUrlsIn, db: DbSession, storage: Storage) -> PartUrlsOut:
    urls = service.presign_parts(db, storage, photo_id, body.part_numbers)
    return PartUrlsOut(
        urls=[PartUrl(part_number=u.part_number, url=u.url, expires_at=u.expires_at) for u in urls]
    )


@router.get(
    "/photos/{photo_id}/parts",
    response_model=UploadedPartsOut,
    summary="이미 올라간 파트 조회 (이어 올리기)",
    responses=problem_responses(401, 404, 409, 503),
)
def list_uploaded_parts(photo_id: int, db: DbSession, storage: Storage) -> UploadedPartsOut:
    progress = service.list_uploaded_parts(db, storage, photo_id)
    return UploadedPartsOut(
        part_size=progress.part_size,
        part_count=progress.part_count,
        parts=[
            UploadedPart(part_number=p.part_number, etag=p.etag, size_bytes=p.size) for p in progress.parts
        ],
    )


@router.post(
    "/photos/{photo_id}/complete",
    response_model=AdminPhotoOut,
    summary="업로드 완료",
    description="모든 파트의 ETag를 보내 업로드를 끝낸다. 성공하면 UPLOADED가 되고 변환 작업이 등록된다. "
    "같은 요청을 다시 보내도 안전하다(멱등).",
    responses=problem_responses(401, 403, 404, 409, 422, 503),
)
def complete_upload(photo_id: int, body: CompleteIn, db: DbSession, storage: Storage) -> AdminPhotoOut:
    parts = {p.part_number: p.etag for p in body.parts}
    return AdminPhotoOut.model_validate(service.complete_upload(db, storage, photo_id, parts))


@router.get(
    "/photos/{photo_id}",
    response_model=AdminPhotoOut,
    summary="사진 상태 조회",
    description="업로드·변환 진행 상황 확인용. 변환 중에는 몇 초 간격으로 폴링한다.",
    responses=problem_responses(401, 404, 503),
)
def get_photo(photo_id: int, db: DbSession) -> AdminPhotoOut:
    return AdminPhotoOut.model_validate(service.get_photo(db, photo_id))


@router.delete(
    "/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="업로드 중단 / 사진 삭제",
    description="UPLOADING이면 멀티파트 업로드를 중단하고, 아니면 원본·파생 파일과 레코드를 지운다.",
    responses=problem_responses(401, 403, 404, 503),
)
def delete_photo(photo_id: int, db: DbSession, storage: Storage) -> Response:
    service.delete_photo(db, storage, photo_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/photos/{photo_id}/retry",
    response_model=AdminPhotoOut,
    summary="실패한 사진 재처리",
    description="FAILED 사진의 변환 작업을 다시 등록한다. FAILED가 아니면 409.",
    responses=problem_responses(401, 403, 404, 409),
)
def retry_photo(photo_id: int, db: DbSession) -> AdminPhotoOut:
    return AdminPhotoOut.model_validate(processing.retry_photo(db, photo_id))
