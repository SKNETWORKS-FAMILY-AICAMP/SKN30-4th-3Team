"""Supabase 생육 커버리지가 부족한 식물을 농사로 채소 문서로 보충합니다.

[전체 실행 순서]
1. 농사로 텃밭채소 원문 수집
   python data/notebooks/NONGSARO_collect_minifarm_vegetable_guides.py

2. 텃밭 전체에 적용되는 general 문서 분리
   python data/notebooks/NONGSARO_extract_garden_general.py

3. Supabase 생육 커버리지 조회 및 부족 식물 normalized JSONL 생성
   python data/notebooks/normalize/coverage_plantGrow_prepare_nongsaro_vegetable_rag.py

4. 청킹
   python data/scripts/chunk_documents.py `
     --input data/interim/nongsaro_minifarm/coverage_plantGrow.nongsaro_vegetable.normalized.jsonl `
     --output data/processed/rag_chunks.nongsaro_vegetable_supplement.jsonl `
     --sources-output data/processed/rag_sources.nongsaro_vegetable_supplement.jsonl `
     --max-chars 1200 `
     --overlap-chars 140

5. 임베딩
   python data/scripts/embed_chunks.py `
     --input data/processed/rag_chunks.nongsaro_vegetable_supplement.jsonl `
     --output data/processed/rag_chunks.nongsaro_vegetable_supplement.embedded.jsonl

6. Supabase 적재 전 확인
   python data/scripts/load_supabase_pgvector.py `
     --chunks data/processed/rag_chunks.nongsaro_vegetable_supplement.embedded.jsonl `
     --sources data/processed/rag_sources.nongsaro_vegetable_supplement.jsonl `
     --dry-run

7. Supabase 적재
   위 명령에서 --dry-run만 제거하여 실행합니다.

[선행조건]
- data/interim/nongsaro_minifarm/nongsaro_minifarm_vegetable_documents.jsonl
- data/interim/nongsaro_minifarm/garden_general.json
- .env의 SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

[선별 기준]
- 병·해충·농약·날씨 청크는 생육 정보 개수에 포함하지 않습니다.
- plant_catalog의 대표 식물명을 기준으로 생육 청크가 2개 이하인 식물을 찾습니다.
- 농사로 문서 제목이 대표 식물명과 같은 경우만 자동 연결합니다.
- 검증된 표기 차이인 셀러리→샐러리, 스피아민트→민트만 별도 연결합니다.
- garden_general.json으로 분리된 텃밭 공통 문서는 제외합니다.

[기본 산출물]
- data/interim/nongsaro_minifarm/
  - coverage_plantGrow.nongsaro_vegetable.normalized.jsonl
  - coverage_plantGrow.nongsaro_vegetable.report.json

[주의]
- Supabase는 읽기만 하며 DB 데이터를 수정하지 않습니다.
- 원본 수집 JSONL과 garden_general.json을 수정하지 않습니다.
- plant_catalog 별칭 전체를 자동 적용하지 않습니다. 짧은 별칭은 다른 식물과
  잘못 연결될 수 있으므로 확실한 표기 차이만 SAFE_TITLE_MAP에 등록합니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import normalize_text, read_jsonl, uuid_for_source_key, write_jsonl  # noqa: E402
from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL  # noqa: E402


INPUT_DIR = DATA_DIR / "interim" / "nongsaro_minifarm"
DEFAULT_DOCUMENTS = INPUT_DIR / "nongsaro_minifarm_vegetable_documents.jsonl"
DEFAULT_GENERAL = INPUT_DIR / "garden_general.json"
DEFAULT_OUTPUT = (
    INPUT_DIR / "coverage_plantGrow.nongsaro_vegetable.normalized.jsonl"
)
DEFAULT_REPORT = INPUT_DIR / "coverage_plantGrow.nongsaro_vegetable.report.json"

SOURCE_KEY = "nongsaro_minifarm_vegetable_supplement"
SOURCE_ID = uuid_for_source_key(SOURCE_KEY)
SOURCE_TITLE = "농사로 텃밭채소 생육정보 보충"

GROWTH_CATEGORIES = {
    "crop_care",
    "indoor_care",
    "ornamental_care",
    "crop_growth_stage",
    "plant_reference",
}

# 실제 데이터베이스 대표 이름으로 연결해도 같은 식물임이 확실한 표기만 둡니다.
SAFE_TITLE_MAP = {
    "셀러리": "샐러리",
    "스피아민트": "민트",
}

TITLE_SUFFIXES = ("텃밭가꾸기",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Supabase 생육 커버리지 부족 식물을 농사로 채소 문서로 보충합니다."
    )
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--general", type=Path, default=DEFAULT_GENERAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--max-existing-chunks",
        type=int,
        default=2,
        help="이 개수 이하의 기존 생육 청크를 가진 식물만 보충합니다.",
    )
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument("--page-size", type=int, default=1000)
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return normalize_text(str(value))


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean(item) for item in value if clean(item)]
    text = clean(value)
    return [text] if text else []


def read_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {path}")
    with path.open("r", encoding="utf-8") as file:
        rows = json.load(file)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"JSON 객체 배열이어야 합니다: {path}")
    return rows


def supabase_rows(table: str, select: str, page_size: int) -> list[dict[str, Any]]:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY가 .env에 필요합니다."
        )

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    rows: list[dict[str, Any]] = []
    start = 0

    while True:
        query = urlencode({"select": select})
        request = Request(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}?{query}",
            headers={
                **headers,
                "Range": f"{start}-{start + page_size - 1}",
            },
        )
        with urlopen(request, timeout=60) as response:
            batch = json.loads(response.read().decode("utf-8"))
        if not isinstance(batch, list):
            raise RuntimeError(f"Supabase {table} 응답이 배열이 아닙니다.")
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size

    return rows


def growth_counts(chunks: Iterable[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in chunks:
        metadata = row.get("metadata") or {}
        if metadata.get("category") not in GROWTH_CATEGORIES:
            continue
        for plant_name in set(string_list(metadata.get("cropOrPlant"))):
            counts[plant_name] += 1
    return counts


def title_plant_candidate(title: str) -> str:
    candidate = clean(title)
    for suffix in TITLE_SUFFIXES:
        if candidate.endswith(suffix):
            candidate = candidate[: -len(suffix)].strip()
            break
    return SAFE_TITLE_MAP.get(candidate, candidate)


def safety_tags_for(text: str) -> list[str]:
    tags = ["general_growing_reference"]
    if any(term in text for term in ("농약", "살충제", "살균제", "제초제")):
        tags.extend(["pesticide_caution", "label_check_required"])
    return tags


def normalized_document(
    row: dict[str, Any],
    plant_name: str,
    existing_chunks: int,
) -> dict[str, Any]:
    title = clean(row.get("title"))
    text = clean(row.get("text"))
    doc_id = clean(row.get("doc_id") or row.get("content_id"))
    if not doc_id:
        raise ValueError(f"doc_id 또는 content_id가 없는 문서입니다: {title}")

    return {
        "doc_id": doc_id,
        "source_id": SOURCE_ID,
        "source_key": SOURCE_KEY,
        "title": title,
        "publisher": clean(row.get("publisher")) or "농촌진흥청 농사로",
        "url": clean(row.get("url") or row.get("source_url")),
        "license": clean(row.get("license")) or "농사로 이용 조건 준수",
        "collected_at": clean(row.get("collected_at")),
        "category": "crop_care",
        "priority": 1 if existing_chunks == 0 else 2,
        "usage_scope": "rag",
        "section": "텃밭 재배 및 생육 관리",
        "crop_or_plant": [plant_name],
        "symptom_keywords": [
            plant_name,
            "재배",
            "생육",
            "텃밭 관리",
        ],
        "safety_tags": safety_tags_for(f"{title}\n{text}"),
        "text": text,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    args = parse_args()
    documents = read_jsonl(args.documents)
    general_rows = read_json_array(args.general)
    general_doc_ids = {clean(row.get("doc_id")) for row in general_rows}

    catalog_rows = supabase_rows("plant_catalog", "name", args.page_size)
    chunk_rows = supabase_rows(
        "rag_chunks",
        "chunk_id,metadata",
        args.page_size,
    )
    catalog_names = {
        clean(row.get("name")) for row in catalog_rows if clean(row.get("name"))
    }
    existing_counts = growth_counts(chunk_rows)

    selected: list[dict[str, Any]] = []
    selected_by_plant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected: Counter[str] = Counter()
    seen_doc_ids: set[str] = set()

    for row in documents:
        doc_id = clean(row.get("doc_id"))
        title = clean(row.get("title"))
        text = clean(row.get("text"))

        if doc_id in general_doc_ids:
            rejected["garden_general"] += 1
            continue
        if len(text) < args.min_chars:
            rejected["short_text"] += 1
            continue

        plant_name = title_plant_candidate(title)
        if plant_name not in catalog_names:
            rejected["not_in_plant_catalog"] += 1
            continue

        count = existing_counts.get(plant_name, 0)
        if count > args.max_existing_chunks:
            rejected["already_sufficient"] += 1
            continue
        if doc_id in seen_doc_ids:
            rejected["duplicate_doc_id"] += 1
            continue

        seen_doc_ids.add(doc_id)
        normalized = normalized_document(row, plant_name, count)
        selected.append(normalized)
        selected_by_plant[plant_name].append(
            {
                "title": title,
                "doc_id": doc_id,
                "text_length": len(text),
            }
        )

    selected.sort(key=lambda row: (existing_counts[row["crop_or_plant"][0]], row["title"]))
    count = write_jsonl(args.output, selected)

    plants = [
        {
            "plant_name": plant_name,
            "existing_growth_chunks": existing_counts.get(plant_name, 0),
            "selected_documents": selected_by_plant[plant_name],
        }
        for plant_name in sorted(
            selected_by_plant,
            key=lambda name: (existing_counts.get(name, 0), name),
        )
    ]
    report = {
        "source": SOURCE_TITLE,
        "source_id": SOURCE_ID,
        "selection_rule": {
            "growth_categories": sorted(GROWTH_CATEGORIES),
            "max_existing_chunks": args.max_existing_chunks,
            "min_chars": args.min_chars,
        },
        "supabase_counts": {
            "plant_catalog": len(catalog_rows),
            "rag_chunks": len(chunk_rows),
        },
        "selected_document_count": count,
        "selected_plant_count": len(plants),
        "plants": plants,
        "rejected_counts": dict(sorted(rejected.items())),
    }
    write_report(args.report, report)

    print(f"Selected {len(plants)} plants / {count} documents.")
    print(f"Normalized JSONL: {args.output}")
    print(f"Coverage report: {args.report}")
    for plant in plants:
        print(
            f"- {plant['plant_name']}: "
            f"existing {plant['existing_growth_chunks']} chunks, "
            f"selected {len(plant['selected_documents'])} documents"
        )


if __name__ == "__main__":
    main()
