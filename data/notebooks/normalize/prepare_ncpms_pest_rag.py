"""NCPMS 병·해충 수집 결과를 RAG 공통 문서 형식으로 변환합니다.

[용도]
- 병 상세와 식물-병 관계를 sick_key로 결합합니다.
- 해충 상세와 식물-해충 관계를 insect_key로 결합합니다.
- 기존 청킹·임베딩 파이프라인이 읽을 수 있는 normalized JSONL을 만듭니다.
- 관계 누락, 중복 키, 미검증 항목은 review JSONL에 기록합니다.

[선행조건]
아래 4개 최신 파일이 data/interim/all_NCPMS에 있어야 합니다.
- disease_details.jsonl
- plant_disease_relations.jsonl
- insect_details.jsonl
- plant_insect_relations.jsonl

[기본 출력]
- data/interim/normalized/rag_documents.ncpms_pest.normalized.jsonl
- data/interim/normalized/rag_documents.ncpms_pest.review.jsonl

[PowerShell 실행]
python data/notebooks/normalize/prepare_ncpms_pest_rag.py

[다음 단계]
python data/scripts/chunk_documents_ncpms_psis.py `
  --dataset ncpms-pest

[주의]
- 원본 4개 JSONL을 수정하지 않습니다.
- 반복되는 공통 주의 문구는 text에서 제거하지만 safety_tags는 유지합니다.
- all_NCPMS 원본 또는 변환 규칙이 변경됐을 때만 다시 실행합니다.
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


DEFAULT_INPUT_DIR = DATA_DIR / "interim" / "all_NCPMS"
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "normalized"
SOURCE_KEY = "ncpms_pest_reference"
SOURCE_ID = uuid_for_source_key(SOURCE_KEY)
SOURCE_URL = "https://ncpms.rda.go.kr/npms/OpenApiInfo.np"
PUBLISHER = "농촌진흥청 국가농작물병해충관리시스템"
LICENSE = "API approval required; verify terms"
SAFETY_TAGS = [
    "not_diagnosis",
    "expert_check_required",
    "pesticide_caution",
]
STANDARD_CAUTION = (
    "주의:\n"
    "이 정보는 확정 진단이 아닌 관찰 참고용입니다. "
    "농약 사용 전 제품 라벨과 농촌진흥청 최신 등록정보를 확인하세요."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NCPMS 병·해충 상세/관계 JSONL을 공통 RAG normalized JSONL로 변환합니다."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--output-name",
        default="rag_documents.ncpms_pest.normalized.jsonl",
    )
    parser.add_argument(
        "--review-name",
        default="rag_documents.ncpms_pest.review.jsonl",
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


def build_text(sections: Iterable[tuple[str, Any]]) -> str:
    blocks: list[str] = []
    for label, value in sections:
        text = clean(value)
        if text:
            blocks.append(f"{label}:\n{text}")
    return "\n\n".join(blocks)


def remove_standard_caution(text: str) -> str:
    """RAG 본문에서 반복되는 공통 주의 문구를 제거합니다."""
    return normalize_text(text.replace(STANDARD_CAUTION, ""))


def index_unique(
    rows: list[dict[str, Any]],
    key_name: str,
    source_name: str,
    reviews: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    counts = Counter(clean(row.get(key_name)) for row in rows if clean(row.get(key_name)))
    for key, count in counts.items():
        if count > 1:
            reviews.append(
                {
                    "issue": "duplicate_detail_key",
                    "source": source_name,
                    "key_name": key_name,
                    "key": key,
                    "count": count,
                }
            )
    return {
        clean(row.get(key_name)): row
        for row in rows
        if clean(row.get(key_name))
    }


def disease_document(
    relation: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    crop_name = clean(relation.get("crop_name") or detail.get("crop_name"))
    disease_name = clean(
        relation.get("disease_name_ko") or detail.get("disease_name_ko")
    )
    sick_key = clean(relation.get("sick_key") or detail.get("sick_key"))
    crop_code = clean(relation.get("crop_code"))
    pathogen_names = unique_strings(
        [
            detail.get("pathogen_name"),
            *[
                pathogen.get("name")
                for pathogen in detail.get("pathogens") or []
                if isinstance(pathogen, dict)
            ],
        ]
    )
    symptom_keywords = unique_strings(
        [disease_name, detail.get("pathogen_name"), detail.get("disease_name_en")]
    )
    doc_id = f"ncpms:disease:{crop_code or crop_name}:{sick_key}"
    text = remove_standard_caution(build_text(
        [
            ("문서 유형", "NCPMS 병해 정보"),
            ("작물", crop_name),
            ("병명", disease_name),
            ("영문명", detail.get("disease_name_en")),
            ("병원체", ", ".join(pathogen_names)),
            ("관찰 가능한 증상", detail.get("symptoms")),
            ("발생 조건", detail.get("development_condition")),
            ("감염 경로", detail.get("infection_route")),
            ("예방 및 일반 방제", detail.get("prevention_method")),
            ("생물적 방제", detail.get("biological_control")),
            ("화학적 방제 참고", detail.get("chemical_control")),
            (
                "주의",
                "이 정보는 확정 진단이 아닌 관찰 참고용입니다. "
                "농약 사용 전 제품 라벨과 농촌진흥청 최신 등록정보를 확인하세요.",
            ),
        ]
    ))
    return {
        "doc_id": doc_id,
        "source_id": SOURCE_ID,
        "source_key": SOURCE_KEY,
        "title": f"{crop_name} {disease_name}".strip(),
        "publisher": PUBLISHER,
        "url": SOURCE_URL,
        "license": LICENSE,
        "collected_at": clean(
            detail.get("collected_at") or relation.get("collected_at")
        ),
        "category": "disease",
        "section": "disease_reference",
        "priority": 2,
        "usage_scope": "reference_only",
        "crop_or_plant": [crop_name] if crop_name else [],
        "symptom_keywords": symptom_keywords,
        "safety_tags": SAFETY_TAGS,
        "text": text,
        "ncpms_key": sick_key,
        "crop_code": crop_code,
        "pest_type": "disease",
        "pest_name": disease_name,
        "review_status": clean(relation.get("review_status")),
        "match_method": clean(relation.get("match_method")),
    }


def insect_document(
    relation: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    crop_name = clean(relation.get("crop_name") or detail.get("crop_name"))
    insect_name = clean(
        relation.get("insect_name_ko") or detail.get("insect_name_ko")
    )
    insect_key = clean(relation.get("insect_key") or detail.get("insect_key"))
    crop_code = clean(relation.get("crop_code"))
    natural_enemies = unique_strings(
        [
            detail.get("natural_enemy"),
            *[
                enemy.get("name")
                for enemy in detail.get("natural_enemies") or []
                if isinstance(enemy, dict)
            ],
        ]
    )
    symptom_keywords = unique_strings(
        [insect_name, detail.get("family"), detail.get("order")]
    )
    doc_id = f"ncpms:insect:{crop_code or crop_name}:{insect_key}"
    text = remove_standard_caution(build_text(
        [
            ("문서 유형", "NCPMS 해충 정보"),
            ("작물", crop_name),
            ("해충명", insect_name),
            ("학명", detail.get("scientific_name")),
            ("분류", " / ".join(unique_strings([detail.get("order"), detail.get("family")]))),
            ("피해 증상", detail.get("damage")),
            ("형태", detail.get("morphology")),
            ("생태", detail.get("ecology")),
            ("분포", detail.get("distribution")),
            ("천적", ", ".join(natural_enemies)),
            ("예방 및 일반 방제", detail.get("prevention_method")),
            (
                "주의",
                "이 정보는 확정 진단이 아닌 관찰 참고용입니다. "
                "농약 사용 전 제품 라벨과 농촌진흥청 최신 등록정보를 확인하세요.",
            ),
        ]
    ))
    return {
        "doc_id": doc_id,
        "source_id": SOURCE_ID,
        "source_key": SOURCE_KEY,
        "title": f"{crop_name} {insect_name}".strip(),
        "publisher": PUBLISHER,
        "url": SOURCE_URL,
        "license": LICENSE,
        "collected_at": clean(
            detail.get("collected_at") or relation.get("collected_at")
        ),
        "category": "insect",
        "section": "insect_reference",
        "priority": 2,
        "usage_scope": "reference_only",
        "crop_or_plant": [crop_name] if crop_name else [],
        "symptom_keywords": symptom_keywords,
        "safety_tags": SAFETY_TAGS,
        "text": text,
        "ncpms_key": insect_key,
        "crop_code": crop_code,
        "pest_type": "insect",
        "pest_name": insect_name,
        "review_status": clean(relation.get("review_status")),
        "match_method": clean(relation.get("match_method")),
    }


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    reviews: list[dict[str, Any]] = []

    disease_details = read_jsonl(input_dir / "disease_details.jsonl")
    disease_relations = read_jsonl(input_dir / "plant_disease_relations.jsonl")
    insect_details = read_jsonl(input_dir / "insect_details.jsonl")
    insect_relations = read_jsonl(input_dir / "plant_insect_relations.jsonl")

    disease_by_key = index_unique(
        disease_details, "sick_key", "disease_details", reviews
    )
    insect_by_key = index_unique(
        insect_details, "insect_key", "insect_details", reviews
    )

    documents: list[dict[str, Any]] = []
    used_disease_keys: set[str] = set()
    used_insect_keys: set[str] = set()

    for relation in disease_relations:
        key = clean(relation.get("sick_key"))
        detail = disease_by_key.get(key)
        if not key or detail is None:
            reviews.append(
                {
                    "issue": "missing_disease_detail",
                    "key": key,
                    "relation": relation,
                }
            )
            continue
        document = disease_document(relation, detail)
        documents.append(document)
        used_disease_keys.add(key)
        if (
            not document["crop_or_plant"]
            or not document["pest_name"]
            or relation.get("review_status") not in {None, "", "verified"}
        ):
            reviews.append(
                {
                    "issue": "disease_document_review_required",
                    "doc_id": document["doc_id"],
                    "crop_or_plant": document["crop_or_plant"],
                    "pest_name": document["pest_name"],
                    "review_status": relation.get("review_status"),
                }
            )

    for relation in insect_relations:
        key = clean(relation.get("insect_key"))
        detail = insect_by_key.get(key)
        if not key or detail is None:
            reviews.append(
                {
                    "issue": "missing_insect_detail",
                    "key": key,
                    "relation": relation,
                }
            )
            continue
        document = insect_document(relation, detail)
        documents.append(document)
        used_insect_keys.add(key)
        if (
            not document["crop_or_plant"]
            or not document["pest_name"]
            or relation.get("review_status") not in {None, "", "verified"}
        ):
            reviews.append(
                {
                    "issue": "insect_document_review_required",
                    "doc_id": document["doc_id"],
                    "crop_or_plant": document["crop_or_plant"],
                    "pest_name": document["pest_name"],
                    "review_status": relation.get("review_status"),
                }
            )

    for key in sorted(set(disease_by_key) - used_disease_keys):
        reviews.append({"issue": "disease_detail_without_relation", "key": key})
    for key in sorted(set(insect_by_key) - used_insect_keys):
        reviews.append({"issue": "insect_detail_without_relation", "key": key})

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

    disease_count = sum(doc["category"] == "disease" for doc in documents)
    insect_count = sum(doc["category"] == "insect" for doc in documents)
    print(f"Wrote {document_count} normalized documents: {output_path}")
    print(f"  diseases={disease_count}, insects={insect_count}")
    print(f"Wrote {review_count} review rows: {review_path}")


if __name__ == "__main__":
    main()
