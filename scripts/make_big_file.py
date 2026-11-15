"""테스트용 대용량 TIFF 생성 (pyvips, 스트리밍으로 만들어 메모리를 거의 쓰지 않는다).

사용: python scripts/make_big_file.py --size 300M out.tif
무압축 8bit RGB라서 파일 크기 ≈ 너비 × 높이 × 3. 노이즈 + 그라디언트라 압축·중복 제거가 안 된다.
"""

import argparse
import math
import sys
from pathlib import Path

import pyvips

UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3}


def parse_size(text: str) -> int:
    text = text.strip().upper()
    if text[-1] in UNITS:
        return int(float(text[:-1]) * UNITS[text[-1]])
    return int(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--size", default="300M", help="목표 크기 (예: 300M, 1G)")
    args = parser.parse_args()

    target = parse_size(args.size)
    side = max(16, math.isqrt(target // 3))
    width, height = side * 3 // 2, side * 2 // 3  # 3:2 비율(카메라 센서)

    noise = pyvips.Image.gaussnoise(width, height, mean=128, sigma=40)
    gradient = pyvips.Image.xyz(width, height)[0] * (255 / width)
    rgb = (noise.bandjoin([gradient, (noise + gradient) / 2])).cast("uchar")
    rgb = rgb.copy(interpretation="srgb")
    rgb.tiffsave(str(args.out), compression="none", bigtiff=target > 3 * 1024**3)

    size = args.out.stat().st_size
    print(f"wrote {args.out} {width}x{height} {size / 1024**2:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
