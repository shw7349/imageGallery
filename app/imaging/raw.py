"""RAW 현상(rawpy/LibRaw) → 16bit TIFF. 순수 함수: 입력 경로 → 출력 경로."""

import logging
from pathlib import Path

import pyvips
import rawpy

from app.imaging.errors import ImagingError

logger = logging.getLogger(__name__)

RAW_EXTENSIONS = frozenset({"arw", "cr2", "cr3", "nef", "raf", "orf", "rw2", "dng"})


def is_raw(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in RAW_EXTENSIONS


def develop_to_tiff(raw_path: Path, out_path: Path) -> Path:
    """카메라 화이트밸런스, 16bit sRGB로 현상해 무압축 TIFF로 쓴다.

    현상 결과(예: 6000만 화소 × 6바이트 ≈ 360MB)는 디스크에 쓰자마자 참조를 해제한다.
    회전은 LibRaw가 RAW 메타데이터대로 적용하므로 결과 TIFF에는 방향 태그가 없다.
    """
    try:
        with rawpy.imread(str(raw_path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=16, output_color=rawpy.ColorSpace.sRGB)
    except (rawpy.LibRawError, OSError) as exc:
        raise ImagingError(f"RAW 현상 실패: {type(exc).__name__}: {exc}") from exc

    try:
        height, width, bands = rgb.shape
        image = pyvips.Image.new_from_memory(rgb.data, width, height, bands, "ushort")
        image = image.copy(interpretation="rgb16")
        image.tiffsave(str(out_path), compression="none", bigtiff=True)
    except pyvips.Error as exc:
        raise ImagingError(f"현상 결과 저장 실패: {exc}") from exc
    finally:
        del rgb  # 큰 배열을 즉시 해제
    logger.info("raw developed", extra={"width": width, "height": height})
    return out_path
