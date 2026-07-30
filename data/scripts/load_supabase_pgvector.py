from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import read_jsonl
from config import DEFAULT_EMBEDDED_CHUNKS, DEFAULT_SOURCES, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load embedded RAG chunks into Supabase pgvector tables.")
    parser.add_argument("--chunks", default=str(DEFAULT_EMBEDDED_CHUNKS))
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--replace", action="store_true", help="Delete existing rag_chunks for loaded source_ids first.")
    parser.add_argument(
        "--include-metadata",
        action="store_true",
        help="Deprecated compatibility flag. Metadata is included by default unless --skip-metadata is used.",
    )
    parser.add_argument("--skip-metadata", action="store_true", help="Do not upload rag_chunks.metadata.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for vector DB loading.")
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("Install supabase Python package before loading: pip install supabase") from exc
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def batched(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def source_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "title": row["title"],
        "url": row.get("url", ""),
        "publisher": row.get("publisher", ""),
        "collected_at": row.get("collected_at") or None,
    }


def chunk_payload(row: dict[str, Any], include_metadata: bool = False) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    metadata.setdefault("chunkId", row["chunk_id"])
    metadata.setdefault("chunkKey", row.get("chunk_key", ""))
    metadata.setdefault("sourceId", row["source_id"])
    metadata.setdefault("sourceKey", row.get("source_key", ""))
    metadata.setdefault("title", row["title"])
    metadata.setdefault("url", row.get("url", ""))
    metadata.setdefault("publisher", row.get("publisher", ""))
    metadata.setdefault("category", row.get("category", ""))
    metadata.setdefault("section", row.get("section") or row.get("category") or "overview")
    metadata.setdefault("excerpt", row.get("excerpt") or row.get("text", "")[:260])
    metadata.setdefault("contentPreview", metadata["excerpt"])
    metadata.setdefault("cropOrPlant", row.get("crop_or_plant", []))
    metadata.setdefault("symptomKeywords", row.get("symptom_keywords", []))
    metadata.setdefault("safetyTags", row.get("safety_tags", []))
    payload = {
        "chunk_id": row["chunk_id"],
        "source_id": row["source_id"],
        "text": row["text"],
        "embedding": row["embedding"],
        "symptom_keywords": row.get("symptom_keywords", []),
        "crop_or_plant": row.get("crop_or_plant", []),
    }
    if include_metadata:
        payload["metadata"] = metadata
    return payload


def main() -> None:
    args = parse_args()
    sources = read_jsonl(Path(args.sources))
    chunks = read_jsonl(Path(args.chunks))
    source_ids = sorted({row["source_id"] for row in chunks})

    print(f"Sources: {len(sources)}")
    print(f"Chunks: {len(chunks)}")
    print(f"Source IDs: {', '.join(source_ids)}")

    if args.dry_run:
        print("Dry run only. Supabase was not modified.")
        return

    client = load_client()

    if sources:
        client.table("rag_sources").upsert([source_payload(row) for row in sources], on_conflict="source_id").execute()

    if args.replace and source_ids:
        for source_id in source_ids:
            client.table("rag_chunks").delete().eq("source_id", source_id).execute()

    include_metadata = not args.skip_metadata or args.include_metadata
    payloads = [chunk_payload(row, include_metadata=include_metadata) for row in chunks]
    try:
        for batch in batched(payloads, args.batch_size):
            client.table("rag_chunks").upsert(batch, on_conflict="chunk_id").execute()
    except Exception as exc:
        if not include_metadata:
            raise
        print(f"Metadata upload failed, retrying without metadata. Reason: {exc}")
        payloads = [chunk_payload(row, include_metadata=False) for row in chunks]
        for batch in batched(payloads, args.batch_size):
            client.table("rag_chunks").upsert(batch, on_conflict="chunk_id").execute()

    print("Supabase pgvector load complete.")


if __name__ == "__main__":
    main()
