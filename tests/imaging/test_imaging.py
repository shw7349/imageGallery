"""이미지 처리(합성 이미지). 실제 RAW는 SAMPLE_RAW_DIR 환경변수가 있을 때만."""

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import pyvips

from app.imaging import derivatives, exif, pipeline, raw
from app.imaging.errors import ImagingError

EXIFTOOL = shutil.which("exiftool")
needs_exiftool = pytest.mark.skipif(EXIFTOOL is None, reason="exiftool 필요")


def _gradient(width: int, height: int) -> pyvips.Image:
    xyz = pyvips.Image.xyz(width, height)
    r = xyz[0] * (255 / width)
    g = xyz[1] * (255 / height)
    return r.bandjoin([g, (r + g) / 2]).cast("uchar").copy(interpretation="srgb")


def _exiftool(*args: str) -> None:
    assert EXIFTOOL
    subprocess.run([EXIFTOOL, "-overwrite_original", *args], check=True, capture_output=True)  # noqa: S603


def _check_outputs(out: Path, width: int, height: int) -> None:
    thumb = pyvips.Image.new_from_file(str(out / "thumb.webp"))
    preview = pyvips.Image.new_from_file(str(out / "preview.webp"))
    for img, limit in ((thumb, 600), (preview, 3840)):
        assert max(img.width, img.height) == min(limit, max(width, height))
        assert img.bands == 3 and img.format == "uchar" and img.interpretation == "srgb"
        # 가로·세로 비율 유지(반올림 1px 허용)
        assert abs(img.width / img.height - width / height) < 0.01
        assert img.get_typeof("exif-data") == 0  # 메타데이터 제거
    dzi = ET.parse(out / "photo.dzi").getroot()  # noqa: S314  # 직접 만든 파일
    size = dzi.find("{http://schemas.microsoft.com/deepzoom/2008}Size")
    assert size is not None
    assert (int(size.get("Width", 0)), int(size.get("Height", 0))) == (width, height)
    assert dzi.get("TileSize") == "256" and dzi.get("Overlap") == "1" and dzi.get("Format") == "webp"
    assert (out / "photo_files" / "0" / "0_0.webp").exists()
    # 공개될 파일은 dzi와 webp뿐(vips-properties.xml 등 속성 파일 없음)
    assert {p.suffix for p in out.rglob("*") if p.is_file()} == {".dzi", ".webp"}


def test_jpeg(tmp_path: Path) -> None:
    src = tmp_path / "a.jpg"
    _gradient(3000, 2000).jpegsave(str(src), Q=90)
    result = derivatives.make_derivatives(src, tmp_path / "out")
    assert (result.width, result.height) == (3000, 2000)
    _check_outputs(result.out_dir, 3000, 2000)


def test_small_image_is_not_upscaled(tmp_path: Path) -> None:
    src = tmp_path / "small.jpg"
    _gradient(500, 300).jpegsave(str(src))
    result = derivatives.make_derivatives(src, tmp_path / "out")
    _check_outputs(result.out_dir, 500, 300)


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    src = tmp_path / "rotated.jpg"
    img = _gradient(3000, 2000).copy()
    img.set_type(pyvips.GValue.gint_type, "orientation", 6)  # 90° 회전 필요
    img.jpegsave(str(src))
    result = derivatives.make_derivatives(src, tmp_path / "out")
    assert (result.width, result.height) == (2000, 3000)  # 세로로 바뀐다
    _check_outputs(result.out_dir, 2000, 3000)


def test_16bit_tiff(tmp_path: Path) -> None:
    src = tmp_path / "deep.tif"
    # 주의: ushort * 정수는 float이 되므로 다시 ushort로 cast해야 진짜 16bit TIFF가 된다
    (_gradient(2400, 1600).cast("ushort") * 257).cast("ushort").copy(interpretation="rgb16").tiffsave(
        str(src)
    )
    assert pyvips.Image.new_from_file(str(src)).format == "ushort"
    result = derivatives.make_derivatives(src, tmp_path / "out")
    _check_outputs(result.out_dir, 2400, 1600)
    # 16bit → 8bit 변환이 밝기를 보존(검은 화면·포화 아님). 원본 8bit 그라디언트 평균 ≈ 127
    avg = pyvips.Image.new_from_file(str(result.out_dir / "preview.webp")).avg()
    assert 120 < avg < 135


def test_cmyk_tiff(tmp_path: Path) -> None:
    src = tmp_path / "cmyk.tif"
    _gradient(1200, 800).colourspace("cmyk").tiffsave(str(src))
    result = derivatives.make_derivatives(src, tmp_path / "out")
    _check_outputs(result.out_dir, 1200, 800)


def test_png_with_alpha_and_greyscale(tmp_path: Path) -> None:
    rgba = tmp_path / "alpha.png"
    _gradient(800, 600).bandjoin(128).pngsave(str(rgba))
    _check_outputs(derivatives.make_derivatives(rgba, tmp_path / "o1").out_dir, 800, 600)

    grey = tmp_path / "grey.png"
    _gradient(800, 600).colourspace("b-w").pngsave(str(grey))
    _check_outputs(derivatives.make_derivatives(grey, tmp_path / "o2").out_dir, 800, 600)


