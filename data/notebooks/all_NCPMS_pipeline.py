# 실행 시 NCPMS 내의 모든 농작물 리스트를 불러오고
# 이 이름대로 병/해충 목록을 검색하며
# 결과는 interim/all_NCPMS 에 저장됩니다

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run NCPMS plant discovery, disease, and insect collection in order."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--include-images", action="store_true")
    parser.add_argument("--limit-plants", type=int)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=10_000)
    return parser.parse_args()


def run(script: str, extra: list[str]) -> None:
    command = [sys.executable, str(HERE / script), *extra]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=HERE.parents[1])


def main() -> int:
    args = parse_args()
    common = [
        "--delay",
        str(args.delay),
        "--page-size",
        str(args.page_size),
        "--max-pages",
        str(args.max_pages),
    ]
    if args.dry_run:
        common.append("--dry-run")

    run("all_NCPMS_plantCode.py", common)

    detail_options = list(common)
    if args.resume:
        detail_options.append("--resume")
    if args.include_images:
        detail_options.append("--include-images")
    if args.limit_plants:
        detail_options.extend(["--limit-plants", str(args.limit_plants)])

    run("all_NCPMS_disease.py", detail_options)
    run("all_NCPMS_insect.py", detail_options)
    run("validate_all_NCPMS.py", ["--allow-empty"] if args.dry_run else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
