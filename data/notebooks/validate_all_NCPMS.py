from __future__ import annotations

import argparse
from pathlib import Path

from all_ncpms_common import OUTPUT_DIR, read_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate pre-embedding all_NCPMS outputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def unique(rows: list[dict], field: str) -> set[str]:
    return {str(row[field]) for row in rows if row.get(field)}


def main() -> int:
    args = parse_args()
    base = args.output_dir
    files = {
        "plants": base / "plant_codes.jsonl",
        "disease_details": base / "disease_details.jsonl",
        "disease_relations": base / "plant_disease_relations.jsonl",
        "insect_details": base / "insect_details.jsonl",
        "insect_relations": base / "plant_insect_relations.jsonl",
    }
    missing = [str(path) for path in files.values() if not path.exists()]
    if missing and not args.allow_empty:
        raise SystemExit("Missing outputs:\n- " + "\n- ".join(missing))

    rows = {name: read_jsonl(path) for name, path in files.items()}
    if not args.allow_empty and not rows["plants"]:
        raise SystemExit("plant_codes.jsonl is empty")

    plant_keys = unique(rows["plants"], "plant_key")
    disease_keys = unique(rows["disease_details"], "sick_key")
    insect_keys = unique(rows["insect_details"], "insect_key")

    errors: list[str] = []
    for relation in rows["disease_relations"]:
        if relation.get("plant_key") not in plant_keys:
            errors.append(f"unknown disease relation plant_key: {relation.get('plant_key')}")
        if relation.get("sick_key") not in disease_keys:
            errors.append(f"missing disease detail: {relation.get('sick_key')}")
    for relation in rows["insect_relations"]:
        if relation.get("plant_key") not in plant_keys:
            errors.append(f"unknown insect relation plant_key: {relation.get('plant_key')}")
        if relation.get("insect_key") not in insect_keys:
            errors.append(f"missing insect detail: {relation.get('insect_key')}")

    if errors:
        raise SystemExit("Validation failed:\n- " + "\n- ".join(errors[:100]))

    print(
        "validation ok:",
        f"plants={len(rows['plants'])}",
        f"diseases={len(rows['disease_details'])}",
        f"disease_relations={len(rows['disease_relations'])}",
        f"insects={len(rows['insect_details'])}",
        f"insect_relations={len(rows['insect_relations'])}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
