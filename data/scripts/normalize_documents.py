from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
    INTERIM_DIR,
    clean_scraped_text,
    detect_symptom_keywords,
    ensure_dirs,
    infer_crop_or_plant,
    load_source_registry,
    merge_safety_tags,
    normalize_text,
    read_jsonl,
    stable_hash,
    write_jsonl,
)
from config import DEFAULT_NORMALIZED_DOCS

EXCLUDE_PATTERNS = [
    "rag_documents.normalized",
    "rag_chunks",
    "embedded",
    "errors",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize collected/interim documents for RAG chunking.")
    parser.add_argument(
        "--input",
        action="append",
        help="Input JSONL path. Repeatable. Defaults to all data/interim/*.jsonl except generated files.",
    )
    parser.add_argument("--output", default=str(DEFAULT_NORMALIZED_DOCS))
    parser.add_argument("--min-chars", type=int, default=120)
    return parser.parse_args()


def default_inputs() -> list[Path]:
    paths = []
    for path in INTERIM_DIR.glob("*.jsonl"):
        if any(pattern in path.name for pattern in EXCLUDE_PATTERNS):
            continue
        paths.append(path)
    return sorted(paths)


def normalize_doc(row: dict[str, Any], registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw_source_id = row.get("source_id")
    source_key = row.get("source_key") or raw_source_id
    source = registry.get(source_key) or registry.get(raw_source_id) or {}
    source_key = source.get("source_key") or source_key
    source_id = source.get("source_uuid") or raw_source_id
    title = row.get("title") or source.get("title") or ""
    text = clean_scraped_text(
        str(row.get("text") or row.get("content") or row.get("body") or ""),
        source_key=source_key,
        title=title,
    )
    symptom_keywords = row.get("symptom_keywords") or detect_symptom_keywords(text)

    category = row.get("category") or source.get("category") or "uncategorized"
    safety_tags = merge_safety_tags(source.get("safety_tags"), row.get("safety_tags"))

    crop_or_plant = list(row.get("crop_or_plant") or row.get("target_crops") or [])
    if source_key in {"nongsaro_indoor_catalog", "nongsaro_crop_tech"}:
        source_title = source.get("title", "")
        crop_or_plant = [name for name in crop_or_plant if source_title and not str(name).startswith(source_title)]
    is_index_doc = source_key and title == source.get("title") and source_key.startswith("nongsaro")
    if not is_index_doc and source_key != "plantsolve_care":
        inference_text = title if source_key.startswith("nongsaro") else f"{title} {text}"
        for name in infer_crop_or_plant(inference_text):
            if name not in crop_or_plant:
                crop_or_plant.append(name)

    doc_id = row.get("doc_id") or f"{source_id or 'unknown'}:{stable_hash(text)}"
    doc = {
        "doc_id": doc_id,
        "source_id": source_id,
        "source_key": source_key,
        "title": title or doc_id,
        "publisher": row.get("publisher") or source.get("publisher") or "",
        "url": row.get("url") or row.get("source_url") or source.get("url") or "",
        "license": row.get("license") or source.get("license") or "verify_required",
        "collected_at": row.get("collected_at") or "",
        "category": category,
        "priority": row.get("priority") or source.get("priority") or 99,
        "usage_scope": row.get("usage_scope") or source.get("usage_scope") or "rag",
        "section": row.get("section") or row.get("heading") or row.get("sub_title") or "",
        "crop_or_plant": crop_or_plant,
        "symptom_keywords": symptom_keywords,
        "safety_tags": safety_tags,
        "text": text,
    }
    if row.get("image_refs"):
        doc["image_refs"] = row["image_refs"]
    return doc


def should_skip_doc(doc: dict[str, Any], registry: dict[str, dict[str, Any]]) -> bool:
    source_key = doc.get("source_key", "")
    source = registry.get(source_key) or {}
    if source_key in {"nongsaro_indoor_catalog", "nongsaro_crop_tech"} and doc.get("title") == source.get("title"):
        return True
    if source_key == "nongsaro_crop_tech" and "등록일" in doc.get("text", "") and len(doc.get("text", "")) < 450:
        return True
    return False


def main() -> None:
    args = parse_args()
    ensure_dirs()
    inputs = [Path(path) for path in args.input] if args.input else default_inputs()
    registry = load_source_registry()

    normalized: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    skipped = 0

    for path in inputs:
        for row in read_jsonl(path):
            doc = normalize_doc(row, registry)
            if should_skip_doc(doc, registry):
                skipped += 1
                continue
            if len(doc["text"]) < args.min_chars:
                skipped += 1
                continue
            if doc["doc_id"] in seen_doc_ids:
                skipped += 1
                continue
            seen_doc_ids.add(doc["doc_id"])
            normalized.append(doc)

    count = write_jsonl(Path(args.output), normalized)
    print(f"Normalized {count} documents to {args.output}. Skipped {skipped}.")


if __name__ == "__main__":
    main()
