"""exiftool로 촬영 정보 추출. 정해진 키만 반환하고 위치(GPS) 정보는 읽지도 않는다."""

import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.imaging.errors import ImagingError

logger = logging.getLogger(__name__)

EXIFTOOL_TIMEOUT_SECONDS = 60

# 요청하는 태그만 읽는다(허용 목록). `#`은 숫자 값으로 받기.
_TAGS = [
    "-Make",
    "-Model",
    "-LensModel",
    "-LensID",
    "-Lens",
    "-FNumber#",
    "-ExposureTime",
    "-ISO#",
    "-FocalLength#",
    "-DateTimeOriginal",
    "-OffsetTimeOriginal",
    "-ImageWidth#",
    "-ImageHeight#",
]

# 반환 키: 계약의 Exif 스키마(app/schemas/public.py)와 같은 이름 + 원본 해상도
ExifData = dict[str, Any]  # Any: 문자열·숫자 혼합 JSON


def _exiftool() -> str:
    path = shutil.which("exiftool")
    if path is None:
        raise ImagingError("exiftool이 설치되어 있지 않습니다")
    return path


def _str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _num(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _taken_at(date_text: object, offset: object) -> str | None:
    text = _str(date_text)
    if not text:
        return None
    try:
        dt = datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007  # 카메라 시계(시간대 없음)
    except ValueError:
        return None
    iso = dt.isoformat()
    off = _str(offset)
    if off and len(off) == 6 and off[0] in "+-":
        iso += off
    return iso


def extract_exif(path: Path) -> ExifData:
    """촬영 정보 dict. 값이 없는 키는 None. exiftool 실패는 ImagingError."""
    cmd = [_exiftool(), "-json", "-api", "largefilesupport=1", *_TAGS, str(path)]
    try:
        proc = subprocess.run(  # noqa: S603  # 고정 실행 파일·인자, 파일 경로는 내부 생성
            cmd, capture_output=True, timeout=EXIFTOOL_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ImagingError(f"exiftool 시간 초과({EXIFTOOL_TIMEOUT_SECONDS}초)") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()[:500]
        raise ImagingError(f"exiftool 실패(종료 코드 {proc.returncode}): {err}")
    try:
        raw: dict[str, Any] = json.loads(proc.stdout)[0]  # Any: exiftool JSON
    except (ValueError, IndexError) as exc:
        raise ImagingError("exiftool 출력 해석 실패") from exc

    iso = _num(raw.get("ISO"))
    return {
        "camera_make": _str(raw.get("Make")),
        "camera_model": _str(raw.get("Model")),
        "lens": _str(raw.get("LensModel")) or _str(raw.get("LensID")) or _str(raw.get("Lens")),
        "f_number": _num(raw.get("FNumber")),
        "exposure_time": _str(raw.get("ExposureTime")),
        "iso": int(iso) if iso is not None else None,
        "focal_length_mm": _num(raw.get("FocalLength")),
        "taken_at": _taken_at(raw.get("DateTimeOriginal"), raw.get("OffsetTimeOriginal")),
        "original_width": _num(raw.get("ImageWidth")),
        "original_height": _num(raw.get("ImageHeight")),
    }
