from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any

from all_ncpms_common import (
    add_client_arguments,
    build_client,
    dedupe_rows,
    extract_records,
    find_first,
    now_iso,
    progress,
    read_jsonl,
    remove_image_fields,
    write_json,
    write_jsonl,
)


SEARCH_SERVICE = "SVC03"
DETAIL_SERVICE = "SVC07"
SEARCH_SIGNATURES = ("insectKey", "insectKorName", "cropCode", "cropName")
DETAIL_SIGNATURES = (
    "insectKey",
    "insectSpeciesKor",
    "cropName",
    "damageInfo",
    "ecologyInfo",
    "preventMethod",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect all NCPMS pest-insect search rows, unique details, and plant relations."
    )
    add_client_arguments(parser)
    parser.add_argument("--plants", type=Path)
    parser.add_argument("--limit-plants", type=int)
    parser.add_argument(
        "--max-discovered-names",
        type=int,
        default=2_000,
        help="Safety cap for crop names discovered from API responses.",
    )
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="Search only names from plant_codes.jsonl; do not enqueue response crop names.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--include-images", action="store_true")
    return parser.parse_args()


def clean(row: dict[str, Any], include_images: bool) -> dict[str, Any]:
    return row if include_images else remove_image_fields(row)


def extract_natural_enemies(detail: dict[str, Any]) -> list[dict[str, str | None]]:
    value = detail.get("enemyInsectList")
    rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    enemies: list[dict[str, str | None]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = find_first(row, "enemyInsectSpeciesKor")
        insect_key = find_first(row, "insectKey")
        scientific_name = find_first(row, "enemyInsectSpecies")
        if name or insect_key or scientific_name:
            enemies.append(
                {
                    "name": name or None,
                    "insect_key": insect_key or None,
                    "scientific_name": scientific_name or None,
                }
            )
    return enemies


def main() -> int:
    args = parse_args()
    output = args.output_dir
    plants_path = args.plants or output / "plant_codes.jsonl"
    catalog_plants = read_jsonl(plants_path)
    plants = catalog_plants
    if args.limit_plants:
        plants = catalog_plants[: args.limit_plants]

    if args.dry_run:
        print(
            json.dumps(
                {
                    "plants": str(plants_path),
                    "plant_count": len(plants),
                    "search_service": SEARCH_SERVICE,
                    "detail_service": DETAIL_SERVICE,
                    "outputs": [
                        str(output / "insect_search.jsonl"),
                        str(output / "insect_details.jsonl"),
                        str(output / "plant_insect_relations.jsonl"),
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    client = build_client(args)
    search_rows: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    review_required_relations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    plants_by_code = {
        str(plant.get("crop_code") or "").strip(): plant
        for plant in catalog_plants
        if str(plant.get("crop_code") or "").strip()
    }
    pending_names: deque[str] = deque()
    queued_names: set[str] = set()
    for plant in plants:
        catalog_crop_name = str(plant.get("crop_name") or "").strip()
        if not catalog_crop_name:
            errors.append(
                {
                    "stage": "search",
                    "crop_code": plant.get("crop_code"),
                    "catalog_crop_name": None,
                    "error": "plant record has no crop_name",
                    "collected_at": now_iso(),
                }
            )
            continue
        if catalog_crop_name not in queued_names:
            queued_names.add(catalog_crop_name)
            pending_names.append(catalog_crop_name)

    initial_search_name_count = len(queued_names)
    searched_names: set[str] = set()
    while pending_names:
        query_crop_name = pending_names.popleft()
        if query_crop_name in searched_names:
            continue
        searched_names.add(query_crop_name)
        progress(
            f"[insect search name {len(searched_names)}/"
            f"{len(searched_names) + len(pending_names)}] {query_crop_name}"
        )
        try:
            rows = client.paged_records(
                SEARCH_SERVICE,
                params={"cropName": query_crop_name},
                signature_fields=SEARCH_SIGNATURES,
                page_size=args.page_size,
                max_pages=args.max_pages,
            )
        except Exception as exc:
            errors.append(
                {
                    "stage": "search",
                    "query_crop_name": query_crop_name,
                    "error": str(exc),
                    "collected_at": now_iso(),
                }
            )
            continue

        for row in rows:
            insect_key = find_first(row, "insectKey", "insect_key")
            insect_name = find_first(row, "insectKorName", "insectSpeciesKor")
            source_crop_code = find_first(row, "cropCode", "crop_code")
            source_crop_name = find_first(row, "cropName")
            if not insect_key:
                continue
            if (
                source_crop_name
                and not args.no_discovery
                and source_crop_name not in queued_names
                and len(queued_names) < args.max_discovered_names
            ):
                queued_names.add(source_crop_name)
                pending_names.append(source_crop_name)
            search_rows.append(
                {
                    "insect_key": insect_key,
                    "insect_name_ko": insect_name or None,
                    "crop_code": source_crop_code or None,
                    "crop_name": source_crop_name or None,
                    "query_crop_name": query_crop_name,
                    "collected_at": now_iso(),
                    "raw_record": clean(row, args.include_images),
                }
            )
            target_plant = plants_by_code.get(source_crop_code)
            relation = {
                "plant_key": (
                    target_plant.get("plant_key")
                    if target_plant
                    else f"ncpms:{source_crop_code}"
                    if source_crop_code
                    else None
                ),
                "crop_code": source_crop_code or None,
                "crop_name": source_crop_name or None,
                "catalog_crop_name": (
                    target_plant.get("crop_name") if target_plant else None
                ),
                "query_crop_name": query_crop_name,
                "insect_key": insect_key,
                "insect_name_ko": insect_name or None,
                "match_method": (
                    "ncpms_response_crop_code"
                    if target_plant
                    else "response_crop_code_missing"
                    if not source_crop_code
                    else "response_crop_code_not_in_official_catalog"
                ),
                "review_status": "verified" if target_plant else "review_required",
                "source_service": SEARCH_SERVICE,
                "collected_at": now_iso(),
            }
            if target_plant:
                relations.append(relation)
            else:
                review_required_relations.append(relation)

    search_rows = dedupe_rows(
        search_rows, ("insect_key", "crop_code", "query_crop_name")
    )
    relations = dedupe_rows(relations, ("plant_key", "insect_key"))
    review_required_relations = dedupe_rows(
        review_required_relations, ("crop_code", "crop_name", "insect_key")
    )
    write_jsonl(output / "insect_search.jsonl", search_rows)
    write_jsonl(output / "plant_insect_relations.jsonl", relations)

    details_path = output / "insect_details.jsonl"
    details = read_jsonl(details_path) if args.resume else []
    completed = {str(row.get("insect_key")) for row in details if row.get("insect_key")}
    insect_keys = sorted({str(row["insect_key"]) for row in relations})

    for index, insect_key in enumerate(insect_keys, 1):
        if insect_key in completed:
            continue
        progress(f"[insect detail {index}/{len(insect_keys)}] {insect_key}")
        try:
            payload = client.request(DETAIL_SERVICE, {"insectKey": insect_key})
            rows = extract_records(payload, DETAIL_SIGNATURES)
            if not rows and isinstance(payload, dict):
                rows = [payload]
            detail = rows[0] if rows else {}
            natural_enemies = extract_natural_enemies(detail)
            details.append(
                {
                    "insect_key": insect_key,
                    "crop_name": find_first(detail, "cropName") or None,
                    "insect_name_ko": find_first(
                        detail, "insectSpeciesKor", "insectKorName"
                    )
                    or None,
                    "scientific_name": find_first(
                        detail, "insectSpecies", "speciesName"
                    )
                    or None,
                    "order": find_first(detail, "insectOrder") or None,
                    "family": find_first(detail, "insectFamily") or None,
                    "distribution": find_first(detail, "distrbInfo") or None,
                    "morphology": find_first(detail, "stleInfo") or None,
                    "ecology": find_first(detail, "ecologyInfo") or None,
                    "damage": find_first(detail, "damageInfo") or None,
                    "prevention_method": find_first(
                        detail, "preventMethod", "preventionMethod"
                    )
                    or None,
                    "natural_enemy": next(
                        (row["name"] for row in natural_enemies if row.get("name")),
                        None,
                    ),
                    "natural_enemies": natural_enemies,
                    "collected_at": now_iso(),
                    "source_service": DETAIL_SERVICE,
                    "raw_record": clean(detail, args.include_images),
                }
            )
            completed.add(insect_key)
            if args.resume:
                write_jsonl(details_path, details)
        except Exception as exc:
            errors.append(
                {
                    "stage": "detail",
                    "insect_key": insect_key,
                    "error": str(exc),
                    "collected_at": now_iso(),
                }
            )

    for detail_row in details:
        natural_enemies = extract_natural_enemies(detail_row.get("raw_record") or {})
        detail_row["natural_enemies"] = natural_enemies
        detail_row["natural_enemy"] = next(
            (row["name"] for row in natural_enemies if row.get("name")),
            detail_row.get("natural_enemy"),
        )
    details = dedupe_rows(details, ("insect_key",))
    write_jsonl(details_path, details)
    write_jsonl(
        output / "insect_relation_review_required.jsonl",
        review_required_relations,
    )
    write_json(output / "insect_errors.json", errors)
    write_json(
        output / "insect_summary.json",
        {
            "collected_at": now_iso(),
            "plants_attempted": len(plants),
            "plants_with_insect": len({row["plant_key"] for row in relations}),
            "search_names_attempted": len(searched_names),
            "discovered_search_names": max(
                0, len(searched_names) - initial_search_name_count
            ),
            "search_rows": len(search_rows),
            "unique_insect_keys": len(insect_keys),
            "details": len(details),
            "relations": len(relations),
            "review_required_relations": len(review_required_relations),
            "errors": len(errors),
        },
    )
    progress(f"[insect] wrote {len(details)} details and {len(relations)} relations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
