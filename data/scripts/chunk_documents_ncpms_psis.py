"""NCPMS 병해충·PSIS 농약 normalized 문서 전용 청킹 스크립트입니다.

[용도]
- 기존 chunk_documents.py의 공통 청크 구조를 유지합니다.
- 명시적인 crop_or_plant가 있으면 본문에서 식물명을 다시 추론하지 않습니다.
- 짧지만 유효한 구조화 병해충·농약 문서를 길이 때문에 제외하지 않습니다.
- NCPMS·PSIS 식별 필드를 청크 metadata에 전달합니다.
- PSIS text에서 불필요한 '문서 유형: PSIS 농약 등록정보' 줄을 제거합니다.

[선행조건]
선택한 dataset에 대응하는 normalized 문서가 있어야 합니다.
- ncpms-pest:
  data/interim/normalized/rag_documents.ncpms_pest.normalized.jsonl
- psis-pesticide:
  data/interim/normalized/rag_documents.psis_pesticide.normalized.jsonl

[PowerShell 실행]
# 병해충
python data/scripts/chunk_documents_ncpms_psis.py `
  --dataset ncpms-pest

# 농약
python data/scripts/chunk_documents_ncpms_psis.py `
  --dataset psis-pesticide

[기본 출력]
- 청크: data/processed/rag_chunks.<dataset>.jsonl
- 출처: data/interim/normalized/rag_sources.<dataset>.jsonl

[다음 단계]
# 병해충 예시
python data/scripts/embed_chunks.py `
  --input data/processed/rag_chunks.ncpms_pest.jsonl `
  --output data/processed/rag_chunks.ncpms_pest.embedded.jsonl

# 농약 예시
python data/scripts/embed_chunks.py `
  --input data/processed/rag_chunks.psis_pesticide.jsonl `
  --output data/processed/rag_chunks.psis_pesticide.embedded.jsonl

[주의]
- ncpms-pest는 최대 1,400자, overlap 160자를 적용합니다.
- psis-pesticide는 최대 2,200자, overlap 250자를 사용합니다.
- 문서가 최대 크기보다 짧으면 분할과 overlap은 발생하지 않습니다.
- 기존 data/scripts/chunk_documents.py는 수정하지 않습니다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import (  # noqa: E402
    chunk_text,
    detect_symptom_keywords,
    infer_crop_or_plant,
    load_source_registry,
    merge_safety_tags,
    normalize_text,
    read_jsonl,
    stable_hash,
    uuid_for_chunk_key,
    write_jsonl,
)


NORMALIZED_DIR = DATA_DIR / "interim" / "normalized"
PROCESSED_DIR = DATA_DIR / "processed"
DATASET_PATHS = {
    "ncpms-pest": {
        "input": NORMALIZED_DIR / "rag_documents.ncpms_pest.normalized.jsonl",
        "output": PROCESSED_DIR / "rag_chunks.ncpms_pest.jsonl",
        "sources_output": NORMALIZED_DIR / "rag_sources.ncpms_pest.jsonl",
    },
    "psis-pesticide": {
        "input": NORMALIZED_DIR / "rag_documents.psis_pesticide.normalized.jsonl",
        "output": PROCESSED_DIR / "rag_chunks.psis_pesticide.jsonl",
        "sources_output": NORMALIZED_DIR / "rag_sources.psis_pesticide.jsonl",
    },
}
STRUCTURED_SOURCE_KEYS = {
    "ncpms_pest_reference",
    "psis_pesticide_safety",
}
PSIS_DOCUMENT_TYPE_LINE = "문서 유형: PSIS 농약 등록정보"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NCPMS 병해충·PSIS 농약 normalized 문서를 청킹합니다."
    )
    parser.add_argument(
        "--dataset",
        required=True,
        choices=sorted(DATASET_PATHS),
        help="ncpms-pest 또는 psis-pesticide",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sources-output", type=Path)
    parser.add_argument("--max-chars", type=int, default=2200)
    parser.add_argument("--overlap-chars", type=int, default=250)
    args = parser.parse_args()

    defaults = DATASET_PATHS[args.dataset]
    args.input = args.input or defaults["input"]
    args.output = args.output or defaults["output"]
    args.sources_output = args.sources_output or defaults["sources_output"]
    return args


def source_record(
    doc: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source = registry.get(doc.get("source_key")) or registry.get(doc.get("source_id")) or {}
    return {
        "source_id": doc["source_id"],
        "source_key": doc.get("source_key", ""),
        "title": source.get("title") or doc["title"],
        "publisher": source.get("publisher") or doc.get("publisher", ""),
        "url": source.get("url") or doc.get("url", ""),
        "license": source.get("license") or doc.get("license", ""),
        "collected_at": doc.get("collected_at", ""),
        "category": source.get("category") or doc.get("category", ""),
        "priority": source.get("priority") or doc.get("priority", 99),
    }


def excerpt_for_display(text: str, max_chars: int = 260) -> str:
    content = normalize_text(text)
    if len(content) <= max_chars:
        return content
    end = max(content.rfind(".", 0, max_chars), content.rfind("\n", 0, max_chars))
    if end < int(max_chars * 0.45):
        end = max_chars
    return content[:end].strip().rstrip(".") + "..."


def meaningful_text_ratio(text: str) -> float:
    compact = "".join(char for char in text if not char.isspace())
    if not compact:
        return 0.0
    meaningful = sum(
        1
        for char in compact
        if char.isalpha() or "\uac00" <= char <= "\ud7a3"
    )
    return meaningful / len(compact)


def should_skip_chunk(text: str, source_key: str) -> bool:
    content = normalize_text(text)
    if not content:
        return True
    if source_key in STRUCTURED_SOURCE_KEYS:
        return meaningful_text_ratio(content) < 0.25
    if len(content) < 80:
        return True
    return meaningful_text_ratio(content) < 0.45


def text_for_chunking(doc: dict[str, Any]) -> str:
    """PSIS 검색 본문에서 metadata와 중복되는 문서 유형 줄을 제거합니다."""
    text = str(doc.get("text") or "")
    if doc.get("source_key") != "psis_pesticide_safety":
        return text
    lines = [
        line
        for line in text.splitlines()
        if normalize_text(line) != PSIS_DOCUMENT_TYPE_LINE
    ]
    return "\n".join(lines).strip()


def section_for_chunk(doc: dict[str, Any], index: int) -> str:
    section = normalize_text(str(doc.get("section") or ""))
    if section:
        return section
    category = normalize_text(str(doc.get("category") or ""))
    if category:
        return category
    if index == 1:
        return "overview"
    return f"part-{index:02d}"


def crop_or_plant_for_document(
    doc: dict[str, Any],
    content: str,
) -> list[str]:
    explicit = [
        normalize_text(str(name))
        for name in doc.get("crop_or_plant") or []
        if normalize_text(str(name))
    ]
    if explicit:
        return list(dict.fromkeys(explicit))

    source_key = str(doc.get("source_key") or "")
    inference_text = (
        doc.get("title", "")
        if source_key.startswith("nongsaro")
        else f"{doc.get('title', '')} {content}"
    )
    return infer_crop_or_plant(inference_text)


def domain_metadata(doc: dict[str, Any]) -> dict[str, Any]:
    """작은 도메인 식별 필드만 청크 metadata에 전달합니다."""
    metadata: dict[str, Any] = {}
    mappings = {
        "ncpms_key": "ncpmsKey",
        "crop_code": "cropCode",
        "pest_type": "pestType",
        "pest_name": "pestName",
        "review_status": "reviewStatus",
        "match_method": "matchMethod",
        "group_key": "groupKey",
        "crop_name": "cropName",
        "target_disease_pest": "targetDiseasePest",
        "use_type": "useType",
        "pesticide_names": "pesticideNames",
        "active_ingredients": "activeIngredients",
        "registration_count": "registrationCount",
    }
    for source_name, metadata_name in mappings.items():
        value = doc.get(source_name)
        if value not in (None, "", []):
            metadata[metadata_name] = value
    return metadata


def chunk_record(
    doc: dict[str, Any],
    text: str,
    index: int,
) -> dict[str, Any]:
    source_id = doc["source_id"]
    source_key = doc.get("source_key", "")
    doc_id = doc["doc_id"]
    chunk_key = f"{source_key or source_id}:{stable_hash(doc_id, 10)}:{index:04d}"
    chunk_id = uuid_for_chunk_key(chunk_key)
    safety_tags = merge_safety_tags(doc.get("safety_tags"))
    content = normalize_text(text)
    crop_or_plant = crop_or_plant_for_document(doc, content)
    symptom_keywords = doc.get("symptom_keywords") or detect_symptom_keywords(content)
    if not symptom_keywords:
        symptom_keywords = [doc.get("category") or "general_reference"]
    section = section_for_chunk(doc, index)
    excerpt = excerpt_for_display(content)
    metadata = {
        "chunkId": chunk_id,
        "chunkKey": chunk_key,
        "docId": doc_id,
        "sourceId": source_id,
        "sourceKey": source_key,
        "title": doc["title"],
        "publisher": doc.get("publisher", ""),
        "url": doc.get("url", ""),
        "license": doc.get("license", ""),
        "category": doc.get("category", ""),
        "section": section,
        "excerpt": excerpt,
        "contentPreview": excerpt,
        "cropOrPlant": crop_or_plant,
        "symptomKeywords": symptom_keywords,
        "safetyTags": safety_tags,
        "usageScope": doc.get("usage_scope", "rag"),
        **domain_metadata(doc),
    }
    if doc.get("image_refs"):
        metadata["imageRefs"] = doc["image_refs"]

    return {
        "chunk_id": chunk_id,
        "chunk_key": chunk_key,
        "source_id": source_id,
        "source_key": source_key,
        "doc_id": doc_id,
        "title": doc["title"],
        "publisher": doc.get("publisher", ""),
        "url": doc.get("url", ""),
        "license": doc.get("license", ""),
        "collected_at": doc.get("collected_at", ""),
        "category": doc.get("category", ""),
        "section": section,
        "excerpt": excerpt,
        "priority": doc.get("priority", 99),
        "usage_scope": doc.get("usage_scope", "rag"),
        "crop_or_plant": crop_or_plant,
        "symptom_keywords": symptom_keywords,
        "safety_tags": safety_tags,
        "text": content,
        "metadata": metadata,
    }


def chunking_params(
    doc: dict[str, Any],
    default_max_chars: int,
    default_overlap_chars: int,
) -> tuple[int, int]:
    source_key = doc.get("source_key", "")
    if source_key == "ncpms_pest_reference":
        return min(default_max_chars, 1400), min(default_overlap_chars, 160)
    return default_max_chars, default_overlap_chars


def main() -> None:
    args = parse_args()
    docs = read_jsonl(args.input.resolve())
    registry = load_source_registry()
    chunks: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}
    skipped_documents = 0

    for doc in docs:
        if not doc.get("source_id"):
            raise ValueError(f"Missing source_id for doc_id={doc.get('doc_id')}")
        if not doc.get("doc_id"):
            raise ValueError(f"Missing doc_id for title={doc.get('title')}")
        sources.setdefault(doc["source_id"], source_record(doc, registry))
        max_chars, overlap_chars = chunking_params(
            doc,
            args.max_chars,
            args.overlap_chars,
        )
        document_chunk_count = 0
        parts = chunk_text(
            text_for_chunking(doc),
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
        for index, part in enumerate(parts, start=1):
            if should_skip_chunk(part, str(doc.get("source_key") or "")):
                continue
            chunks.append(chunk_record(doc, part, index))
            document_chunk_count += 1
        if document_chunk_count == 0:
            skipped_documents += 1

    source_count = write_jsonl(args.sources_output.resolve(), sources.values())
    chunk_count = write_jsonl(args.output.resolve(), chunks)
    unique_doc_count = len({chunk["doc_id"] for chunk in chunks})
    print(f"Dataset: {args.dataset}")
    print(f"Input documents: {len(docs)}")
    print(f"Wrote {chunk_count} chunks: {args.output.resolve()}")
    print(f"Covered documents: {unique_doc_count}")
    print(f"Skipped documents: {skipped_documents}")
    print(f"Wrote {source_count} sources: {args.sources_output.resolve()}")


if __name__ == "__main__":
    main()
