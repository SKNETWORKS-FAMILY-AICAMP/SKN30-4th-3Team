from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from all_ncpms_common import (
    OUTPUT_DIR,
    add_client_arguments,
    download_bytes,
    extract_ncpms_crop_codes_from_manual_zip,
    find_first,
    normalize_name,
    now_iso,
    read_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_MANUAL_URL = "https://ncpms.rda.go.kr/download/npms_api_manual.zip"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download the official NCPMS OpenAPI manual ZIP and extract every "
            "cropCode row from its crop-code XLSX sheet."
        )
    )
    add_client_arguments(parser)
    parser.add_argument("--manual-url", default=DEFAULT_MANUAL_URL)
    parser.add_argument(
        "--manual-zip",
        type=Path,
        help="Use an already downloaded npms_api_manual.zip instead of the network.",
    )
    parser.add_argument(
        "--merge-seed",
        action="store_true",
        help=(
            "Append local priority plants that are absent from the official NCPMS "
            "code list. These rows are marked not_in_ncpms_code_list."
        ),
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "catalog" / "priority_plant_catalog.jsonl",
    )
    return parser.parse_args()


def normalize_official(row: dict[str, str], source_hash: str) -> dict[str, Any]:
    return {
        "plant_key": f"ncpms:{row['crop_code']}",
        "crop_code": row["crop_code"],
        "crop_name": row["crop_name"],
        "aliases": [],
        "source": "NCPMS_official_code_list",
        "source_url": DEFAULT_MANUAL_URL,
        "code_status": "official",
        "source_sha256": source_hash,
        "discovered_at": now_iso(),
    }


def normalize_seed(row: dict[str, Any]) -> dict[str, Any] | None:
    crop_name = find_first(row, "name_ko")
    if not crop_name:
        return None
    aliases = row.get("aliases") if isinstance(row.get("aliases"), list) else []
    return {
        "plant_key": f"name:{normalize_name(crop_name)}",
        "crop_code": None,
        "crop_name": crop_name,
        "scientific_name": find_first(row, "name_scientific") or None,
        "english_name": find_first(row, "name_en") or None,
        "family": find_first(row, "family") or None,
        "category_name": row.get("category"),
        "aliases": aliases,
        "source": "farmhani_priority_plant_seed",
        "source_url": str(Path("data/catalog/priority_plant_catalog.jsonl")),
        "code_status": "not_in_ncpms_code_list",
        "discovered_at": now_iso(),
    }


def main() -> int:
    args = parse_args()
    output = args.output_dir or OUTPUT_DIR
    if args.dry_run:
        print(
            json.dumps(
                {
                    "source": str(args.manual_zip) if args.manual_zip else args.manual_url,
                    "official_output": str(output / "plant_codes.official.jsonl"),
                    "merged_output": str(output / "plant_codes.jsonl"),
                    "merge_seed": args.merge_seed,
                    "expected_official_rows": 760,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    manual_zip = (
        args.manual_zip.read_bytes()
        if args.manual_zip
        else download_bytes(args.manual_url, timeout=max(args.timeout, 60), retries=args.retries)
    )
    source_hash = hashlib.sha256(manual_zip).hexdigest()
    extracted = extract_ncpms_crop_codes_from_manual_zip(manual_zip)
    official = [normalize_official(row, source_hash) for row in extracted]
    official.sort(key=lambda row: row["crop_code"])

    duplicate_codes = len(official) - len({row["crop_code"] for row in official})
    duplicate_pairs = len(official) - len(
        {(row["crop_code"], row["crop_name"]) for row in official}
    )
    if duplicate_codes or duplicate_pairs:
        raise RuntimeError(
            f"Official crop list has duplicates: codes={duplicate_codes}, pairs={duplicate_pairs}"
        )

    catalog = list(official)
    review_required: list[dict[str, Any]] = []
    if args.merge_seed:
        official_names = {normalize_name(row["crop_name"]) for row in official}
        for source_row in read_jsonl(args.seed):
            seed_row = normalize_seed(source_row)
            if not seed_row or normalize_name(seed_row["crop_name"]) in official_names:
                continue
            catalog.append(seed_row)
            review_required.append(seed_row)

    write_jsonl(output / "plant_codes.official.jsonl", official)
    write_jsonl(output / "plant_codes.jsonl", catalog)
    write_jsonl(output / "plant_codes.review_required.jsonl", review_required)
    write_json(
        output / "plant_code_summary.json",
        {
            "collected_at": now_iso(),
            "source_url": args.manual_url,
            "source_sha256": source_hash,
            "official_crop_codes": len(official),
            "merged_catalog_rows": len(catalog),
            "local_seed_rows_appended": len(review_required),
            "code_status_counts": {
                "official": len(official),
                "not_in_ncpms_code_list": len(review_required),
            },
            "scope_note": (
                "This is the complete cropCode sheet distributed in the current "
                "official NCPMS OpenAPI manual. It is complete for that published "
                "code list, not for every botanical species in existence."
            ),
        },
    )
    print(f"[plant] extracted {len(official)} official NCPMS crop codes into {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
