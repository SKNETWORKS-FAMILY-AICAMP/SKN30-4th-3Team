"""
PSIS 파이프라인 실행을 위한 선행 파일 생성 코드입니다.

***수동 검증이 포함되었기에 향후 업데이트 시 추가적인 검토가 필요할 수 있습니다.***

Supabase plant_catalog에는 존재하지만 NCPMS의 검증된 병·해충 관계에는
없는 식물을 찾아 PSIS 수집용 supplement target JSONL을 생성합니다.

선행 조건:

1. 저장소 루트 .env에 아래 값이 있어야 합니다.

   SUPABASE_URL=...
   SUPABASE_SERVICE_ROLE_KEY=...

   plant_catalog 공개 읽기가 허용된 환경에서는
   SUPABASE_ANON_KEY를 대신 사용할 수 있습니다.

2. NCPMS 파이프라인 결과가 있어야 합니다.

   data/interim/all_NCPMS/plant_codes.jsonl
   data/interim/all_NCPMS/plant_disease_relations.jsonl
   data/interim/all_NCPMS/plant_insect_relations.jsonl

PowerShell:

    python data/notebooks/PSIS_getReady_target_supabase.py

기본 출력:

    data/notebooks/supabase_supplement_targets.jsonl

Git에 포함된 기존 출력 파일은 자동 추출 오탐을 제거한 큐레이션 기준
목록입니다. 기본 실행은 이 목록의 행과 검증값을 그대로 보존하며,
Supabase에서 새로 발견한 이름은 자동 추가하지 않고 통계로만 알립니다.

기존 기준 목록 없이 Supabase에서 새 파일을 만들거나 신규 후보까지
포함하려면 --include-new를 명시합니다.

Supabase에 연결하지 않고 내보낸 plant_catalog JSONL로 검증:

    python data/notebooks/PSIS_getReady_target_supabase.py `
      --catalog-jsonl data/interim/plant_catalog.jsonl `
      --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from PSIS_from_NCPMS import (
    DEFAULT_NCPMS_DIR,
    canonical_name_variants,
    load_env,
    load_ncpms_target_plants,
    normalize_text,
    normalized_name,
    read_jsonl,
)


DATA_DIR = Path(__file__).resolve().parents[1]
NOTEBOOKS_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = NOTEBOOKS_DIR / "supabase_supplement_targets.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build PSIS supplement targets from Supabase plant_catalog plants "
            "that are absent from verified NCPMS disease/insect targets."
        )
    )
    parser.add_argument(
        "--ncpms-dir",
        type=Path,
        default=DEFAULT_NCPMS_DIR,
        help="Directory containing all_NCPMS relation and plant-code JSONL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output supplement target JSONL.",
    )
    parser.add_argument(
        "--catalog-jsonl",
        type=Path,
        help=(
            "Optional local plant_catalog export. When supplied, Supabase is "
            "not contacted."
        ),
    )
    parser.add_argument(
        "--table",
        choices=["rag_chunks", "plant_catalog"],
        default="plant_catalog",
        help=(
            "Supabase source table. plant_catalog is the safe default. "
            "rag_chunks uses crop_or_plant metadata and can contain "
            "automatically inferred names that require review."
        ),
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument(
        "--no-preserve-existing",
        action="store_true",
        help=(
            "Do not preserve curated aliases/status fields from an existing "
            "output file."
        ),
    )
    parser.add_argument(
        "--include-new",
        action="store_true",
        help=(
            "Add newly discovered Supabase candidates that are not in the "
            "existing curated output. Review the result before PSIS use."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts and sample rows without writing the output.",
    )
    return parser.parse_args()


def clean_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = normalize_text(value)
        key = normalized_name(item)
        if not item or not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def fetch_supabase_plants(
    table: str,
    page_size: int,
) -> list[dict[str, Any]]:
    env = load_env()
    url = env.get("SUPABASE_URL", "")
    key = (
        env.get("SUPABASE_SERVICE_ROLE_KEY", "")
        or env.get("SUPABASE_ANON_KEY", "")
    )
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "(or SUPABASE_ANON_KEY) are required."
        )

    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError(
            "The supabase package is required. Install project dependencies "
            "before running this script."
        ) from exc

    client = create_client(url, key)
    rows: list[dict[str, Any]] = []
    start = 0
    select_columns = (
        "crop_or_plant,metadata"
        if table == "rag_chunks"
        else "id,name,species"
    )
    while True:
        response = (
            client.table(table)
            .select(select_columns)
            .range(start, start + page_size - 1)
            .execute()
        )
        page = response.data or []
        rows.extend(row for row in page if isinstance(row, dict))
        if len(page) < page_size:
            break
        start += page_size
    return rows


def ncpms_name_keys(targets: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for target in targets:
        names = [
            target.get("ncpms_crop_name"),
            *(target.get("source_names") or []),
            *(target.get("search_aliases") or []),
        ]
        for name in names:
            clean_name = normalize_text(name)
            if not clean_name:
                continue
            keys.add(normalized_name(clean_name))
            keys.update(canonical_name_variants(clean_name))
    return {key for key in keys if key}


def existing_by_name(
    output_path: Path,
    preserve: bool,
) -> dict[str, dict[str, Any]]:
    if not preserve or not output_path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(output_path):
        name = normalize_text(row.get("ncpms_crop_name"))
        if name:
            result[normalized_name(name)] = row
    return result


def build_supplement_targets(
    catalog_rows: list[dict[str, Any]],
    ncpms_targets: list[dict[str, Any]],
    existing_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ncpms_keys = ncpms_name_keys(ncpms_targets)
    catalog_names: dict[str, str] = {}

    for row in catalog_rows:
        raw_names: list[Any] = [row.get("name")]
        crop_or_plant = row.get("crop_or_plant")
        if isinstance(crop_or_plant, list):
            raw_names.extend(crop_or_plant)
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            metadata_names = (
                metadata.get("cropOrPlant")
                or metadata.get("crop_or_plant")
                or []
            )
            if isinstance(metadata_names, list):
                raw_names.extend(metadata_names)
        for raw_name in raw_names:
            name = normalize_text(raw_name)
            if not name:
                continue
            key = normalized_name(name)
            if not key:
                continue
            catalog_names.setdefault(key, name)

    output: list[dict[str, Any]] = []
    for key, name in catalog_names.items():
        variants = {key, *canonical_name_variants(name)}
        if variants & ncpms_keys:
            continue

        previous = existing_rows.get(key) or {}
        source_names = clean_strings(
            previous.get("source_names") or [name]
        )
        if name not in source_names:
            source_names.insert(0, name)

        output.append(
            {
                "has_disease": False,
                "has_insect": False,
                "ncpms_crop_code": "",
                "ncpms_crop_name": name,
                "ncpms_plant_key": f"supabase:{key}",
                "psis_self_or_synonym_hit": bool(
                    previous.get("psis_self_or_synonym_hit", False)
                ),
                "review_aliases": clean_strings(
                    previous.get("review_aliases") or []
                ),
                "search_aliases": clean_strings(
                    previous.get("search_aliases") or []
                ),
                "source_names": source_names,
            }
        )

    output.sort(key=lambda row: normalized_name(row["ncpms_crop_name"]))
    return output


def normalize_curated_targets(
    existing_rows: dict[str, dict[str, Any]],
    ncpms_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild the reviewed allowlist without introducing inferred names."""
    ncpms_keys = ncpms_name_keys(ncpms_targets)
    output: list[dict[str, Any]] = []
    for key, row in existing_rows.items():
        name = normalize_text(row.get("ncpms_crop_name"))
        if not name:
            continue
        # The supplement contains only plants absent from verified NCPMS
        # disease/insect targets. Drop an entry if NCPMS later gains it.
        if {key, *canonical_name_variants(name)} & ncpms_keys:
            continue
        output.append(
            {
                "has_disease": False,
                "has_insect": False,
                "ncpms_crop_code": "",
                "ncpms_crop_name": name,
                "ncpms_plant_key": normalize_text(
                    row.get("ncpms_plant_key")
                )
                or f"supabase:{key}",
                "psis_self_or_synonym_hit": bool(
                    row.get("psis_self_or_synonym_hit", False)
                ),
                "review_aliases": clean_strings(
                    row.get("review_aliases") or []
                ),
                "search_aliases": clean_strings(
                    row.get("search_aliases") or []
                ),
                "source_names": clean_strings(
                    row.get("source_names") or [name]
                ),
            }
        )
    output.sort(key=lambda row: normalized_name(row["ncpms_crop_name"]))
    return output


