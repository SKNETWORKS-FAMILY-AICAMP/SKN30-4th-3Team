"""국립수목원 식물도감 API 수집 결과를 RAG 적재 전 normalized JSONL로 변환합니다.

용도
----
- API 통합 수집 결과의 식물명을 Supabase ``plant_catalog.name``에 맞춥니다.
- 검색명, API 결과명, 확실한 별칭, 학명 순으로 대표명을 검증합니다.
- plant_catalog에는 없지만 기존 병·해충 RAG에서 정확한 식물명으로 사용 중인
  검증된 6종은 기존 ``metadata.cropOrPlant`` 이름을 그대로 사용합니다.
- ``▶``, ``①``처럼 순서·장식에만 쓰이는 기호를 제거합니다.
- 온도, 길이, 넓이, 농도와 관련된 ``℃``, ``°C``, ``㎝``, ``㎡``, ``%``,
  ``×``, ``~`` 등의 정보성 기호는 보존합니다.
- 기존 청킹·임베딩 파이프라인이 읽는 normalized 문서 스키마로 만듭니다.

선행 조건
---------
1. 다음 수집을 완료합니다.

   python data/notebooks/KNA_collect_plant_growth_api.py --scientific-review --resume

2. 프로젝트 ``.env``에 ``SUPABASE_URL``과 다음 키 중 하나가 있어야 합니다.

   SUPABASE_SERVICE_ROLE_KEY 또는 SUPABASE_ANON_KEY

실행
----
python data/notebooks/normalize/prepare_kna_plant_growth_rag.py

로컬로 내보낸 plant_catalog JSONL을 사용할 때:

python data/notebooks/normalize/prepare_kna_plant_growth_rag.py `
  --catalog-jsonl data/interim/plant_catalog.jsonl

기본 입력
---------
data/interim/coverage_plant_grow_kna_api/plant_growth_api_collected.jsonl

기본 출력
---------
data/interim/coverage_plant_grow_kna_api/normalized/
  kna_plant_growth.normalized.jsonl
  kna_plant_growth.name_mapping.json

주의
----
- 같은 종임을 확인할 수 없는 유사 이름은 억지로 매핑하지 않습니다.
- 미매핑 문서는 normalized JSONL에서 제외하고 name_mapping.json에 남깁니다.
- 원본은 공공누리 제4유형입니다. 원문 파일은 수정하지 않으며 기계적인
  표시 정리만 별도 산출물에 적용합니다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config import (  # noqa: E402
    SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)


DEFAULT_INPUT = (
    DATA_DIR
    / "interim"
    / "coverage_plant_grow_kna_api"
    / "plant_growth_api_collected.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    DATA_DIR / "interim" / "coverage_plant_grow_kna_api" / "normalized"
)
DEFAULT_OUTPUT_NAME = "kna_plant_growth.normalized.jsonl"
DEFAULT_REPORT_NAME = "kna_plant_growth.name_mapping.json"

SOURCE_URL = "https://www.data.go.kr/data/15143513/openapi.do"
SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, SOURCE_URL))
SOURCE_KEY = "kna_plant_growth"
SOURCE_TITLE = "산림청 국립수목원 식물자원 조회 서비스"
LICENSE = "공공누리 제4유형(출처표시·상업적 이용금지·변경금지)"

# plant_catalog에는 없지만 기존 Supabase 병·해충 청크에서 정확한 식물명으로
# 사용 중임을 확인한 항목이다. 유사종 이름으로 바꾸지 않고 원래 이름을 유지한다.
EXISTING_RAG_CANONICAL_NAMES = {
    "감",
    "감초",
    "동부",
    "밤",
    "오갈피",
    "초피",
    "앵두",
}

# 의미를 전달하지 않는 순번·장식용 기호만 대상으로 한다.
# 단위와 범위에 필요한 %, ℃, °, ㎝, ㎡, ×, ~, -, / 등은 포함하지 않는다.
DECORATIVE_MARKS = re.compile(r"[▶▷►▸▹●○■□◆◇※•▪▫]+")
CIRCLED_NUMBERS = re.compile(r"[\u2460-\u2473\u24ea]")
HTML_TAG = re.compile(r"<[^>]+>")
SPACE_RUN = re.compile(r"[ \t\u00a0]+")
BLANK_RUN = re.compile(r"\n{3,}")

DETAIL_FIELDS: tuple[tuple[str, str], ...] = (
    ("plantGnrlNm", "식물명"),
    ("plantSpecsScnm", "학명"),
    ("engNm", "영문명"),
    ("familyKorNm", "과명"),
    ("familyNm", "과 학명"),
    ("genusKorNm", "속명"),
    ("genusNm", "속 학명"),
    ("orplcNm", "원산지"),
    ("dstrb", "국내 분포"),
    ("osDstrb", "국외 분포"),
    ("rrngType", "생육형"),
    ("shpe", "형태"),
    ("grwEvrntDesc", "생육환경"),
    ("farmSpftDesc", "재배 특성"),
    ("brdMthdDesc", "번식 방법"),
    ("bugInfo", "병해충 정보"),
    ("bfofMthod", "방제 방법"),
    ("spft", "특성"),
    ("smlrPlntDesc", "유사 식물"),
    ("prtcPlnDesc", "보호 정보"),
    ("inductionDesc", "도입 정보"),
    ("useMthdDesc", "이용 방법"),
    ("woodDesc", "목재 정보"),
    ("note", "비고"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="국립수목원 API 수집 결과를 plant_catalog 대표명 기준 RAG 문서로 변환합니다."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    parser.add_argument("--report-name", default=DEFAULT_REPORT_NAME)
    parser.add_argument(
        "--catalog-jsonl",
        type=Path,
        help="Supabase 대신 사용할 plant_catalog JSONL(name, species 필드)",
    )
    parser.add_argument("--page-size", type=int, default=1000)
    return parser.parse_args()


def clean_scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalized_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean_scalar(value)).casefold()
    return re.sub(r"[\s\-_/·ㆍ.,()]+", "", text)


def normalized_scientific_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean_scalar(value)).casefold()
    return re.sub(r"[^a-z×]+", "", text)


def clean_text(value: Any) -> str:
    """장식 기호만 제거하고 단위·문장부호·수치 정보는 보존한다."""
    text = html.unescape(clean_scalar(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = HTML_TAG.sub(" ", text)
    text = CIRCLED_NUMBERS.sub("\n", text)
    text = DECORATIVE_MARKS.sub("\n", text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = SPACE_RUN.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
    return BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}의 {line_number}행이 올바른 JSON이 아닙니다.") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path}의 {line_number}행이 JSON 객체가 아닙니다.")
            rows.append(row)
    return rows


def read_catalog_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def fetch_catalog(page_size: int) -> list[dict[str, Any]]:
    key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    if not SUPABASE_URL or not key:
        raise RuntimeError(
            "SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY 또는 SUPABASE_ANON_KEY가 필요합니다."
        )
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        query = urlencode({"select": "id,name,species"})
        request = Request(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/plant_catalog?{query}",
            headers={
                **headers,
                "Range": f"{start}-{start + page_size - 1}",
            },
        )
        with urlopen(request, timeout=60) as response:
            page = json.loads(response.read().decode("utf-8"))
        if not isinstance(page, list):
            raise RuntimeError("Supabase plant_catalog 응답이 배열이 아닙니다.")
        rows.extend(row for row in page if isinstance(row, dict))
        if len(page) < page_size:
            break
        start += page_size
    return rows


def candidate_aliases(candidate: dict[str, Any]) -> list[str]:
    aliases = clean_scalar(candidate.get("aliases"))
    return [
        item.strip()
        for item in re.split(r"[,;/]", aliases)
        if item.strip()
    ]


def catalog_indexes(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    names: dict[str, str] = {}
    species: dict[str, list[str]] = {}
    for row in rows:
        name = clean_scalar(row.get("name"))
        if not name:
            continue
        names.setdefault(normalized_name(name), name)
        scientific_key = normalized_scientific_name(row.get("species"))
        if scientific_key:
            species.setdefault(scientific_key, [])
            if name not in species[scientific_key]:
                species[scientific_key].append(name)
    return names, species


def map_catalog_name(
    row: dict[str, Any],
    catalog_names: dict[str, str],
    catalog_species: dict[str, list[str]],
) -> tuple[str, str]:
    candidate = row.get("candidate") or {}
    checks = [
        ("검색명 일치", clean_scalar(row.get("query_name"))),
        ("API 결과명 일치", clean_scalar(candidate.get("result_name"))),
    ]
    checks.extend(("API 별칭 일치", alias) for alias in candidate_aliases(candidate))

    for method, value in checks:
        matched = catalog_names.get(normalized_name(value))
        if matched:
            return matched, method

    scientific_key = normalized_scientific_name(candidate.get("scientific_name"))
    scientific_matches = catalog_species.get(scientific_key, [])
    if len(scientific_matches) == 1:
        return scientific_matches[0], "학명 일치"
    query_name = clean_scalar(row.get("query_name"))
    if query_name in EXISTING_RAG_CANONICAL_NAMES:
        return query_name, "기존 병해충 RAG 이름 일치"
    return "", "미매핑"


def detail_for(row: dict[str, Any]) -> dict[str, Any]:
    candidate = row.get("candidate") or {}
    detail = candidate.get("detail")
    return detail if isinstance(detail, dict) else {}


def document_text(canonical_name: str, row: dict[str, Any]) -> str:
    detail = detail_for(row)
    sections: list[str] = [f"식물: {canonical_name}"]
    used_values: set[str] = set()
    for field, label in DETAIL_FIELDS:
        value = clean_text(detail.get(field))
        if not value or value in used_values:
            continue
        used_values.add(value)
        sections.append(f"{label}\n{value}")
    return "\n\n".join(sections).strip()


def safety_tags_for(text: str) -> list[str]:
    tags = ["general_growing_reference"]
    if any(term in text for term in ("농약", "살충제", "살균제", "제초제", "배액", "살포")):
        tags.extend(["pesticide_caution", "label_check_required"])
    if any(term in text for term in ("약용", "복용", "용법", "용량", "치료")):
        tags.append("medicinal_claim_caution")
    return tags


def normalized_document(
    row: dict[str, Any],
    canonical_name: str,
    mapping_method: str,
) -> dict[str, Any]:
    candidate = row.get("candidate") or {}
    plant_id = clean_scalar(row.get("selected_plant_id") or candidate.get("plant_id"))
    if not plant_id:
        raise ValueError(f"plant_id가 없습니다: {row.get('query_name')}")
    text = document_text(canonical_name, row)
    if len(text) < 80:
        raise ValueError(f"본문이 너무 짧습니다: {row.get('query_name')}")

    collected_at = clean_scalar(row.get("searched_at"))
    return {
        "doc_id": f"kna_plant:{plant_id}",
        "source_id": SOURCE_ID,
        "source_key": SOURCE_KEY,
        "title": f"{canonical_name} 생육·식물도감 정보",
        "publisher": "산림청 국립수목원",
        "url": SOURCE_URL,
        "license": clean_scalar(row.get("license")) or LICENSE,
        "collected_at": collected_at,
        "category": "plant_growth",
        "priority": 2,
        "usage_scope": "rag",
        "section": "생육·재배·형태·병해충",
        "crop_or_plant": [canonical_name],
        "symptom_keywords": [canonical_name, "생육", "재배", "식물도감"],
        "safety_tags": safety_tags_for(text),
        "text": text,
        "kna_plant_id": plant_id,
        "scientific_name": clean_scalar(candidate.get("scientific_name")),
        "original_query_name": clean_scalar(row.get("query_name")),
        "catalog_mapping_method": mapping_method,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    args = parse_args()
    collected = read_jsonl(args.input)
    catalog = (
        read_catalog_jsonl(args.catalog_jsonl)
        if args.catalog_jsonl
        else fetch_catalog(args.page_size)
    )
    catalog_names, catalog_species = catalog_indexes(catalog)

    documents: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    documents_by_id: dict[str, dict[str, Any]] = {}
    duplicate_documents: list[dict[str, Any]] = []

    for row in collected:
        candidate = row.get("candidate") or {}
        canonical_name, method = map_catalog_name(
            row,
            catalog_names,
            catalog_species,
        )
        mapping = {
            "query_name": clean_scalar(row.get("query_name")),
            "api_result_name": clean_scalar(candidate.get("result_name")),
            "scientific_name": clean_scalar(candidate.get("scientific_name")),
            "catalog_name": canonical_name or None,
            "mapping_method": method,
        }
        if not canonical_name:
            mapping["api_aliases"] = candidate_aliases(candidate)
            unmatched.append(mapping)
            continue

        document = normalized_document(row, canonical_name, method)
        existing = documents_by_id.get(document["doc_id"])
        if existing is not None:
            original_names = existing.setdefault(
                "original_query_names",
                [existing.pop("original_query_name")],
            )
            if document["original_query_name"] not in original_names:
                original_names.append(document["original_query_name"])
            duplicate_documents.append(
                {
                    "doc_id": document["doc_id"],
                    "catalog_name": canonical_name,
                    "merged_query_name": document["original_query_name"],
                }
            )
        else:
            documents_by_id[document["doc_id"]] = document
        mappings.append(mapping)

    documents = list(documents_by_id.values())
    documents.sort(key=lambda row: row["crop_or_plant"][0])
    mappings.sort(key=lambda row: row["query_name"])
    unmatched.sort(key=lambda row: row["query_name"])

    output_path = args.output_dir / args.output_name
    report_path = args.output_dir / args.report_name
    write_jsonl(output_path, documents)
    write_json(
        report_path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_count": len(collected),
            "catalog_count": len(catalog),
            "normalized_count": len(documents),
            "unmatched_count": len(unmatched),
            "duplicate_document_count": len(duplicate_documents),
            "duplicate_documents": duplicate_documents,
            "mappings": mappings,
            "unmatched": unmatched,
            "cleaning_rule": {
                "removed": ["장식용 화살표·도형", "원문자 순번 ①~⑳"],
                "preserved_examples": ["℃", "°C", "%", "㎝", "cm", "㎡", "m²", "×", "~"],
            },
        },
    )

    print(f"입력 수집 문서: {len(collected)}건")
    print(f"plant_catalog: {len(catalog)}건")
    print(f"정규화 완료: {len(documents)}건")
    print(f"미매핑 제외: {len(unmatched)}건")
    for row in unmatched:
        print(
            f"  - {row['query_name']} "
            f"(API 결과명: {row['api_result_name']}, 학명: {row['scientific_name']})"
        )
    print(f"출력: {output_path}")
    print(f"매핑 보고서: {report_path}")


if __name__ == "__main__":
    main()
