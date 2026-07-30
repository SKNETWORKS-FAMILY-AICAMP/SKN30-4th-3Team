from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
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
from config import DEFAULT_CHUNKS, DEFAULT_NORMALIZED_DOCS, DEFAULT_SOURCES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk normalized RAG documents with citation metadata.")
    parser.add_argument("--input", default=str(DEFAULT_NORMALIZED_DOCS))
    parser.add_argument("--output", default=str(DEFAULT_CHUNKS))
    parser.add_argument("--sources-output", default=str(DEFAULT_SOURCES))
    parser.add_argument("--max-chars", type=int, default=2200)
    parser.add_argument("--overlap-chars", type=int, default=250)
    return parser.parse_args()


def source_record(doc: dict[str, Any], registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
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
    meaningful = sum(1 for char in compact if char.isalpha() or "\uac00" <= char <= "\ud7a3")
    return meaningful / len(compact)


def should_skip_chunk(text: str) -> bool:
    content = normalize_text(text)
    if len(content) < 80:
        return True
    return meaningful_text_ratio(content) < 0.45


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


def chunk_record(doc: dict[str, Any], text: str, index: int) -> dict[str, Any]:
    source_id = doc["source_id"]
    source_key = doc.get("source_key", "")
    doc_id = doc["doc_id"]
    chunk_key = f"{source_key or source_id}:{stable_hash(doc_id, 10)}:{index:04d}"
    chunk_id = uuid_for_chunk_key(chunk_key)
    safety_tags = merge_safety_tags(doc.get("safety_tags"))
    content = normalize_text(text)
    crop_or_plant = list(doc.get("crop_or_plant", []))
    if source_key != "plantsolve_care":
        inference_text = doc.get("title", "") if source_key.startswith("nongsaro") else f"{doc.get('title', '')} {content}"
        for name in infer_crop_or_plant(inference_text):
            if name not in crop_or_plant:
                crop_or_plant.append(name)
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


def chunking_params(doc: dict[str, Any], default_max_chars: int, default_overlap_chars: int) -> tuple[int, int]:
    source_key = doc.get("source_key", "")
    if source_key == "ncpms_pest_reference":
        return min(default_max_chars, 1400), min(default_overlap_chars, 160)
    if source_key in {"nongsaro_indoor_catalog", "nongsaro_crop_tech"}:
        return min(default_max_chars, 1200), min(default_overlap_chars, 140)
    return default_max_chars, default_overlap_chars


def main() -> None:
    args = parse_args()
    docs = read_jsonl(Path(args.input))
    registry = load_source_registry()
    chunks: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}

    for doc in docs:
        if not doc.get("source_id"):
            raise ValueError(f"Missing source_id for doc_id={doc.get('doc_id')}")
        sources.setdefault(doc["source_id"], source_record(doc, registry))
        max_chars, overlap_chars = chunking_params(doc, args.max_chars, args.overlap_chars)
        parts = chunk_text(doc.get("text", ""), max_chars=max_chars, overlap_chars=overlap_chars)
        for index, part in enumerate(parts, start=1):
            if should_skip_chunk(part):
                continue
            chunks.append(chunk_record(doc, part, index))

    source_count = write_jsonl(Path(args.sources_output), sources.values())
    chunk_count = write_jsonl(Path(args.output), chunks)
    print(f"Wrote {chunk_count} chunks: {args.output}")
    print(f"Wrote {source_count} sources: {args.sources_output}")


if __name__ == "__main__":
    main()
