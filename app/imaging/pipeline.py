"""처리 파이프라인: 원본 경로 → (RAW 현상) → EXIF → 파생 이미지. DB·S3를 모른다."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from app.imaging import derivatives, exif, raw

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    out_dir: Path  # 업로드할 디렉터리(thumb.webp, preview.webp, photo.dzi, photo_files/)
    width: int
    height: int
    exif: exif.ExifData


def process(original_path: Path, work_dir: Path) -> PipelineResult:
    """work_dir 아래에 중간 파일과 결과(out/)를 만든다. 정리는 호출자 몫(work_dir째 삭제)."""
    started = time.monotonic()
    metadata = exif.extract_exif(original_path)  # 원본(RAW 포함)에서 읽는다

    source = original_path
    if raw.is_raw(original_path):
        source = raw.develop_to_tiff(original_path, work_dir / "developed.tif")

    result = derivatives.make_derivatives(source, work_dir / "out")
    if source != original_path:
        source.unlink(missing_ok=True)  # 현상 TIFF(수백 MB)는 바로 지운다

    logger.info(
        "pipeline done",
        extra={
            "width": result.width,
            "height": result.height,
            "seconds": round(time.monotonic() - started, 1),
        },
    )
    return PipelineResult(out_dir=result.out_dir, width=result.width, height=result.height, exif=metadata)
