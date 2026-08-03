"""
기존 NCPMS 전체 수집 결과와 누락 식물 추가 수집 결과를 중복 제거하여 병합합니다.

원본 폴더는 수정하지 않고 별도의 병합 폴더를 만듭니다.

실행:
    python data/notebooks/NCPMS_merge_missing_results.py

입력:
    data/interim/all_NCPMS
    data/interim/all_NCPMS_missing

출력:
    data/interim/all_NCPMS_merged

병합 후:
    python data/notebooks/validate_all_NCPMS.py `
      --output-dir data/interim/all_NCPMS_merged
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = ROOT / "data/interim/all_NCPMS"
ADDITION_DIR = ROOT / "data/interim/all_NCPMS_missing"
OUTPUT_DIR = ROOT / "data/interim/all_NCPMS_merged"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def dedupe(rows: Iterable[dict], fields: tuple[str, ...]) -> list[dict]:
    result: list[dict] = []
    positions: dict[tuple[str, ...], int] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in fields)
        if key in positions:
            # 추가 수집분에 더 최신/완전한 값이 있으면 같은 위치를 교체합니다.
            result[positions[key]] = row
        else:
            positions[key] = len(result)
            result.append(row)
    return result


def prepare_output() -> None:
    if not BASE_DIR.exists():
        raise FileNotFoundError(BASE_DIR)
    if not ADDITION_DIR.exists():
        raise FileNotFoundError(ADDITION_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in BASE_DIR.iterdir():
        if path.is_file():
            shutil.copy2(path, OUTPUT_DIR / path.name)


def merge_plants_and_remap_relations(
    disease_relations: list[dict],
    insect_relations: list[dict],
) -> tuple[list[dict], list[dict], list[dict], int]:
    plants = read_jsonl(BASE_DIR / "plant_codes.jsonl")
    by_code = {
        str(row.get("crop_code") or "").strip(): row
        for row in plants
        if str(row.get("crop_code") or "").strip()
    }
    added = 0

    for relation in [*disease_relations, *insect_relations]:
        crop_code = str(relation.get("crop_code") or "").strip()
        crop_name = str(relation.get("crop_name") or relation.get("query_crop_name") or "").strip()
        if not crop_code:
            continue
        if crop_code not in by_code:
            plant = {
                "aliases": [],
                "code_status": "verified_exact_search_response",
                "crop_code": crop_code,
                "crop_name": crop_name,
                "discovered_at": relation.get("collected_at")
                or datetime.now(timezone.utc).isoformat(),
                "plant_key": f"ncpms:{crop_code}",
                "source": "NCPMS_exact_name_search_response",
                "source_url": "https://ncpms.rda.go.kr/npms/OpenApiInfo.np",
            }
            plants.append(plant)
            by_code[crop_code] = plant
            added += 1

        plant = by_code[crop_code]
        relation["plant_key"] = plant["plant_key"]
        relation["catalog_crop_name"] = relation.get("catalog_crop_name") or crop_name
        relation["review_status"] = "verified"
        if relation.get("match_method") == "exact_query_crop_name":
            relation["match_method"] = "exact_query_crop_name"

    return plants, disease_relations, insect_relations, added


def main() -> None:
    prepare_output()

    disease_relations_new = read_jsonl(
        ADDITION_DIR / "plant_disease_relations.jsonl"
    )
    insect_relations_new = read_jsonl(
        ADDITION_DIR / "plant_insect_relations.jsonl"
    )
    plants, disease_relations_new, insect_relations_new, added_plants = (
        merge_plants_and_remap_relations(
            disease_relations_new,
            insect_relations_new,
        )
    )

    specs = [
        (
            "disease_search.jsonl",
            ("sick_key", "crop_code", "query_crop_name"),
            read_jsonl(ADDITION_DIR / "disease_search.jsonl"),
        ),
        (
            "disease_details.jsonl",
            ("sick_key",),
            read_jsonl(ADDITION_DIR / "disease_details.jsonl"),
        ),
        (
            "plant_disease_relations.jsonl",
            ("plant_key", "sick_key"),
            disease_relations_new,
        ),
        (
            "insect_search.jsonl",
            ("insect_key", "crop_code", "query_crop_name"),
            read_jsonl(ADDITION_DIR / "insect_search.jsonl"),
        ),
        (
            "insect_details.jsonl",
            ("insect_key",),
            read_jsonl(ADDITION_DIR / "insect_details.jsonl"),
        ),
        (
            "plant_insect_relations.jsonl",
            ("plant_key", "insect_key"),
            insect_relations_new,
        ),
    ]

    counts: dict[str, dict[str, int]] = {}
    write_jsonl(OUTPUT_DIR / "plant_codes.jsonl", plants)
    for filename, key_fields, additions in specs:
        original = read_jsonl(BASE_DIR / filename)
        merged = dedupe([*original, *additions], key_fields)
        write_jsonl(OUTPUT_DIR / filename, merged)
        counts[filename] = {
            "original": len(original),
            "addition_input": len(additions),
            "merged": len(merged),
            "net_added": len(merged) - len(original),
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_dir": str(BASE_DIR),
        "addition_dir": str(ADDITION_DIR),
        "output_dir": str(OUTPUT_DIR),
        "supplemental_plant_codes_added": added_plants,
        "files": counts,
    }
    (OUTPUT_DIR / "missing_merge_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
