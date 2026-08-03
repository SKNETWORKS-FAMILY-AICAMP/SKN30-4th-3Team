"""농사로 과수 텃밭 문서 14건을 표 제거 후 RAG normalized v2로 변환합니다.

[전체 실행 순서]
1. 과수 텃밭 문서 수집
   python data/notebooks/NONGSARO_collect_minifarm_guides.py

2. 표 제거·본문 전처리·RAG 정규화
   python data/notebooks/normalize/coverage_plantGrow_prepare_nongsaro_fruit_v2.py

3. 청킹
   python data/scripts/chunk_documents.py `
     --input data/interim/nongsaro_minifarm/normalizev2/coverage_plantGrow.nongsaro_fruit.normalized.v2.jsonl `
     --output data/processed/rag_chunks.nongsaro_fruit.jsonl `
     --sources-output data/processed/rag_sources.nongsaro_fruit.jsonl `
     --max-chars 1200 `
     --overlap-chars 140

4. 임베딩
   python data/scripts/embed_chunks.py `
     --input data/processed/rag_chunks.nongsaro_fruit.jsonl `
     --output data/processed/rag_chunks.nongsaro_fruit.embedded.jsonl

[선행조건]
- data/interim/nongsaro_minifarm/nongsaro_minifarm_documents.jsonl

[기본 출력]
- data/interim/nongsaro_minifarm/normalizev2/
  - coverage_plantGrow.nongsaro_fruit.normalized.v2.jsonl
  - coverage_plantGrow.nongsaro_fruit.preprocess_report.json

[표 제거 기준]
- `표 1. ...`, `... 표`, `... 보여주는 표`, `... 나타내는 표`를 표 시작으로 봅니다.
- 표 시작부터 다음 번호 절 또는 한글 소항목 절까지 제거합니다.
- HTML table 경계가 사라진 텍스트이므로 제거 범위를 문서별 리포트에 기록합니다.
- 출력 후 preprocess_report의 removed_table_chars가 큰 문서는 검토해야 합니다.

[주의]
- 원본 수집 JSONL은 수정하지 않습니다.
- 과수명은 확정된 문서 제목 매핑만 사용합니다.
- 기존 vegetable v2와 동일한 UI·학명·농약·중복 제목 전처리를 재사용합니다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[2]
NORMALIZE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = DATA_DIR / "scripts"
for module_dir in (NORMALIZE_DIR, SCRIPTS_DIR):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))

from common import normalize_text, read_jsonl, uuid_for_source_key, write_jsonl  # noqa: E402
from coverage_plantGrow_preprocess_v2 import preprocess_text  # noqa: E402


INPUT_DIR = DATA_DIR / "interim" / "nongsaro_minifarm"
DEFAULT_INPUT = INPUT_DIR / "nongsaro_minifarm_documents.jsonl"
DEFAULT_OUTPUT_DIR = INPUT_DIR / "normalizev2"
DEFAULT_OUTPUT_NAME = "coverage_plantGrow.nongsaro_fruit.normalized.v2.jsonl"
DEFAULT_REPORT_NAME = "coverage_plantGrow.nongsaro_fruit.preprocess_report.json"

SOURCE_KEY = "nongsaro_minifarm_fruit"
SOURCE_ID = uuid_for_source_key(SOURCE_KEY)

TITLE_TO_PLANT = {
    "블루베리 텃밭가꾸기": "블루베리",
    "석류 텃밭가꾸기": "석류",
    "살구 텃밭가꾸기": "살구",
    "복숭아 텃밭가꾸기": "복숭아",
    "모과 텃밭가꾸기": "모과",
    "매실 텃밭가꾸기": "매실",
    "가정에서 한라봉 기르기": "한라봉",
    "가정에서 온주밀감 기르기": "온주밀감",
    "가정에서 금감 기르기": "금감",
    "양앵두 텃밭가꾸기": "양앵두",
    "자두 텃밭가꾸기": "자두",
    "포도 텃밭가꾸기": "포도",
    "사과를 이용한 텃밭가꾸기": "사과",
    "참다래를 이용한 텃밭가꾸기": "참다래",
}

NUMBERED_SECTION_RE = re.compile(r"^\s*\d+[.)]\s*\S.*$")
KOREAN_SUBSECTION_RE = re.compile(r"^\s*[가-하][.)]\s*\S.*$")
PAREN_SECTION_RE = re.compile(r"^\s*\d+\)\s*\S.*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="농사로 과수 텃밭 문서에서 표를 제거하고 RAG normalized v2를 생성합니다."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    parser.add_argument("--report-name", default=DEFAULT_REPORT_NAME)
    return parser.parse_args()


def is_table_marker(paragraph: str) -> bool:
    value = normalize_text(paragraph)
    if not value or len(value) > 240:
        return False
    if re.match(r"^표\s*\d+[.)]", value):
        return True
    if value.endswith("보여주는 표") or value.endswith("나타내는 표"):
        return True
    # 수집기가 만든 `열1, 열2, 열3 표` 형식의 요약행입니다.
    return value.endswith(" 표") and "," in value


def is_structural_heading(paragraph: str) -> bool:
    value = normalize_text(paragraph)
    return bool(
        NUMBERED_SECTION_RE.match(value)
        or KOREAN_SUBSECTION_RE.match(value)
        or PAREN_SECTION_RE.match(value)
    )


def remove_table_blocks(text: str) -> tuple[str, list[dict[str, Any]]]:
    """표 표식부터 다음 구조 절 직전까지 제거합니다."""
    paragraphs = re.split(r"\n\s*\n", text)
    kept: list[str] = []
    removals: list[dict[str, Any]] = []
    in_table = False
    current: list[str] = []
    marker = ""

    def close_table() -> None:
        nonlocal current, marker
        if not current:
            return
        removed_text = "\n\n".join(current)
        removals.append(
            {
                "marker": marker,
                "removed_paragraphs": len(current),
                "removed_chars": len(removed_text),
            }
        )
        current = []
        marker = ""

    for paragraph in paragraphs:
        value = paragraph.strip()
        if not in_table and is_table_marker(value):
            in_table = True
            marker = normalize_text(value)[:180]
            current.append(value)
            continue

        if in_table:
            if is_structural_heading(value):
                close_table()
                in_table = False
                kept.append(value)
            else:
                current.append(value)
            continue

        kept.append(value)

    if in_table:
        close_table()

    return "\n\n".join(part for part in kept if part), removals


def normalized_document(
    row: dict[str, Any],
    plant_name: str,
    text: str,
) -> dict[str, Any]:
    title = normalize_text(str(row.get("title") or plant_name))
    return {
        "doc_id": row.get("doc_id") or f"nongsaro_fruit:{row.get('content_id')}",
        "source_id": SOURCE_ID,
        "source_key": SOURCE_KEY,
        "title": title,
        "publisher": row.get("publisher") or "농촌진흥청 농사로",
        "url": row.get("url") or row.get("source_url") or "",
        "license": row.get("license") or "농사로 이용 조건 준수",
        "collected_at": row.get("collected_at") or "",
        "category": "crop_care",
        "priority": 2,
        "usage_scope": "rag",
        "section": "과수 텃밭 재배 및 생육 관리",
        "crop_or_plant": [plant_name],
        "symptom_keywords": [plant_name, "과수", "재배", "생육", "텃밭 관리"],
        "safety_tags": ["general_growing_reference", "pesticide_caution"],
        "text": text,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    output_path = args.output_dir / args.output_name
    report_path = args.output_dir / args.report_name

    documents: list[dict[str, Any]] = []
    document_reports: list[dict[str, Any]] = []
    total_changes: Counter[str] = Counter()
    seen_doc_ids: set[str] = set()

    for row in rows:
        title = normalize_text(str(row.get("title") or ""))
        plant_name = TITLE_TO_PLANT.get(title)
        if not plant_name:
            raise ValueError(f"확정 과수명 매핑이 없는 제목입니다: {title}")

        original_text = str(row.get("text") or "")
        without_tables, table_removals = remove_table_blocks(original_text)
        cleaned_text, changes = preprocess_text(without_tables)
        total_changes.update(changes)
        total_changes["table_blocks"] += len(table_removals)
        total_changes["table_chars"] += sum(
            item["removed_chars"] for item in table_removals
        )

        document = normalized_document(row, plant_name, cleaned_text)
        if document["doc_id"] in seen_doc_ids:
            raise ValueError(f"중복 doc_id입니다: {document['doc_id']}")
        if not cleaned_text:
            raise ValueError(f"표 제거 후 본문이 비었습니다: {title}")
        seen_doc_ids.add(document["doc_id"])
        documents.append(document)
        document_reports.append(
            {
                "doc_id": document["doc_id"],
                "title": title,
                "plant_name": plant_name,
                "before_chars": len(original_text),
                "after_chars": len(cleaned_text),
                "removed_ratio": round(
                    1 - (len(cleaned_text) / max(1, len(original_text))),
                    4,
                ),
                "table_removals": table_removals,
                "other_changes": dict(changes),
            }
        )

    count = write_jsonl(output_path, documents)
    write_report(
        report_path,
        {
            "input": str(args.input),
            "output": str(output_path),
            "document_count": count,
            "total_changes": dict(total_changes),
            "documents": document_reports,
        },
    )

    print(f"Prepared {count} fruit documents: {output_path}")
    print(f"Report: {report_path}")
    print(f"Removed table blocks: {total_changes['table_blocks']}")
    print(f"Removed table characters: {total_changes['table_chars']}")


if __name__ == "__main__":
    main()
