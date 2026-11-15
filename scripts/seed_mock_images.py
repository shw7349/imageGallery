"""목업 이미지 생성 → 로컬 MinIO derived 버킷의 mock/{n}/ 아래에 업로드.

    uv run python scripts/seed_mock_images.py

이미지마다 08단계 파이프라인과 같은 형식을 만든다:
  thumb.webp(긴 변 600px), preview.webp(긴 변 3840px), photo.dzi + photo_files/(256px 타일, overlap 1, WebP)
원본 크기는 app/mock/data.py의 IMAGE_SIZES와 같다(딥줌은 원본의 1/2 크기로 만들어 시간을 줄인다).
"""

import sys
import tempfile
from pathlib import Path

import pyvips

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.mock.data import IMAGE_SIZES, MOCK_PREFIX  # noqa: E402
from app.storage.s3 import S3Storage  # noqa: E402

PALETTE = [
    (230, 126, 34), (52, 152, 219), (46, 204, 113), (155, 89, 182), (241, 196, 15), (231, 76, 60),
    (26, 188, 156), (52, 73, 94), (243, 156, 18), (22, 160, 133), (192, 57, 43), (127, 140, 141),
]  # fmt: skip
CONTENT_TYPES = {".webp": "image/webp", ".dzi": "application/xml"}


def make_image(index: int, width: int, height: int, label_text: str) -> pyvips.Image:
    r, g, b = PALETTE[index % len(PALETTE)]
    x = pyvips.Image.xyz(width, height)
    fx = x[0] / width
    fy = x[1] / height
    # 대각선 그라디언트 + 격자(딥줌 확대 시 디테일 확인용)
    base = (fx + fy) / 2
    grid = ((x[0] % 200) < 2) | ((x[1] % 200) < 2)
    img = (base * r * 0.8 + 30).bandjoin([(1 - base) * g * 0.8 + 30, fy * b * 0.8 + 30]).cast("uchar")
    img = grid.ifthenelse([255, 255, 255], img)
    label = pyvips.Image.text(label_text, dpi=max(150, width // 8), font="sans bold", align="centre")
    lx, ly = (width - label.width) // 2, (height - label.height) // 2
    # 글자 영역(label=마스크)만 흰색으로 칠한다
    patch = img.crop(lx, ly, label.width, label.height)
    patch = (label > 128).ifthenelse([255, 255, 255], patch)
    return img.insert(patch, lx, ly)


def build(index: int, width: int, height: int, out: Path) -> None:
    img = make_image(index, width // 2, height // 2, f"MOCK {index}\n{width}x{height}")
    img = img.copy(interpretation="srgb")
    img.thumbnail_image(600, height=600).webpsave(str(out / "thumb.webp"), Q=80, strip=True)
    img.thumbnail_image(3840, height=3840).webpsave(str(out / "preview.webp"), Q=85, strip=True)
    img.dzsave(str(out / "photo"), tile_size=256, overlap=1, suffix=".webp[Q=80]", strip=True)
    (out / "photo_files" / "vips-properties.xml").unlink(missing_ok=True)  # 공개 CDN에 올리지 않는다


def main() -> int:
    settings = get_settings()
    storage = S3Storage(settings)
    for i, (w, h) in enumerate(IMAGE_SIZES):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            build(i, w, h, out)
            storage.delete_prefix(storage.derived_bucket, f"{MOCK_PREFIX}/{i}/")
            n = storage.upload_directory(
                out,
                storage.derived_bucket,
                f"{MOCK_PREFIX}/{i}/",
                cache_control="public, max-age=300",
                content_type_for=lambda p: CONTENT_TYPES.get(p.suffix, "application/octet-stream"),
            )
        print(f"mock/{i}: {w}x{h} → {n} files")
    print(f"done. 예: {settings.cdn_base_url.rstrip('/')}/{MOCK_PREFIX}/0/thumb.webp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
