from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import read_jsonl
from config import PROCESSED_DIR, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

DEFAULT_INPUT = PROCESSED_DIR / "plant_master.sample.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load plant master records into Supabase plant_catalog.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for plant catalog loading.")
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("Install supabase Python package before loading: pip install supabase") from exc
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def batched(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def description_for(row: dict[str, Any]) -> str:
    parts = []
    if row.get("description"):
        parts.append(str(row["description"]))
    aliases = row.get("aliases") or []
    if aliases:
        parts.append("별칭: " + ", ".join(str(alias) for alias in aliases))
    categories = row.get("category") or []
    if categories:
        parts.append("분류: " + ", ".join(str(category) for category in categories))
    return " / ".join(parts)


def payload(row: dict[str, Any]) -> dict[str, Any]:
    aliases = [str(alias).strip() for alias in (row.get("aliases") or []) if str(alias).strip()]
    return {
        "id": row["plant_id"],
        "name": row["name_ko"],
        "species": row.get("name_scientific") or row.get("name_en") or row["name_ko"],
        "family_name": row.get("family") or None,
        "description": description_for(row) or None,
        "aliases": aliases,
    }


def main() -> None:
    args = parse_args()
    rows = read_jsonl(Path(args.input))
    payloads = [payload(row) for row in rows]

    print(f"Plant catalog records: {len(payloads)}")
    if args.dry_run:
        print("Dry run only. Supabase was not modified.")
        return

    client = load_client()
    for batch in batched(payloads, args.batch_size):
        client.table("plant_catalog").upsert(batch, on_conflict="id").execute()
    print("Supabase plant_catalog load complete.")


if __name__ == "__main__":
    main()
