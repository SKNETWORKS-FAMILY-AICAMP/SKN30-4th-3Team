"""
아래 명령어를 PowerShell에서 실행하면,
all_PSIS에 수집된 SVC01 농약 등록 목록과 SVC02 상세정보를 불러와
Supabase 농약 단일 테이블에 적재하기 위한 최종 JSONL을 생성합니다.

잘 모르겠으면 PSIS_new_table.md를 참고하세요

python data/notebooks/PSIS_new_table.py

**입력 파일**

- data/interim/all_PSIS/pesticide_registration_list.jsonl
  SVC01에서 수집한 작물, 대상 병해충, 농약 제품 및 사용방법 목록입니다.

- data/interim/all_PSIS/pesticide_registration_details.jsonl
  SVC02에서 수집한 독성, 어독성 및 상세 성분 정보입니다.
  파일이 없거나 아직 수집 중이어도 SVC01 데이터만으로 실행할 수 있습니다.

- data/interim/all_PSIS/ncpms_psis_crop_mapping.jsonl
  NCPMS/Supabase 식물명과 PSIS 작물명을 연결할 때 사용합니다.

- data/interim/all_PSIS/crops.jsonl
  같은 PSIS 작물에 연결된 식물명과 검증된 별칭을 crop_or_plant에
  모을 때 사용합니다.

**가공 기준**

- PSIS 작물 코드 + 대상 병해충 + 용도를 기준으로 한 그룹을 만듭니다.
- 서비스 식물명과 검증된 별칭은 crop_or_plant 리스트에 저장합니다.
- 같은 crop_code + pesti_code + disease_use_seq 등록정보는 중복 제거합니다.
- 제품별 사용방법과 독성 정보는 registrations 리스트에 보존합니다.
- SVC02 상세정보가 있으면 pesti_code를 기준으로 제품 공통 독성정보를
  SVC01의 모든 등록정보에 자동 병합합니다.
- 사용시기, 희석배수, 사용횟수, 수확 전 안전사용기간은 작물·병해충
  등록마다 달라질 수 있으므로 SVC01 값을 그대로 유지합니다.

**출력 파일**

- data/interim/all_PSIS/PSIS_result/psis_pesticide_groups.jsonl
  Supabase 농약 단일 테이블에 적재할 최종 결과입니다.

- data/interim/all_PSIS/PSIS_result/summary.json
  원본 행, 중복 제거, 최종 그룹, SVC02 병합 및 독성정보 통계입니다.

**주요 옵션**

--input
  기본 SVC01 입력 파일 대신 다른 pesticide_registration_list.jsonl을
  사용할 때 지정합니다.

--details
  기본 SVC02 입력 파일 대신 다른 pesticide_registration_details.jsonl을
  사용할 때 지정합니다.

--mapping
  다른 NCPMS-PSIS 작물 매핑 JSONL을 사용할 때 지정합니다.

--crops
  다른 PSIS 작물 JSONL을 사용할 때 지정합니다.

--output-dir
  최종 JSONL과 summary.json을 저장할 폴더를 변경합니다.

--output-name
  최종 JSONL 파일명을 변경합니다.

**사용 예시**

기본 경로로 최종 결과 생성:

python data/notebooks/PSIS_new_table.py

별도 상세 파일과 출력 폴더 사용:

python data/notebooks/PSIS_new_table.py `
  --details data/interim/all_PSIS/pesticide_registration_details.jsonl `
  --output-dir data/interim/all_PSIS/PSIS_result
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DATA_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = DATA_DIR / "interim" / "all_PSIS" / "pesticide_registration_list.jsonl"
DEFAULT_MAPPING = DATA_DIR / "interim" / "all_PSIS" / "ncpms_psis_crop_mapping.jsonl"
DEFAULT_CROPS = DATA_DIR / "interim" / "all_PSIS" / "crops.jsonl"
DEFAULT_DETAILS = (
    DATA_DIR
    / "interim"
    / "all_PSIS"
    / "pesticide_registration_details.jsonl"
)
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "all_PSIS" / "PSIS_result"
DEFAULT_OUTPUT_NAME = "psis_pesticide_groups.jsonl"

REGISTRATION_FIELDS = (
    "pesti_code",
    "disease_use_seq",
    "pesticide_name",
    "brand_name",
    "company_name",
    "active_ingredient",
    "mode_of_action",
    "manufacture_import_type",
    "first_registration_date",
    "application_timing",
    "dilution_or_dose",
    "max_use_count",
    "preharvest_interval",
    "wafindex",
    "active_ingredient_amount",
    "toxicity_code",
    "toxicity_name",
    "fish_toxicity",
    "detail_collected_at",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Group PSIS registrations for one Supabase pesticide table."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--crops", type=Path, default=DEFAULT_CROPS)
    parser.add_argument(
        "--details",
        type=Path,
        default=DEFAULT_DETAILS,
        help=(
            "Optional SVC02 JSONL. When present, toxicity and detail fields "
            "are merged by pesti_code."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = clean(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def read_jsonl(path: Path, *, required: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required JSONL does not exist: {path}")
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def stable_id(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def registration_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    """Identity of a PSIS crop registration, independent of service aliases."""
    return (
        clean(row.get("crop_code")),
        clean(row.get("pesti_code")),
        clean(row.get("disease_use_seq")),
    )


def build_details_index(
    detail_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    product_fields = (
        "pesticide_name",
        "brand_name",
        "company_name",
        "active_ingredient",
        "active_ingredient_amount",
        "toxicity_code",
        "toxicity_name",
        "fish_toxicity",
    )
    for row in detail_rows:
        pesti_code = clean(row.get("pesti_code"))
        if not pesti_code:
            continue
        previous = result.get(pesti_code)
        if previous is None:
            result[pesti_code] = row
            continue
        for field in product_fields:
            previous_value = clean(previous.get(field))
            current_value = clean(row.get(field))
            if (
                previous_value
                and current_value
                and previous_value != current_value
            ):
                raise ValueError(
                    "Conflicting product-level SVC02 value for "
                    f"pesti_code={pesti_code}, field={field}: "
                    f"{previous_value!r} != {current_value!r}"
                )
            if not previous_value and current_value:
                previous[field] = row.get(field)
    return result


def registration_payload(
    row: dict[str, Any],
    details_by_key: dict[str, dict[str, Any]],
) -> dict[str, str]:
    pesti_code = clean(row.get("pesti_code"))
    detail = details_by_key.get(pesti_code) or {}
    payload = {field: clean(row.get(field)) for field in REGISTRATION_FIELDS}

    # Only product-level values are propagated from the representative SVC02
    # response. Usage instructions remain registration-specific SVC01 values.
    for field in (
        "pesticide_name",
        "brand_name",
        "company_name",
        "active_ingredient",
        "active_ingredient_amount",
        "toxicity_code",
        "toxicity_name",
        "fish_toxicity",
    ):
        detail_value = clean(detail.get(field))
        if detail_value:
            payload[field] = detail_value
    payload["detail_collected_at"] = clean(detail.get("collected_at"))
    return payload


def build_crop_aliases(
    rows: list[dict[str, Any]],
    mapping_rows: list[dict[str, Any]],
    crop_rows: list[dict[str, Any]],
) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        crop_code = clean(row.get("crop_code"))
        aliases[crop_code].extend(
            [
                row.get("crop_name"),
                row.get("query_crop_name"),
                row.get("ncpms_crop_name"),
            ]
        )

    for row in mapping_rows:
        crop_code = clean(row.get("psis_crop_code"))
        aliases[crop_code].extend(
            [
                row.get("psis_crop_name"),
                row.get("query_crop_name"),
                row.get("ncpms_crop_name"),
            ]
        )

    for row in crop_rows:
        crop_code = clean(row.get("crop_code"))
        aliases[crop_code].extend(row.get("crop_names") or [])
        aliases[crop_code].append(row.get("crop_name"))
        for plant in row.get("ncpms_plants") or []:
            if isinstance(plant, dict):
                aliases[crop_code].append(plant.get("crop_name"))

    return {crop_code: unique_strings(names) for crop_code, names in aliases.items()}


def build_rows(
    source_rows: list[dict[str, Any]],
    crop_aliases: dict[str, list[str]],
    details_by_key: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        key = (
            clean(row.get("crop_code")),
            clean(row.get("target_disease_pest")),
            clean(row.get("use_type")),
        )
        if not all(key):
            raise ValueError(f"Required grouping value is empty: {key!r}")
        groups[key].append(row)

    output: list[dict[str, Any]] = []
    duplicate_count = 0

    for (crop_code, target, use_type), rows in sorted(groups.items()):
        first = rows[0]
        crop_name = clean(first.get("crop_name"))
        crop_or_plant = unique_strings([crop_name, *crop_aliases.get(crop_code, [])])

        registrations_by_id: dict[tuple[str, str, str], dict[str, str]] = {}
        for row in rows:
            identity = registration_identity(row)
            payload = registration_payload(row, details_by_key)
            previous = registrations_by_id.get(identity)
            if previous is None:
                registrations_by_id[identity] = payload
            elif previous == payload:
                duplicate_count += 1
            else:
                raise ValueError(
                    "Conflicting registration values for "
                    f"crop_code={identity[0]}, pesti_code={identity[1]}, "
                    f"disease_use_seq={identity[2]}"
                )

        registrations = sorted(
            registrations_by_id.values(),
            key=lambda item: (
                item["brand_name"],
                item["pesticide_name"],
                item["pesti_code"],
                item["disease_use_seq"],
            ),
        )
        pesticide_names = unique_strings(
            item["brand_name"] or item["pesticide_name"] for item in registrations
        )
        active_ingredients = unique_strings(
            item["active_ingredient"] for item in registrations
        )
        group_id = stable_id(crop_code, target, use_type)
        source_url = clean(first.get("source_url"))
        collected_at = clean(first.get("collected_at"))

        # Keep embedding/search text limited to fields that identify the
        # pesticide group. Registration counts, repeated safety notices, and
        # exact label details belong to structured columns and are rendered
        # only after the table query.
        text = (
            "문서 유형: PSIS 농약 등록정보\n"
            f"작물: {', '.join(crop_or_plant)}\n"
            f"대상 병해충: {target}\n"
            f"용도: {use_type}"
        )
        symptom_keywords = unique_strings(
            [target, use_type, "농약", "방제", "병해충"]
        )

        output.append(
            {
                "id": group_id,
                "group_key": f"{crop_code}:{target}:{use_type}",
                "crop_code": crop_code,
                "crop_name": crop_name,
                "crop_or_plant": crop_or_plant,
                "target_disease_pest": target,
                "use_type": use_type,
                "symptom_keywords": symptom_keywords,
                "registration_count": len(registrations),
                "pesticide_names": pesticide_names,
                "active_ingredients": active_ingredients,
                "registrations": registrations,
                "text": text,
                "source_service": clean(first.get("source_service")) or "SVC01",
                "source_url": source_url,
                "collected_at": collected_at,
                "safety_tags": [
                    "not_diagnosis",
                    "pesticide_caution",
                    "label_check_required",
                    "expert_check_required",
                ],
                "metadata": {
                    "category": "pesticide_registration",
                    "section": "registered_pesticides",
                    "cropCode": crop_code,
                    "cropName": crop_name,
                    "cropOrPlant": crop_or_plant,
                    "targetDiseasePest": target,
                    "useType": use_type,
                    "registrationCount": len(registrations),
                    "sourceService": clean(first.get("source_service")) or "SVC01",
                    "sourceUrl": source_url,
                    "safetyTags": [
                        "not_diagnosis",
                        "pesticide_caution",
                        "label_check_required",
                        "expert_check_required",
                    ],
                },
            }
        )

    stats = {
        "source_rows": len(source_rows),
        "output_groups": len(output),
        "output_registrations": sum(row["registration_count"] for row in output),
        "removed_duplicate_rows": duplicate_count,
        "unique_crops": len({row["crop_code"] for row in output}),
        "unique_targets": len({row["target_disease_pest"] for row in output}),
        "details_available": len(details_by_key),
        "details_matched": sum(
            1
            for row in output
            for registration in row["registrations"]
            if registration["detail_collected_at"]
        ),
        "toxicity_name_present": sum(
            1
            for row in output
            for registration in row["registrations"]
            if registration["toxicity_name"]
        ),
        "fish_toxicity_present": sum(
            1
            for row in output
            for registration in row["registrations"]
            if registration["fish_toxicity"]
        ),
    }
    return output, stats


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def main() -> int:
    args = parse_args()
    source_rows = read_jsonl(args.input)
    mapping_rows = read_jsonl(args.mapping, required=False)
    crop_rows = read_jsonl(args.crops, required=False)
    detail_rows = read_jsonl(args.details, required=False)

    crop_aliases = build_crop_aliases(source_rows, mapping_rows, crop_rows)
    details_by_key = build_details_index(detail_rows)
    output_rows, stats = build_rows(
        source_rows,
        crop_aliases,
        details_by_key,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / args.output_name
    summary_path = args.output_dir / "summary.json"
    write_jsonl(output_path, output_rows)

    summary = {
        **stats,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grouping": ["crop_code", "target_disease_pest", "use_type"],
        "output_file": str(output_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
