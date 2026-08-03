"""농사로 과수 생육 문서를 최종 적재용 JSONL로 정리합니다.

용도
- 기존 과수 v2 정규화 결과에서 적재하지 않을 식물을 제외합니다.
- 확실한 동의어만 plant_catalog의 대표 식물명으로 통일합니다.
- RAG 적재에 필요한 필드와 값이 있는지 검사합니다.
- 별도 보고서 없이 최종 JSONL 하나만 생성합니다.

선행 조건
1. 다음 코드를 먼저 실행해 과수 v2 정규화 파일을 생성합니다.

   python data/notebooks/normalize/coverage_plantGrow_prepare_nongsaro_fruit_v2.py

실행
   python data/notebooks/normalize/prepare_nongsaro_fruit_for_load.py

기본 입력
- data/interim/nongsaro_minifarm/normalizev2/
  coverage_plantGrow.nongsaro_fruit.normalized.v2.jsonl

기본 출력
- data/interim/nongsaro_minifarm/normalizev2/
  coverage_plantGrow.nongsaro_fruit.ready.jsonl

현재 제외 식물
- 온주밀감, 금감, 복숭아
- plant_catalog에 없는 살구, 자두

주의
- 살구와 자두는 각각 개살구나무·서양자두나무로 자동 치환하지 않고
  기존 plant_catalog 식물만 보강하기 위해 제외합니다.
- 이 코드는 Supabase에 접속하거나 데이터를 적재하지 않습니다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = (
    DATA_DIR
    / "interim"
    / "nongsaro_minifarm"
    / "normalizev2"
    / "coverage_plantGrow.nongsaro_fruit.normalized.v2.jsonl"
)
DEFAULT_OUTPUT = (
    DATA_DIR
    / "interim"
    / "nongsaro_minifarm"
    / "normalizev2"
    / "coverage_plantGrow.nongsaro_fruit.ready.jsonl"
)

EXCLUDED_PLANTS = {"온주밀감", "금감", "복숭아", "살구", "자두"}

# 실제 plant_catalog에서 사용하는 대표명으로 확실하게 통일할 수 있는 항목만 둡니다.
CANONICAL_NAMES = {
    "석류": "석류나무",
    "모과": "모과나무",
    "매실": "매실나무",
    "양앵두": "체리",
    "사과": "사과나무",
}

REQUIRED_FIELDS = {
    "doc_id",
    "source_id",
    "title",
    "url",
    "collected_at",
    "license",
    "category",
    "crop_or_plant",
    "text",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="과수 v2 정규화 결과를 최종 적재용 JSONL로 정리합니다."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"JSONL {line_number}행이 올바르지 않습니다.") from error
    return rows


def plant_names(row: dict[str, Any], line_number: int) -> list[str]:
    value = row.get("crop_or_plant")
    if isinstance(value, str):
        names = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        names = [str(item).strip() for item in value if str(item).strip()]
    else:
        names = []

    if not names:
        raise ValueError(f"{line_number}행에 crop_or_plant가 없습니다.")
    return names


def validate_row(row: dict[str, Any], line_number: int) -> None:
    missing = sorted(field for field in REQUIRED_FIELDS if field not in row)
    if missing:
        raise ValueError(f"{line_number}행 필수 필드 누락: {', '.join(missing)}")
    if not str(row.get("doc_id") or "").strip():
        raise ValueError(f"{line_number}행 doc_id가 비어 있습니다.")
    if not str(row.get("text") or "").strip():
        raise ValueError(f"{line_number}행 text가 비어 있습니다.")


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)

    prepared: list[dict[str, Any]] = []
    excluded: list[str] = []
    renamed: list[tuple[str, str]] = []
    seen_doc_ids: set[str] = set()

    for line_number, original in enumerate(rows, start=1):
        validate_row(original, line_number)
        names = plant_names(original, line_number)

        matched_exclusions = sorted(set(names) & EXCLUDED_PLANTS)
        if matched_exclusions:
            excluded.extend(matched_exclusions)
            continue

        normalized_names: list[str] = []
        for name in names:
            canonical = CANONICAL_NAMES.get(name, name)
            if canonical != name:
                renamed.append((name, canonical))
            if canonical not in normalized_names:
                normalized_names.append(canonical)

        row = dict(original)
        row["crop_or_plant"] = normalized_names

        doc_id = str(row["doc_id"])
        if doc_id in seen_doc_ids:
            raise ValueError(f"중복 doc_id입니다: {doc_id}")
        seen_doc_ids.add(doc_id)
        prepared.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as file:
        for row in prepared:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"입력 문서: {len(rows)}건")
    print(f"제외 문서: {len(rows) - len(prepared)}건 ({', '.join(sorted(set(excluded)))})")
    print(f"최종 문서: {len(prepared)}건")
    print(f"대표명 변환: {len(renamed)}건")
    for before, after in sorted(set(renamed)):
        print(f"  - {before} -> {after}")
    print(f"출력: {args.output}")


if __name__ == "__main__":
    main()
