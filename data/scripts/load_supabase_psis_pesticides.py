"""임베딩된 PSIS 농약 청크를 Supabase 전용 테이블에 적재합니다.

[용도]
- 기존 load_supabase_pgvector.py의 source·batch·upsert 흐름을 유지합니다.
- embedded 청크와 normalized 문서를 doc_id로 1:1 결합합니다.
- 검색용 text·embedding과 제품별 registrations를 한 행에 함께 적재합니다.
- 기존 청크 metadata는 삭제하거나 축소하지 않고 그대로 보존합니다.

[선행조건]
1. 아래 3개 파일이 있어야 합니다.
   - data/processed/rag_chunks.psis_pesticide.embedded.jsonl
   - data/interim/normalized/rag_documents.psis_pesticide.normalized.jsonl
   - data/interim/normalized/rag_sources.psis_pesticide.jsonl
2. Supabase에 psis_pesticide_chunks 테이블이 생성되어 있어야 합니다.
   - 생성 SQL: data/scripts/create_supabase_psis_pesticide_table.sql
3. 실제 적재 시 .env에 다음 값이 필요합니다.
   - SUPABASE_URL
   - SUPABASE_SERVICE_ROLE_KEY

[검증만 실행]
python data/scripts/load_supabase_psis_pesticides.py --dry-run

[실제 적재]
python data/scripts/load_supabase_psis_pesticides.py

[정상 기준]
- Embedded chunks: 1168
- Matched normalized rows: 1168
- Registrations: 13158
- Embedding dimensions: 1536

[적재 대상]
- 출처: public.rag_sources
- 농약: public.psis_pesticide_chunks

[주의]
- 삭제나 replace를 수행하지 않고 chunk_id 기준 upsert만 합니다.
- registrations는 임베딩하지 않고 JSONB 구조화 데이터로 저장합니다.
- 자연어 벡터 검색 RPC와 백엔드 조회 로직은 백엔드 담당 범위입니다.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from pathlib import Path
from typing import Any

from common import read_jsonl
from config import DATA_DIR, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


DEFAULT_CHUNKS = DATA_DIR / "processed" / "rag_chunks.psis_pesticide.embedded.jsonl"
DEFAULT_DOCUMENTS = (
    DATA_DIR
    / "interim"
    / "normalized"
    / "rag_documents.psis_pesticide.normalized.jsonl"
)
DEFAULT_SOURCES = (
    DATA_DIR
    / "interim"
    / "normalized"
    / "rag_sources.psis_pesticide.jsonl"
)
TARGET_TABLE = "psis_pesticide_chunks"
EMBEDDING_DIMENSIONS = 1536


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load embedded PSIS pesticide chunks into Supabase."
    )
    parser.add_argument("--chunks", default=str(DEFAULT_CHUNKS))
    parser.add_argument("--documents", default=str(DEFAULT_DOCUMENTS))
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for loading."
        )
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError(
            "Install supabase Python package before loading: pip install supabase"
        ) from exc
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def batched(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size <= 0:
        raise ValueError("--batch-size must be greater than 0.")
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def source_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "title": row["title"],
        "url": row.get("url", ""),
        "publisher": row.get("publisher", ""),
        "collected_at": row.get("collected_at") or None,
    }


def index_documents(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    doc_ids = [str(row.get("doc_id") or "") for row in rows]
    missing_count = sum(not doc_id for doc_id in doc_ids)
    duplicates = [
        doc_id
        for doc_id, count in Counter(doc_ids).items()
        if doc_id and count > 1
    ]
    if missing_count:
        raise ValueError(f"Normalized rows missing doc_id: {missing_count}")
    if duplicates:
        raise ValueError(f"Duplicate normalized doc_id values: {duplicates[:5]}")
    return {str(row["doc_id"]): row for row in rows}


def validate_embedding(row: dict[str, Any]) -> None:
    doc_id = str(row.get("doc_id") or "")
    embedding = row.get("embedding")
    if not isinstance(embedding, list):
        raise ValueError(f"Embedding is not a list for doc_id={doc_id}")
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding dimensions must be {EMBEDDING_DIMENSIONS} "
            f"for doc_id={doc_id}; got {len(embedding)}"
        )
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in embedding
    ):
        raise ValueError(f"Embedding contains invalid values for doc_id={doc_id}")


def pesticide_payload(
    chunk: dict[str, Any],
    document: dict[str, Any],
) -> dict[str, Any]:
    validate_embedding(chunk)
    doc_id = str(chunk["doc_id"])
    registrations = document.get("registrations") or []
    if not isinstance(registrations, list) or any(
        not isinstance(item, dict) for item in registrations
    ):
        raise ValueError(f"Invalid registrations for doc_id={doc_id}")

    registration_count = document.get("registration_count")
    if registration_count != len(registrations):
        raise ValueError(
            f"registration_count mismatch for doc_id={doc_id}: "
            f"{registration_count} != {len(registrations)}"
        )

    # 기존 청킹/임베딩 metadata를 삭제하거나 축소하지 않고 그대로 보존합니다.
    metadata = dict(chunk.get("metadata") or {})
    metadata.setdefault("docId", doc_id)
    metadata.setdefault("groupKey", document.get("group_key", ""))
    metadata.setdefault("cropCode", document.get("crop_code", ""))
    metadata.setdefault("cropName", document.get("crop_name", ""))
    metadata.setdefault(
        "targetDiseasePest",
        document.get("target_disease_pest", ""),
    )
    metadata.setdefault("useType", document.get("use_type", ""))
    metadata.setdefault("pesticideNames", document.get("pesticide_names", []))
    metadata.setdefault(
        "activeIngredients",
        document.get("active_ingredients", []),
    )
    metadata.setdefault("registrationCount", len(registrations))

    return {
        "chunk_id": chunk["chunk_id"],
        "source_id": chunk["source_id"],
        "doc_id": doc_id,
        "group_key": document["group_key"],
        "title": chunk["title"],
        "crop_code": document.get("crop_code") or None,
        "crop_name": document["crop_name"],
        "target_disease_pest": document["target_disease_pest"],
        "use_type": document.get("use_type") or None,
        "text": chunk["text"],
        "embedding": chunk["embedding"],
        "pesticide_names": document.get("pesticide_names") or [],
        "active_ingredients": document.get("active_ingredients") or [],
        "registration_count": len(registrations),
        "registrations": registrations,
        "metadata": metadata,
        "collected_at": document.get("collected_at") or None,
    }


def build_payloads(
    chunks: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    document_by_id = index_documents(documents)

    chunk_doc_ids = [str(row.get("doc_id") or "") for row in chunks]
    missing_doc_ids = sum(not doc_id for doc_id in chunk_doc_ids)
    duplicate_doc_ids = [
        doc_id
        for doc_id, count in Counter(chunk_doc_ids).items()
        if doc_id and count > 1
    ]
    if missing_doc_ids:
        raise ValueError(f"Embedded chunks missing doc_id: {missing_doc_ids}")
    if duplicate_doc_ids:
        raise ValueError(
            "PSIS groups must have exactly one embedded chunk; "
            f"duplicate doc_id values: {duplicate_doc_ids[:5]}"
        )

    chunk_ids = [str(row.get("chunk_id") or "") for row in chunks]
    invalid_chunk_ids = [
        chunk_id
        for chunk_id, count in Counter(chunk_ids).items()
        if not chunk_id or count > 1
    ]
    if invalid_chunk_ids:
        raise ValueError(
            f"Missing or duplicate chunk_id values: {invalid_chunk_ids[:5]}"
        )

    chunk_doc_id_set = set(chunk_doc_ids)
    document_doc_id_set = set(document_by_id)
    chunks_without_documents = chunk_doc_id_set - document_doc_id_set
    documents_without_chunks = document_doc_id_set - chunk_doc_id_set
    if chunks_without_documents or documents_without_chunks:
        raise ValueError(
            "Chunk/document join mismatch: "
            f"chunks_without_documents={len(chunks_without_documents)}, "
            f"documents_without_chunks={len(documents_without_chunks)}"
        )

    source_ids = {str(row.get("source_id") or "") for row in sources}
    chunk_source_ids = {str(row.get("source_id") or "") for row in chunks}
    if not source_ids or source_ids != chunk_source_ids:
        raise ValueError(
            "Source linkage mismatch: "
            f"sources={sorted(source_ids)}, chunks={sorted(chunk_source_ids)}"
        )

    source_payloads = [source_payload(row) for row in sources]
    pesticide_payloads = [
        pesticide_payload(chunk, document_by_id[str(chunk["doc_id"])])
        for chunk in chunks
    ]
    return source_payloads, pesticide_payloads


def main() -> None:
    args = parse_args()
    chunks = read_jsonl(Path(args.chunks))
    documents = read_jsonl(Path(args.documents))
    sources = read_jsonl(Path(args.sources))
    source_payloads, pesticide_payloads = build_payloads(
        chunks,
        documents,
        sources,
    )

    registration_total = sum(
        row["registration_count"] for row in pesticide_payloads
    )
    source_ids = sorted({row["source_id"] for row in pesticide_payloads})
    print(f"Sources: {len(source_payloads)}")
    print(f"Embedded chunks: {len(chunks)}")
    print(f"Matched normalized rows: {len(pesticide_payloads)}")
    print(f"Registrations: {registration_total}")
    print(f"Embedding dimensions: {EMBEDDING_DIMENSIONS}")
    print(f"Source IDs: {', '.join(source_ids)}")
    print(f"Target table: {TARGET_TABLE}")

    if args.dry_run:
        print("Ready to load.")
        print("Dry run only. Supabase was not modified.")
        return

    client = load_client()
    if source_payloads:
        client.table("rag_sources").upsert(
            source_payloads,
            on_conflict="source_id",
        ).execute()
    for batch in batched(pesticide_payloads, args.batch_size):
        client.table(TARGET_TABLE).upsert(
            batch,
            on_conflict="chunk_id",
        ).execute()
    print("Supabase PSIS pesticide load complete.")


if __name__ == "__main__":
    main()
