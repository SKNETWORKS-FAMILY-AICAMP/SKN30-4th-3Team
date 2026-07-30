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


SEARCH_SERVICE = "SVC01"
DETAIL_SERVICE = "SVC05"
SEARCH_SIGNATURES = ("sickKey", "sickNameKor", "cropCode", "cropName")
DETAIL_SIGNATURES = (
    "sickKey",
    "sickNameKor",
    "cropName",
    "symptoms",
    "developmentCondition",
    "preventionMethod",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect all NCPMS disease search rows, unique details, and plant relations."
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


def extract_pathogens(detail: dict[str, Any]) -> list[dict[str, str | None]]:
    value = detail.get("virusList")
    rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    pathogens: list[dict[str, str | None]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = find_first(row, "virusName", "pathogenName")
        description = find_first(row, "sfeNm", "description")
        if name or description:
            pathogens.append(
                {
                    "name": name or None,
                    "description": description or None,
                }
            )
    return pathogens


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
                        str(output / "disease_search.jsonl"),
                        str(output / "disease_details.jsonl"),
                        str(output / "plant_disease_relations.jsonl"),
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
            f"[disease search name {len(searched_names)}/"
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
            sick_key = find_first(row, "sickKey", "sick_key")
            disease_name = find_first(row, "sickNameKor", "disease_name")
            source_crop_code = find_first(row, "cropCode", "crop_code")
            source_crop_name = find_first(row, "cropName")
            if not sick_key:
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
                    "sick_key": sick_key,
                    "disease_name_ko": disease_name or None,
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
                "sick_key": sick_key,
                "disease_name_ko": disease_name or None,
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
        search_rows, ("sick_key", "crop_code", "query_crop_name")
    )
    relations = dedupe_rows(relations, ("plant_key", "sick_key"))
    review_required_relations = dedupe_rows(
        review_required_relations, ("crop_code", "crop_name", "sick_key")
    )
    write_jsonl(output / "disease_search.jsonl", search_rows)
    write_jsonl(output / "plant_disease_relations.jsonl", relations)

    details_path = output / "disease_details.jsonl"
    details = read_jsonl(details_path) if args.resume else []
    completed = {str(row.get("sick_key")) for row in details if row.get("sick_key")}
    sick_keys = sorted({str(row["sick_key"]) for row in relations})

    for index, sick_key in enumerate(sick_keys, 1):
        if sick_key in completed:
            continue
        progress(f"[disease detail {index}/{len(sick_keys)}] {sick_key}")
        try:
            payload = client.request(DETAIL_SERVICE, {"sickKey": sick_key})
            rows = extract_records(payload, DETAIL_SIGNATURES)
            if not rows and isinstance(payload, dict):
                rows = [payload]
            detail = rows[0] if rows else {}
            pathogens = extract_pathogens(detail)
            details.append(
                {
                    "sick_key": sick_key,
                    "crop_name": find_first(detail, "cropName") or None,
                    "disease_name_ko": find_first(detail, "sickNameKor") or None,
                    "disease_name_en": find_first(detail, "sickNameEng") or None,
                    "infection_route": find_first(detail, "infectionRoute") or None,
                    "development_condition": find_first(detail, "developmentCondition") or None,
                    "symptoms": find_first(detail, "symptoms") or None,
                    "prevention_method": find_first(detail, "preventionMethod") or None,
                    "biological_control": find_first(detail, "biologyPrvnbeMth") or None,
                    "chemical_control": find_first(detail, "chemicalPrvnbeMth") or None,
                    "pathogen_name": next(
                        (row["name"] for row in pathogens if row.get("name")),
                        None,
                    ),
                    "pathogens": pathogens,
                    "collected_at": now_iso(),
                    "source_service": DETAIL_SERVICE,
                    "raw_record": clean(detail, args.include_images),
                }
            )
            completed.add(sick_key)
            if args.resume:
                write_jsonl(details_path, details)
        except Exception as exc:
            errors.append(
                {
                    "stage": "detail",
                    "sick_key": sick_key,
                    "error": str(exc),
                    "collected_at": now_iso(),
                }
            )

    for detail_row in details:
        pathogens = extract_pathogens(detail_row.get("raw_record") or {})
        detail_row["pathogens"] = pathogens
        detail_row["pathogen_name"] = next(
            (row["name"] for row in pathogens if row.get("name")),
            detail_row.get("pathogen_name"),
        )
    details = dedupe_rows(details, ("sick_key",))
    write_jsonl(details_path, details)
    write_jsonl(
        output / "disease_relation_review_required.jsonl",
        review_required_relations,
    )
    write_json(output / "disease_errors.json", errors)
    write_json(
        output / "disease_summary.json",
        {
            "collected_at": now_iso(),
            "plants_attempted": len(plants),
            "plants_with_disease": len({row["plant_key"] for row in relations}),
            "search_names_attempted": len(searched_names),
            "discovered_search_names": max(
                0, len(searched_names) - initial_search_name_count
            ),
            "search_rows": len(search_rows),
            "unique_sick_keys": len(sick_keys),
            "details": len(details),
            "relations": len(relations),
            "review_required_relations": len(review_required_relations),
            "errors": len(errors),
        },
    )
    progress(f"[disease] wrote {len(details)} details and {len(relations)} relations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
