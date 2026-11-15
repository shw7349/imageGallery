"""관리자 사진 업로드·상태 API. 계약(스키마)은 05단계에서 확정, 구현은 06·07단계."""

from fastapi import APIRouter, Response, status

from app.api.errors import problem_responses
from app.errors import NotImplementedYet
from app.schemas.photos import (
    CompleteIn,
    PartUrlsIn,
    PartUrlsOut,
    UploadedPartsOut,
    UploadStartIn,
    UploadStartOut,
)
from app.schemas.posts import AdminPhotoOut

router = APIRouter(tags=["admin-photos"])


@router.post(
    "/posts/{post_id}/photos",
    response_model=UploadStartOut,
    status_code=status.HTTP_201_CREATED,
    summary="업로드 시작",
    description="S3 멀티파트 업로드를 만들고 사진 레코드(UPLOADING)를 생성한다. "
    "파일 바이트는 이 API로 보내지 않는다.",
    responses=problem_responses(401, 403, 404, 409, 422, 501),
)
def start_upload(post_id: int, body: UploadStartIn) -> UploadStartOut:
    raise NotImplementedYet("06단계에서 구현")


@router.post(
    "/photos/{photo_id}/parts",
    response_model=PartUrlsOut,
    summary="파트 업로드 URL 발급",
    description="요청한 파트 번호마다 Presigned PUT URL(1시간)을 발급한다. 번호는 1..partCount.",
    responses=problem_responses(401, 403, 404, 409, 422, 501),
)
def presign_parts(photo_id: int, body: PartUrlsIn) -> PartUrlsOut:
    raise NotImplementedYet("06단계에서 구현")


@router.get(
    "/photos/{photo_id}/parts",
    response_model=UploadedPartsOut,
    summary="이미 올라간 파트 조회 (이어 올리기)",
    responses=problem_responses(401, 404, 409, 501),
)
def list_uploaded_parts(photo_id: int) -> UploadedPartsOut:
    raise NotImplementedYet("06단계에서 구현")


@router.post(
    "/photos/{photo_id}/complete",
    response_model=AdminPhotoOut,
    summary="업로드 완료",
    description="모든 파트의 ETag를 보내 업로드를 끝낸다. 성공하면 UPLOADED가 되고 변환 작업이 등록된다. "
    "같은 요청을 다시 보내도 안전하다(멱등).",
    responses=problem_responses(401, 403, 404, 409, 422, 501),
)
def complete_upload(photo_id: int, body: CompleteIn) -> AdminPhotoOut:
    raise NotImplementedYet("06단계에서 구현")


@router.get(
    "/photos/{photo_id}",
    response_model=AdminPhotoOut,
    summary="사진 상태 조회",
    description="업로드·변환 진행 상황 확인용. 변환 중에는 몇 초 간격으로 폴링한다.",
    responses=problem_responses(401, 404, 501),
)
def get_photo(photo_id: int) -> AdminPhotoOut:
    raise NotImplementedYet("06단계에서 구현")


@router.delete(
    "/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="업로드 중단 / 사진 삭제",
    description="UPLOADING이면 멀티파트 업로드를 중단하고, 아니면 원본·파생 파일과 레코드를 지운다.",
    responses=problem_responses(401, 403, 404, 501),
)
def delete_photo(photo_id: int) -> Response:
    raise NotImplementedYet("06단계에서 구현")


@router.post(
    "/photos/{photo_id}/retry",
    response_model=AdminPhotoOut,
    summary="실패한 사진 재처리",
    description="FAILED 사진의 변환 작업을 다시 등록한다. FAILED가 아니면 409.",
    responses=problem_responses(401, 403, 404, 409, 501),
)
def retry_photo(photo_id: int) -> AdminPhotoOut:
    raise NotImplementedYet("07단계에서 구현")
