"""파생 이미지 생성(pyvips). 순수 함수: 입력 경로 → 출력 디렉터리.

출력(계약 docs/api.md "이미지 URL"과 같은 이름):
  thumb.webp    긴 변 600px, Q80, 메타데이터 제거
  preview.webp  긴 변 3840px(원본이 작으면 그대로), Q85, 메타데이터 제거
  photo.dzi + photo_files/{레벨}/{열}_{행}.webp   256px 타일, overlap 1, Q80

모든 출력은 EXIF 방향을 반영하고 8bit sRGB로 맞춘다(CMYK·16bit·흑백·알파 입력 포함).
"""

from dataclasses import dataclass
from pathlib import Path

import pyvips

from app.imaging.errors import ImagingError

THUMB_SIZE = 600
PREVIEW_SIZE = 3840
TILE_SIZE = 256
TILE_OVERLAP = 1

THUMB_NAME = "thumb.webp"
PREVIEW_NAME = "preview.webp"
DZI_BASENAME = "photo"  # → photo.dzi, photo_files/


@dataclass(frozen=True)
class Derivatives:
    out_dir: Path
    width: int  # 방향 반영 후 원본 픽셀 크기
    height: int


def configure(concurrency: int, cache_max_mb: int) -> None:
    """libvips 스레드·캐시 제한(워커 시작 시 1회). 메모리 상한을 예측 가능하게 한다."""
    pyvips.concurrency_set(concurrency)
    pyvips.cache_set_max_mem(cache_max_mb * 1024 * 1024)
    pyvips.cache_set_max(100)


def to_srgb8(image: pyvips.Image) -> pyvips.Image:
    """임베디드 ICC가 있으면 그것으로, 없으면 해석(interpretation)대로 8bit sRGB 3밴드로 변환."""
    if image.hasalpha():
        image = image.flatten(background=[255, 255, 255])
    if image.get_typeof("icc-profile-data") != 0:
        try:
            image = image.icc_transform("srgb", embedded=True, intent="perceptual")
        except pyvips.Error:
            image = image.colourspace("srgb")
    elif image.interpretation != "srgb":
        image = image.colourspace("srgb")
    if image.format != "uchar":
        image = image.cast("uchar")
    if image.bands > 3:
        image = image.extract_band(0, n=3)
    return image.copy(interpretation="srgb")


def _needs_rotation(path: Path) -> bool:
    header = pyvips.Image.new_from_file(str(path))
    if header.get_typeof("orientation") == 0:
        return False
    return int(header.get("orientation")) not in (0, 1)


def _thumbnail(source: Path, size: int, dest: Path, quality: int) -> None:
    # thumbnail: 방향 자동 반영, 축소 로딩(JPEG shrink-on-load)으로 원본 전체를 디코딩하지 않는다.
    image = pyvips.Image.thumbnail(str(source), size, height=size, size="down", export_profile="srgb")
    to_srgb8(image).webpsave(str(dest), Q=quality, strip=True)


def make_derivatives(source: Path, out_dir: Path) -> Derivatives:
    """source(JPEG/TIFF/PNG, RAW는 미리 TIFF로 현상)에서 썸네일·미리보기·딥줌 타일을 만든다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 회전이 없으면 순차 접근(스트리밍)으로 메모리를 최소화. 회전이 필요하면 임의 접근이 필요하다.
        access = "random" if _needs_rotation(source) else "sequential"
        full = pyvips.Image.new_from_file(str(source), access=access).autorot()
        full = to_srgb8(full)
        width, height = full.width, full.height
        full.dzsave(
            str(out_dir / DZI_BASENAME),
            tile_size=TILE_SIZE,
            overlap=TILE_OVERLAP,
            suffix=".webp[Q=80]",
            strip=True,
        )
        # dzsave가 남기는 이미지 속성 파일: 뷰어가 쓰지 않고 공개 CDN에 올라가므로 지운다(정보 노출 방지)
        (out_dir / f"{DZI_BASENAME}_files" / "vips-properties.xml").unlink(missing_ok=True)
        preview = out_dir / PREVIEW_NAME
        _thumbnail(source, PREVIEW_SIZE, preview, quality=85)
        _thumbnail(preview, THUMB_SIZE, out_dir / THUMB_NAME, quality=80)  # 작은 미리보기에서 만든다
    except pyvips.Error as exc:
        raise ImagingError(f"이미지 변환 실패: {str(exc).splitlines()[0]}") from exc
    return Derivatives(out_dir=out_dir, width=width, height=height)