def write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )
    temp_path.replace(path)


def main() -> int:
    args = parse_args()
    if args.page_size < 1 or args.page_size > 1000:
        raise ValueError("--page-size must be between 1 and 1000.")

    ncpms_targets = load_ncpms_target_plants(args.ncpms_dir)
    if args.catalog_jsonl:
        catalog_rows = read_jsonl(args.catalog_jsonl)
        catalog_source = str(args.catalog_jsonl)
    else:
        catalog_rows = fetch_supabase_plants(args.table, args.page_size)
        catalog_source = f"supabase:{args.table}"

    existing_rows = existing_by_name(
        args.output,
        preserve=not args.no_preserve_existing,
    )
    discovered_targets = build_supplement_targets(
        catalog_rows,
        ncpms_targets,
        existing_rows,
    )
    if existing_rows and not args.include_new:
        supplement_targets = normalize_curated_targets(
            existing_rows,
            ncpms_targets,
        )
    else:
        supplement_targets = discovered_targets
    discovered_keys = {
        normalized_name(row["ncpms_crop_name"])
        for row in discovered_targets
    }
    output_keys = {
        normalized_name(row["ncpms_crop_name"])
        for row in supplement_targets
    }
    summary = {
        "catalog_source": catalog_source,
        "catalog_rows": len(catalog_rows),
        "catalog_plant_names": len(
            {
                normalized_name(name)
                for row in catalog_rows
                for name in (
                    [row.get("name")]
                    + (
                        row.get("crop_or_plant")
                        if isinstance(row.get("crop_or_plant"), list)
                        else []
                    )
                    + (
                        (
                            row.get("metadata", {}).get("cropOrPlant")
                            or row.get("metadata", {}).get("crop_or_plant")
                            or []
                        )
                        if isinstance(row.get("metadata"), dict)
                        and isinstance(
                            (
                                row.get("metadata", {}).get("cropOrPlant")
                                or row.get("metadata", {}).get(
                                    "crop_or_plant"
                                )
                                or []
                            ),
                            list,
                        )
                        else []
                    )
                )
                if normalize_text(name)
            }
        ),
        "ncpms_targets": len(ncpms_targets),
        "supplement_targets": len(supplement_targets),
        "new_candidates_not_added": len(discovered_keys - output_keys),
        "curated_targets_not_in_current_source": len(
            output_keys - discovered_keys
        ),
        "preserved_existing_rows": sum(
            normalized_name(row["ncpms_crop_name"]) in existing_rows
            for row in supplement_targets
        ),
        "output": str(args.output),
        "include_new": args.include_new,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for row in supplement_targets[:5]:
        print(json.dumps(row, ensure_ascii=False))

    if not args.dry_run:
        write_jsonl_atomic(args.output, supplement_targets)
        print(f"Wrote {len(supplement_targets)} rows: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
