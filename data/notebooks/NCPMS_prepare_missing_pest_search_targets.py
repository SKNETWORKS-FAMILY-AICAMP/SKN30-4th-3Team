"""
현재 Supabase 병·해충 커버리지에서 누락된 식물의 단일 검색명 목록을 생성합니다.

이 코드는 NCPMS API를 호출하지 않습니다. 다음 수집 단계에서 사용할 검색 대상만
준비하며, 별칭 확장이나 유사 이름 추론을 하지 않습니다.

검색명 선택 순서:
    1. plant_catalog.name과 정확히 일치하는 이름
    2. catalog에 없으면 기존 KNA 수집 결과의 query_name
    3. 둘 다 없으면 unmatched

선행 조건:
    1. update_supabase_plant_data_coverage.py를 실행하여 coverage MD 갱신
    2. .env에 SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY 또는 SUPABASE_KEY 설정
    3. KNA name_mapping JSON이 normalized 폴더에 존재

실행:
    python data/notebooks/NCPMS_prepare_missing_pest_search_targets.py

입력:
    data/interim/insect/list/plant_data_coverage.md
    data/interim/coverage_plant_grow_kna_api/normalized/*.name_mapping.json
    Supabase plant_catalog

출력:
    data/interim/insect/list/ncpms_missing_pest_search_targets.json
    data/interim/insect/list/ncpms_missing_disease_targets.jsonl
    data/interim/insect/list/ncpms_missing_insect_targets.jsonl
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
COVERAGE_PATH = ROOT / "data/interim/insect/list/plant_data_coverage.md"
KNA_MAPPING_DIR = ROOT / "data/interim/coverage_plant_grow_kna_api/normalized"
KNA_ROOT = ROOT / "data/interim/coverage_plant_grow_kna_api"
OUTPUT_PATH = ROOT / "data/interim/insect/list/ncpms_missing_pest_search_targets.json"
DISEASE_OUTPUT_PATH = (
    ROOT / "data/interim/insect/list/ncpms_missing_disease_targets.jsonl"
)
INSECT_OUTPUT_PATH = (
    ROOT / "data/interim/insect/list/ncpms_missing_insect_targets.jsonl"
)


def normalize_name(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch_catalog_names() -> set[str]:
    base_url = os.environ["SUPABASE_URL"].rstrip("/")
    api_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
    names: set[str] = set()
    offset = 0
    page_size = 1000

    while True:
        query = urlencode(
            {"select": "name", "limit": page_size, "offset": offset}
        )
        request = Request(
            f"{base_url}/rest/v1/plant_catalog?{query}",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=120) as response:
            page = json.loads(response.read().decode("utf-8"))
        for row in page:
            name = normalize_name(row.get("name"))
            if name:
                names.add(name)
        if len(page) < page_size:
            return names
        offset += page_size


def split_outside_parentheses(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return [normalize_name(part) for part in parts if normalize_name(part)]


def read_missing_coverage() -> list[dict]:
    if not COVERAGE_PATH.exists():
        raise FileNotFoundError(
            f"coverage 파일이 없습니다. 먼저 update_supabase_plant_data_coverage.py를 "
            f"실행하세요: {COVERAGE_PATH}"
        )

    rows: list[dict] = []
    for line in COVERAGE_PATH.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] == "대표 식물명":
            continue
        disease_missing = cells[3] == "X"
        insect_missing = cells[4] == "X"
        if not disease_missing and not insect_missing:
            continue
        rows.append(
            {
                "canonical_name": normalize_name(cells[0]),
                "aliases": (
                    []
                    if cells[1] in {"", "-"}
                    else split_outside_parentheses(cells[1])
                ),
                "search_disease": disease_missing,
                "search_insect": insect_missing,
            }
        )
    return rows


def load_kna_query_index() -> dict[str, str]:
    """KNA에서 exact_match로 실제 호출했던 검색명 하나만 연결합니다."""
    index: dict[str, str] = {}

    # 원본 수집 로그의 searched_term이 실제 KNA API에 전달했던 검색어입니다.
    raw_paths = sorted(KNA_ROOT.glob("*.collected.jsonl"))
    collected_path = KNA_ROOT / "plant_growth_api_collected.jsonl"
    if collected_path.exists():
        raw_paths.append(collected_path)
    for path in raw_paths:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("status") != "exact_match":
                continue
            search_name = normalize_name(row.get("searched_term") or row.get("query_name"))
            candidate = row.get("candidate") or {}
            result_name = normalize_name(candidate.get("result_name"))
            if not search_name:
                continue
            for exact_name in (
                normalize_name(row.get("query_name")),
                search_name,
                result_name,
            ):
                if exact_name:
                    index.setdefault(exact_name, search_name)

    if not KNA_MAPPING_DIR.exists():
        return index
    for path in sorted(KNA_MAPPING_DIR.glob("*.name_mapping.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        mappings = payload if isinstance(payload, list) else payload.get("mappings", [])
        for row in mappings:
            if not isinstance(row, dict):
                continue
            search_name = normalize_name(
                row.get("searched_term") or row.get("query_name")
            )
            if not search_name:
                continue
            # fuzzy 후보가 아니라 검증된 mapping 파일의 정확 필드만 사용합니다.
            for key in ("catalog_name", "api_result_name"):
                exact_name = normalize_name(row.get(key))
                if exact_name:
                    index.setdefault(exact_name, search_name)
    return index


def select_search_name(
    row: dict, catalog_names: set[str], kna_index: dict[str, str]
) -> tuple[str | None, str | None]:
    canonical_name = row["canonical_name"]

    # 대표명이 catalog에 있으면 다른 별칭은 살펴보지 않습니다.
    if canonical_name in catalog_names:
        return canonical_name, "plant_catalog"

    # 대표명은 없지만 coverage에 함께 표시된 실제 DB 이름이 catalog에 있을 수 있습니다.
    for alias in row["aliases"]:
        if alias in catalog_names:
            return alias, "plant_catalog"

    # catalog 정확 일치가 전혀 없을 때만 KNA에서 실제 사용했던 검색명을 사용합니다.
    for exact_name in [canonical_name, *row["aliases"]]:
        query_name = kna_index.get(exact_name)
        if query_name:
            return query_name, "kna_query_name"

    return None, None


def main() -> None:
    load_dotenv()
    catalog_names = fetch_catalog_names()
    missing_rows = read_missing_coverage()
    kna_index = load_kna_query_index()

    targets: list[dict] = []
    unmatched: list[dict] = []
    for row in missing_rows:
        search_name, source = select_search_name(row, catalog_names, kna_index)
        result = {
            "canonical_name": row["canonical_name"],
            "search_name": search_name,
            "name_source": source,
            "search_disease": row["search_disease"],
            "search_insect": row["search_insect"],
        }
        if search_name:
            targets.append(result)
        else:
            unmatched.append(result)

    targets.sort(key=lambda row: row["canonical_name"])
    unmatched.sort(key=lambda row: row["canonical_name"])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_rule": [
            "plant_catalog.name exact match",
            "KNA verified query_name",
            "otherwise unmatched",
        ],
        "coverage_input_count": len(missing_rows),
        "target_count": len(targets),
        "unmatched_count": len(unmatched),
        "targets": targets,
        "unmatched": unmatched,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    disease_targets = [
        {
            "plant_key": f"coverage:{row['canonical_name']}",
            "crop_name": row["search_name"],
            "canonical_name": row["canonical_name"],
            "name_source": row["name_source"],
        }
        for row in targets
        if row["search_disease"]
    ]
    insect_targets = [
        {
            "plant_key": f"coverage:{row['canonical_name']}",
            "crop_name": row["search_name"],
            "canonical_name": row["canonical_name"],
            "name_source": row["name_source"],
        }
        for row in targets
        if row["search_insect"]
    ]
    DISEASE_OUTPUT_PATH.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in disease_targets
        ),
        encoding="utf-8",
    )
    INSECT_OUTPUT_PATH.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in insect_targets
        ),
        encoding="utf-8",
    )
    print(OUTPUT_PATH)
    print(DISEASE_OUTPUT_PATH)
    print(INSECT_OUTPUT_PATH)
    print(
        json.dumps(
            {
                "coverage_input_count": len(missing_rows),
                "target_count": len(targets),
                "unmatched_count": len(unmatched),
                "disease_target_count": len(disease_targets),
                "insect_target_count": len(insect_targets),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
