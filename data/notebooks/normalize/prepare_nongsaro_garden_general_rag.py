"""농사로 텃밭 공통 관리 JSON을 RAG 공통 문서 JSONL로 변환합니다.

[용도]
- garden_general.json(JSON 배열)을 기존 청킹 파이프라인이 읽을 수 있는
  normalized JSONL 형식으로 변환합니다.
- 개별 작물이 아닌 텃밭 전체 관리 문서이므로 crop_or_plant는 빈 목록으로 둡니다.
- topic을 section으로 보존하여 계획, 농자재, 모종 심기 등의 검색 문맥에 사용합니다.
- Supabase rag_sources의 UUID 컬럼과 호환되는 안정적인 source_id를 생성합니다.

[선행조건]
- data/interim/nongsaro_minifarm/garden_general.json이 있어야 합니다.

[기본 출력]
- data/interim/nongsaro_minifarm/garden_general.normalized.jsonl

[PowerShell 실행]
python data/notebooks/normalize/prepare_nongsaro_garden_general_rag.py

[다음 단계]
python data/scripts/chunk_documents.py `
  --input data/interim/nongsaro_minifarm/garden_general.normalized.jsonl `
  --output data/processed/rag_chunks.nongsaro_general.jsonl `
  --sources-output data/processed/rag_sources.nongsaro_general.jsonl `
  --max-chars 1200 `
  --overlap-chars 140

[주의]
- 원본 garden_general.json은 수정하지 않습니다.
- 출력 파일은 다시 실행하면 동일 경로에 덮어씁니다.
- 이 저장소는 스크립트 자동 등록 방식을 사용하지 않으며 위 경로로 직접 실행합니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import normalize_text, uuid_for_source_key, write_jsonl  # noqa: E402


DEFAULT_INPUT = (
    DATA_DIR / "interim" / "nongsaro_minifarm" / "garden_general.json"
)
DEFAULT_OUTPUT = (
    DATA_DIR
    / "interim"
    / "nongsaro_minifarm"
    / "garden_general.normalized.jsonl"
)
SOURCE_KEY = "nongsaro_minifarm_general"
SOURCE_ID = uuid_for_source_key(SOURCE_KEY)
SOURCE_TITLE = "농사로 텃밭 공통 관리 정보"
DEFAULT_PUBLISHER = "농촌진흥청 농사로"
DEFAULT_LICENSE = "농사로 이용 조건 준수"
PESTICIDE_TERMS = ("농약", "살충제", "살균제", "제초제", "농약용기")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="농사로 텃밭 공통 관리 JSON을 normalized JSONL로 변환합니다."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-chars", type=int, default=80)
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return normalize_text(str(value))


def read_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"최상위 JSON 값은 배열이어야 합니다: {path}")
    if not all(isinstance(row, dict) for row in data):
        raise ValueError(f"배열의 모든 항목은 JSON 객체여야 합니다: {path}")
    return data


def safety_tags_for(text: str) -> list[str]:
    tags = ["general_gardening_reference"]
    if any(term in text for term in PESTICIDE_TERMS):
        tags.extend(["pesticide_caution", "label_check_required"])
    return tags


def normalized_document(row: dict[str, Any]) -> dict[str, Any]:
    title = clean(row.get("title")) or "농사로 텃밭 공통 관리 정보"
    text = clean(row.get("text"))
    topic = clean(row.get("topic")) or "garden_general"
    original_doc_id = clean(row.get("doc_id") or row.get("content_id"))
    if not original_doc_id:
        raise ValueError(f"doc_id 또는 content_id가 없는 문서입니다: {title}")

    return {
        "doc_id": original_doc_id,
        "source_id": SOURCE_ID,
        "source_key": SOURCE_KEY,
        "title": title,
        "publisher": clean(row.get("publisher")) or DEFAULT_PUBLISHER,
        "url": clean(row.get("url") or row.get("source_url")),
        "license": clean(row.get("license")) or DEFAULT_LICENSE,
        "collected_at": clean(row.get("collected_at")),
        "category": "garden_general",
        "priority": 2,
        "usage_scope": "rag",
        "section": topic,
        "crop_or_plant": [],
        "symptom_keywords": ["텃밭 관리", topic],
        "safety_tags": safety_tags_for(f"{title}\n{text}"),
        "text": text,
    }


def main() -> None:
    args = parse_args()
    rows = read_json_array(args.input)
    documents: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    skipped_short = 0
    skipped_duplicate = 0

    for row in rows:
        document = normalized_document(row)
        if len(document["text"]) < args.min_chars:
            skipped_short += 1
            continue
        if document["doc_id"] in seen_doc_ids:
            skipped_duplicate += 1
            continue
        seen_doc_ids.add(document["doc_id"])
        documents.append(document)

    count = write_jsonl(args.output, documents)
    print(f"Normalized {count} documents: {args.output}")
    print(
        "Skipped "
        f"{skipped_short} short documents and "
        f"{skipped_duplicate} duplicate documents."
    )
    print(f"Source: {SOURCE_TITLE} ({SOURCE_ID})")


if __name__ == "__main__":
    main()
