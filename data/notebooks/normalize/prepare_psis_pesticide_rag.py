"""PSIS 농약 그룹을 검색용 문서와 구조화 등록정보로 변환합니다.

[용도]
- PSIS_new_table.py가 만든 농약 그룹 JSONL을 RAG 공통 형식으로 변환합니다.
- 검색용 text에는 작물·대상 병해충·제품명·유효성분을 넣습니다.
- 희석배수·사용 시기·횟수·독성 정보는 registrations에 그대로 보존합니다.
- 중복 그룹, 등록 건수 불일치, 필수 필드 누락은 review JSONL에 기록합니다.

[선행조건]
아래 최신 그룹 파일이 있어야 합니다.
- data/interim/all_PSIS/PSIS_result/psis_pesticide_groups.jsonl

필요한 경우 먼저 실행:
python data/notebooks/PSIS_new_table.py

[기본 출력]
- data/interim/normalized/rag_documents.psis_pesticide.normalized.jsonl
- data/interim/normalized/rag_documents.psis_pesticide.review.jsonl

[PowerShell 실행]
python data/notebooks/normalize/prepare_psis_pesticide_rag.py

[다음 단계]
python data/scripts/chunk_documents_ncpms_psis.py `
  --dataset psis-pesticide

[주의]
- registrations는 임베딩 대상 text에 풀어 넣지 않습니다.
- registrations는 농약 전용 업로더가 doc_id로 다시 결합합니다.
- 원본 그룹 파일 또는 변환 규칙이 변경됐을 때만 다시 실행합니다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


DATA_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import normalize_text, read_jsonl, uuid_for_source_key, write_jsonl  # noqa: E402


DEFAULT_INPUT = (
    DATA_DIR
    / "interim"
    / "all_PSIS"
    / "PSIS_result"
    / "psis_pesticide_groups.jsonl"
)
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "normalized"
SOURCE_KEY = "psis_pesticide_safety"
SOURCE_ID = uuid_for_source_key(SOURCE_KEY)
SOURCE_URL = "https://psis.rda.go.kr/psis/cont/contentMain.ps?menuId=PS00381"
PUBLISHER = "농촌진흥청 농약안전정보시스템"
LICENSE = (
    "Korea Open Government License: attribution + no-derivatives shown on guide page"
)
SAFETY_TAGS = [
    "not_diagnosis",
    "expert_check_required",
    "pesticide_caution",
    "label_check_required",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PSIS 농약 그룹 JSONL을 공통 RAG normalized JSONL로 변환합니다."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--output-name",
        default="rag_documents.psis_pesticide.normalized.jsonl",
    )
    parser.add_argument(
        "--review-name",
        default="rag_documents.psis_pesticide.review.jsonl",
    )
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return normalize_text(str(value))


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean(value)
        if text and text not in result:
            result.append(text)
    return result


def build_search_text(
    crop_name: str,
    target_name: str,
    use_type: str,
    pesticide_names: list[str],
    active_ingredients: list[str],
) -> str:
    lines = [
        "문서 유형: PSIS 농약 등록정보",
        f"작물: {crop_name}",
        f"대상 병해충: {target_name}",
    ]
    if use_type:
        lines.append(f"용도: {use_type}")
    if pesticide_names:
        lines.append(f"제품명: {', '.join(pesticide_names)}")
    if active_ingredients:
        lines.append(f"유효성분: {', '.join(active_ingredients)}")
    return "\n".join(lines)


def normalized_document(row: dict[str, Any]) -> dict[str, Any]:
    group_key = clean(row.get("group_key"))
    crop_code = clean(row.get("crop_code"))
    crop_name = clean(row.get("crop_name"))
    target_name = clean(row.get("target_disease_pest"))
    use_type = clean(row.get("use_type"))
    pesticide_names = unique_strings(row.get("pesticide_names") or [])
    active_ingredients = unique_strings(row.get("active_ingredients") or [])
    registrations = [
        registration
        for registration in row.get("registrations") or []
        if isinstance(registration, dict)
    ]
    doc_id = f"psis:pesticide:{group_key}"
    search_text = build_search_text(
        crop_name,
        target_name,
        use_type,
        pesticide_names,
        active_ingredients,
    )

    return {
        "doc_id": doc_id,
        "source_id": SOURCE_ID,
        "source_key": SOURCE_KEY,
        "title": f"{crop_name} {target_name} 등록 농약".strip(),
        "publisher": PUBLISHER,
        "url": SOURCE_URL,
        "license": LICENSE,
        "collected_at": clean(row.get("collected_at")),
        "category": "pesticide_registration",
        "section": "registered_pesticides",
        "priority": 3,
        "usage_scope": "safety_reference_only",
        "crop_or_plant": [crop_name] if crop_name else [],
        "symptom_keywords": unique_strings([target_name, use_type]),
        "safety_tags": SAFETY_TAGS,
        "text": search_text,
        # 아래 필드는 임베딩 대상이 아니라 농약 전용 테이블 적재 시 재결합할 구조화 값입니다.
        "group_key": group_key,
        "crop_code": crop_code,
        "crop_name": crop_name,
        "target_disease_pest": target_name,
        "use_type": use_type,
        "pesticide_names": pesticide_names,
        "active_ingredients": active_ingredients,
        "registration_count": len(registrations),
        "registrations": registrations,
    }


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())
    output_dir = args.output_dir.resolve()
    documents: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []

    group_counts = Counter(clean(row.get("group_key")) for row in rows)
    for group_key, count in group_counts.items():
        if not group_key:
            reviews.append({"issue": "missing_group_key", "count": count})
        elif count > 1:
            reviews.append(
                {
                    "issue": "duplicate_group_key",
                    "group_key": group_key,
                    "count": count,
                }
            )

    for row in rows:
        document = normalized_document(row)
        documents.append(document)
        issues: list[str] = []
        if not document["group_key"]:
            issues.append("missing_group_key")
        if not document["crop_name"]:
            issues.append("missing_crop_name")
        if not document["target_disease_pest"]:
            issues.append("missing_target_disease_pest")
        if not document["registrations"]:
            issues.append("missing_registrations")
        original_count = row.get("registration_count")
        if original_count is not None and original_count != document["registration_count"]:
            issues.append("registration_count_mismatch")
        if len(document["text"]) > 2200:
            issues.append("search_text_exceeds_default_chunk_size")
        if issues:
            reviews.append(
                {
                    "issue": "pesticide_group_review_required",
                    "group_key": document["group_key"],
                    "doc_id": document["doc_id"],
                    "issues": issues,
                    "original_registration_count": original_count,
                    "normalized_registration_count": document["registration_count"],
                    "text_length": len(document["text"]),
                }
            )

    duplicate_doc_ids = [
        doc_id
        for doc_id, count in Counter(doc["doc_id"] for doc in documents).items()
        if count > 1
    ]
    for doc_id in duplicate_doc_ids:
        reviews.append({"issue": "duplicate_doc_id", "doc_id": doc_id})

    output_path = output_dir / args.output_name
    review_path = output_dir / args.review_name
    document_count = write_jsonl(output_path, documents)
    review_count = write_jsonl(review_path, reviews)
    total_registrations = sum(doc["registration_count"] for doc in documents)
    max_text_length = max((len(doc["text"]) for doc in documents), default=0)

    print(f"Wrote {document_count} normalized documents: {output_path}")
    print(f"  registrations={total_registrations}, max_text_length={max_text_length}")
    print(f"Wrote {review_count} review rows: {review_path}")


if __name__ == "__main__":
    main()