def test_corrupt_file_raises_imaging_error(tmp_path: Path) -> None:
    src = tmp_path / "broken.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    with pytest.raises(ImagingError):
        derivatives.make_derivatives(src, tmp_path / "out")


@needs_exiftool
def test_exif_extraction_whitelist_and_no_gps(tmp_path: Path) -> None:
    src = tmp_path / "shot.jpg"
    _gradient(600, 400).jpegsave(str(src))
    _exiftool(
        "-Make=SONY",
        "-Model=ILCE-7RM5",
        "-LensModel=FE 24-70mm F2.8 GM II",
        "-FNumber=8",
        "-ExposureTime=1/250",
        "-ISO=100",
        "-FocalLength=35",
        "-DateTimeOriginal=2026:11:01 06:12:30",
        "-OffsetTimeOriginal=+09:00",
        "-GPSLatitude=33.36",
        "-GPSLatitudeRef=N",
        "-GPSLongitude=126.53",
        "-GPSLongitudeRef=E",
        str(src),
    )
    data = exif.extract_exif(src)
    assert data == {
        "camera_make": "SONY",
        "camera_model": "ILCE-7RM5",
        "lens": "FE 24-70mm F2.8 GM II",
        "f_number": 8.0,
        "exposure_time": "1/250",
        "iso": 100,
        "focal_length_mm": 35.0,
        "taken_at": "2026-11-01T06:12:30+09:00",
        "original_width": 600.0,
        "original_height": 400.0,
    }
    assert not any("gps" in k.lower() for k in data)


@needs_exiftool
def test_exif_missing_fields_are_none(tmp_path: Path) -> None:
    src = tmp_path / "plain.png"
    _gradient(100, 100).pngsave(str(src))
    data = exif.extract_exif(src)
    assert data["camera_make"] is None and data["taken_at"] is None
    assert data["original_width"] == 100.0


@needs_exiftool
def test_exif_failure_raises(tmp_path: Path) -> None:
    with pytest.raises(ImagingError):
        exif.extract_exif(tmp_path / "missing.jpg")


@needs_exiftool
def test_pipeline_outputs_and_metadata(tmp_path: Path) -> None:
    src = tmp_path / "shot.jpg"
    _gradient(1600, 1000).jpegsave(str(src))
    _exiftool("-Make=FUJIFILM", "-GPSLatitude=33.36", "-GPSLatitudeRef=N", str(src))
    result = pipeline.process(src, tmp_path / "work")
    assert (result.width, result.height) == (1600, 1000)
    assert result.exif["camera_make"] == "FUJIFILM"
    # 결과 파일에 GPS 등 메타데이터가 남지 않는다
    out = subprocess.run(  # noqa: S603
        [EXIFTOOL or "exiftool", "-GPS:all", "-json", str(result.out_dir / "preview.webp")],
        capture_output=True,
        check=True,
    )
    assert b"GPS" not in out.stdout


def test_raw_detection() -> None:
    assert raw.is_raw(Path("a.ARW")) and raw.is_raw(Path("b.cr3")) and not raw.is_raw(Path("c.tif"))


def test_raw_develop_wiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rawpy 출력(16bit 배열)을 TIFF로 쓰는 부분 확인. 실제 디코딩은 아래 SAMPLE_RAW_DIR 테스트에서."""
    import numpy as np

    array = np.full((300, 450, 3), 30000, dtype=np.uint16)

    class FakeRaw:
        def __enter__(self) -> "FakeRaw":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def postprocess(self, **kwargs: object) -> "np.ndarray[tuple[int, ...], np.dtype[np.uint16]]":
            assert kwargs["output_bps"] == 16 and kwargs["use_camera_wb"] is True
            return array

    monkeypatch.setattr(raw.rawpy, "imread", lambda _path: FakeRaw())
    out = raw.develop_to_tiff(tmp_path / "x.arw", tmp_path / "dev.tif")
    tiff = pyvips.Image.new_from_file(str(out))
    assert (tiff.width, tiff.height, tiff.bands, tiff.format) == (450, 300, 3, "ushort")


def test_raw_decode_error_is_imaging_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.arw"
    bad.write_bytes(b"not raw")
    with pytest.raises(ImagingError):
        raw.develop_to_tiff(bad, tmp_path / "out.tif")


SAMPLE_RAW_DIR = os.environ.get("SAMPLE_RAW_DIR")


@pytest.mark.skipif(not SAMPLE_RAW_DIR, reason="SAMPLE_RAW_DIR에 실제 RAW 파일이 있을 때만")
def test_real_raw_files(tmp_path: Path) -> None:
    assert SAMPLE_RAW_DIR
    files = [p for p in Path(SAMPLE_RAW_DIR).iterdir() if raw.is_raw(p)]
    assert files, "RAW 파일이 없습니다"
    for f in files:
        result = pipeline.process(f, tmp_path / f.stem)
        assert result.width > 1000 and result.height > 1000
        assert (result.out_dir / "photo.dzi").exists()
