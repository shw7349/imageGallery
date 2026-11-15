"""파이프라인 벤치마크: 처리 시간과 최대 메모리(RSS)를 잰다.

    uv run python scripts/bench_pipeline.py big.tif
    uv run python scripts/bench_pipeline.py big.tif --keep out/    # 결과를 남겨 viewer.html로 확인

워커와 같은 libvips 제한(VIPS_CONCURRENCY, VIPS_CACHE_MAX_MB 설정값)을 적용한다.
"""

import argparse
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.imaging import derivatives, pipeline  # noqa: E402

RSS_UNIT = 1 if sys.platform == "darwin" else 1024  # macOS는 바이트, Linux는 KB


def _peak_mb(who: int) -> float:
    return resource.getrusage(who).ru_maxrss * RSS_UNIT / 1024**2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--keep", type=Path, help="결과(out/)를 이 디렉터리로 복사")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--cache-mb", type=int, default=100)
    args = parser.parse_args()

    derivatives.configure(args.concurrency, args.cache_mb)
    size_mb = args.path.stat().st_size / 1024**2
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.monotonic()
        result = pipeline.process(args.path, Path(tmp))
        seconds = time.monotonic() - t0
        files = sum(1 for p in result.out_dir.rglob("*") if p.is_file())
        out_mb = sum(p.stat().st_size for p in result.out_dir.rglob("*") if p.is_file()) / 1024**2
        if args.keep:
            shutil.copytree(result.out_dir, args.keep, dirs_exist_ok=True)

    print(f"입력        {args.path.name} ({size_mb:.0f}MB, {result.width}x{result.height})")
    print(f"처리 시간    {seconds:.1f}s")
    children = _peak_mb(resource.RUSAGE_CHILDREN)
    print(f"최대 메모리  {_peak_mb(resource.RUSAGE_SELF):.0f}MB (exiftool 등 자식 {children:.0f}MB)")
    print(f"결과        파일 {files}개, {out_mb:.1f}MB")
    if args.keep:
        print(
            f"뷰어        cd {args.keep} && python3 -m http.server 8090 → "
            "http://127.0.0.1:8090/viewer.html?dzi=photo.dzi (scripts/viewer.html을 같은 곳에 복사)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
