"""
Supabase의 plant_catalog, rag_chunks, PSIS_pesticide_chunks를 조회하여
식물별 생육/병/해충/농약 보유 현황을 의미 단위로 통합한 Markdown으로 저장합니다.

실행:
    python data/notebooks/update_supabase_plant_data_coverage.py

환경변수:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY 또는 SUPABASE_KEY

산출물:
    data/interim/insect/list/plant_data_coverage.md

통합 기준:
    - 기존 coverage Markdown에서 검증했던 DB 표기
    - KNA 수집 과정에서 검증한 검색명/대표명 매핑
    - plant_catalog에서 학명이 완전히 동일한 이름
    - 서비스상 안전하게 동일 대상으로 볼 수 있는 제한적 수동 매핑

통합 기준은 식물학적 분류보다 사용자가 실제로 키우고 관리하는 대상을 우선합니다.
"""

from __future__ import annotations

import json
import os
import re
from ast import literal_eval
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data/interim/insect/list/plant_data_coverage.md"
KNA_MAPPING_DIR = ROOT / "data/interim/coverage_plant_grow_kna_api/normalized"

GROWTH_CATEGORIES = {
    "crop_care",
    "indoor_care",
    "ornamental_care",
    "crop_growth_stage",
    "plant_reference",
    "plant_growth",
}

# 자동 추론하지 않고 의미가 확실한 범위만 연결합니다.
SAFE_ALIASES = {
    "감귤나무": "감귤",
    "단감주나무": "단감",
    "구기자나무": "구기자",
    "깻잎나물": "들깨",
    "논벼": "벼",
    "깻잎": "들깨",
    "들깨(잎)": "들깨",
    "들깨(종실)": "들깨",
    "두릅나무": "두릅",
    "마늘(잎마늘)": "마늘",
    "매실나무": "매실",
    "무화과나무": "무화과",
    "물앵두나무": "앵두",
    "복숭아나무": "복숭아",
    "브로콜리(녹색꽃양배추)": "브로콜리",
    "녹색꽃양배추": "브로콜리",
    "서양자두나무": "자두",
    "양앵두": "체리",
    "양앵두(체리)": "체리",
    "올리브나무": "올리브",
    "인도고무나무": "고무나무",
    "잎마늘": "마늘",
    "잎치커리": "치커리",
    "참다래 농작업일정": "참다래",
    "치커리(쌈용, 잎치커리)": "치커리",
    "칼라데아마코야나": "칼라데아",
    "행운목": "드라세나",
    "오렌지쟈스민": "오렌지재스민",
    "피망": "파프리카",
    "단고추": "파프리카",
}

CULTIVATION_TERMS = {
    "고랭지재배",
    "균상재배",
    "노지재배",
    "무가온",
    "무가온 시설재배",
    "병재배",
    "보통재배",
    "사계성여름재배",
    "시설재배",
    "억제재배",
    "원목재배",
    "촉성재배",
    "평야지재배",
    "표준가온",
}

# 식물명이 아니라 수집/분류 과정에서 생긴 값입니다.
EXCLUDED_NAMES = {
    "",
    "난",
    "과수",
    "과채류",
    "관엽류",
    "기타",
    "기타관엽식물류",
    "버섯류",
    "식량작물",
    "엽채류",
    "종실",
    "채소",
    "화훼류",
} | CULTIVATION_TERMS


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, name: str) -> None:
        if name:
            self.parent.setdefault(name, name)

    def find(self, name: str) -> str:
        self.add(name)
        while self.parent[name] != name:
            self.parent[name] = self.parent[self.parent[name]]
            name = self.parent[name]
        return name

    def union(self, left: str, right: str) -> None:
        if not left or not right:
            return
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


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


def supabase_get(table: str, select: str, page_size: int = 1000) -> list[dict]:
    base_url = os.environ["SUPABASE_URL"].rstrip("/")
    api_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
    rows: list[dict] = []
    offset = 0

    while True:
        query = urlencode({"select": select, "limit": page_size, "offset": offset})
        request = Request(
            f"{base_url}/rest/v1/{table}?{query}",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=120) as response:
            page = json.loads(response.read().decode("utf-8"))
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def normalize_name(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_source_name(value: object) -> str:
    name = normalize_name(value)
    if name in EXCLUDED_NAMES:
        return ""
    for term in sorted(CULTIVATION_TERMS, key=len, reverse=True):
        parenthesized = f"({term})"
        if name.endswith(parenthesized):
            return normalize_name(name[: -len(parenthesized)])
        suffix = f" {term}"
        if name.endswith(suffix):
            return normalize_name(name[: -len(suffix)])
    return name


def extract_names(value: object) -> list[str]:
    """metadata의 문자열/배열 및 문자열로 저장된 Python 배열을 이름 목록으로 바꾼다."""
    if isinstance(value, (list, tuple, set)):
        return [name for item in value for name in extract_names(item)]
    if not isinstance(value, str):
        name = clean_source_name(value)
        return [name] if name else []

    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = literal_eval(value)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, (list, tuple, set)):
            return [name for item in parsed for name in extract_names(item)]
    name = clean_source_name(value)
    return [name] if name else []


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
    return [part for part in parts if part]


def load_previous_md_aliases(uf: UnionFind) -> set[str]:
    representatives: set[str] = set()
    if not OUTPUT.exists():
        return representatives

    for line in OUTPUT.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or cells[0] in {"식물명", "대표 식물명", "대표 식물명 (통합 표기)"}:
            continue

        label = cells[0]
        match = re.match(r"^(.*?)\s*\(DB 표기:\s*(.*)\)$", label)
        representative = normalize_name(match.group(1) if match else label)
        if not representative:
            continue
        representatives.add(representative)
        uf.add(representative)

        if match:
            for alias in split_outside_parentheses(match.group(2)):
                uf.union(representative, normalize_name(alias))
        elif len(cells) >= 2 and cells[1] not in {"O", "X", "-"}:
            for alias in split_outside_parentheses(cells[1]):
                uf.union(representative, normalize_name(alias))
    return representatives


def load_kna_aliases(uf: UnionFind) -> set[str]:
    preferred: set[str] = set()
    if not KNA_MAPPING_DIR.exists():
        return preferred

    for path in KNA_MAPPING_DIR.glob("*.name_mapping.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        rows = payload if isinstance(payload, list) else payload.get("mappings", [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            catalog_name = normalize_name(row.get("catalog_name"))
            if not catalog_name:
                continue
            preferred.add(catalog_name)
            for key in (
                "query_name",
                "searched_term",
                "matched_name",
                "api_name",
                "api_result_name",
            ):
                alias = normalize_name(row.get(key))
                if alias:
                    uf.union(catalog_name, alias)
    return preferred


def main() -> None:
    load_dotenv()
    uf = UnionFind()
    old_representatives = load_previous_md_aliases(uf)
    preferred_catalog_names = load_kna_aliases(uf)

    catalog_rows = supabase_get("plant_catalog", "name,species")
    rag_rows = supabase_get("rag_chunks", "metadata")
    # PostgreSQL에서 따옴표 없이 생성한 식별자는 실제로 소문자로 저장됩니다.
    pesticide_rows = supabase_get("psis_pesticide_chunks", "crop_name")

    catalog_names: set[str] = set()
    species_to_names: dict[str, set[str]] = defaultdict(set)
    for row in catalog_rows:
        name = normalize_name(row.get("name"))
        species = normalize_name(row.get("species"))
        if not name:
            continue
        catalog_names.add(name)
        uf.add(name)
        if species:
            species_to_names[species].add(name)

    for names in species_to_names.values():
        ordered = sorted(names)
        for name in ordered[1:]:
            uf.union(ordered[0], name)

    for alias, representative in SAFE_ALIASES.items():
        uf.union(representative, alias)

    counts: dict[str, Counter] = defaultdict(Counter)
    source_names: set[str] = set(catalog_names)

    for row in rag_rows:
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        names = extract_names(
            metadata.get("crop_or_plant")
            or metadata.get("cropOrPlant")
            or metadata.get("plant_name")
        )
        category = normalize_name(metadata.get("category")).lower()
        if not names:
            continue
        for name in names:
            source_names.add(name)
            uf.add(name)
            if category in GROWTH_CATEGORIES:
                counts[name]["growth"] += 1
            elif category == "disease":
                counts[name]["disease"] += 1
            elif category == "insect":
                counts[name]["insect"] += 1

    for row in pesticide_rows:
        name = normalize_name(row.get("crop_name"))
        if not name:
            continue
        source_names.add(name)
        uf.add(name)
        counts[name]["pesticide"] += 1

    # 띄어쓰기만 다른 복합 식물명도 같은 사용자 검색 의도로 취급합니다.
    compact_to_names: dict[str, list[str]] = defaultdict(list)
    for name in source_names:
        compact_to_names[re.sub(r"\s+", "", name)].append(name)
    for names in compact_to_names.values():
        for name in names[1:]:
            uf.union(names[0], name)

    # 최종 식물 목록은 plant_catalog에 존재하는 그룹으로만 제한합니다.
    # rag_chunks/PSIS에만 있는 표기는 새 식물 행을 만들지 않고, catalog 대표명의
    # coverage를 판정하기 위한 DB 표기로만 사용합니다.
    catalog_roots = {uf.find(name) for name in catalog_names}
    groups: dict[str, set[str]] = defaultdict(set)
    for name in source_names:
        root = uf.find(name)
        if name not in EXCLUDED_NAMES and root in catalog_roots:
            groups[root].add(name)

    rows: list[tuple[str, list[str], Counter]] = []
    for members in groups.values():
        totals = Counter()
        for member in members:
            totals.update(counts[member])

        candidates = members & catalog_names
        preferred = candidates & preferred_catalog_names
        old_candidates = members & old_representatives
        if preferred:
            representative = max(preferred, key=lambda n: (sum(counts[n].values()), -len(n), n))
        elif candidates:
            representative = max(candidates, key=lambda n: (sum(counts[n].values()), -len(n), n))
        elif old_candidates:
            representative = max(old_candidates, key=lambda n: (sum(counts[n].values()), -len(n), n))
        else:
            representative = max(members, key=lambda n: (sum(counts[n].values()), -len(n), n))

        aliases = sorted(members - {representative})
        rows.append((representative, aliases, totals))

    rows.sort(key=lambda item: item[0])
    summary = {
        key: sum(1 for _, _, total in rows if total[key] > 0)
        for key in ("growth", "disease", "insect", "pesticide")
    }

    lines = [
        "# 식물 데이터 보유 현황",
        "",
        (
            f"총 {len(rows)}종 | 생육 {summary['growth']} | 병 {summary['disease']} | "
            f"해충 {summary['insect']} | 농약 {summary['pesticide']}"
        ),
        "",
        "- Supabase의 현재 데이터를 다시 조회한 결과입니다.",
        "- 최종 식물 수는 plant_catalog의 대표 식물 그룹만 집계하고, 다른 테이블의 표기는 O/X 판정용 별칭으로만 사용합니다.",
        "- 대표명·검증된 별칭·동일 학명을 의미 단위로 묶었으며, 그룹 내 어느 표기에라도 데이터가 있으면 O입니다.",
        "- 식물학적 표기보다 사용자 재배 의도를 우선하여 열매·잎·나무·작물 표기가 달라도 실제 재배 대상이 같으면 통합합니다.",
        "- 예: `들깨`·`깻잎`·`깻잎나물`·`들깨(잎)`, `감귤`·`감귤나무`를 각각 하나로 집계합니다.",
        "",
        "| 대표 식물명 | 함께 검색되는 DB 표기 | 생육 | 병 | 해충 | 농약 |",
        "|---|---|:---:|:---:|:---:|:---:|",
    ]
    for representative, aliases, total in rows:
        alias_text = ", ".join(aliases) if aliases else "-"
        marks = ["O" if total[key] else "X" for key in ("growth", "disease", "insect", "pesticide")]
        lines.append(
            f"| {representative} | {alias_text} | {marks[0]} | {marks[1]} | {marks[2]} | {marks[3]} |"
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    print(OUTPUT)
    merged_catalog_groups = [
        sorted(members & catalog_names)
        for members in groups.values()
        if len(members & catalog_names) > 1
    ]
    print(
        json.dumps(
            {
                "catalog_rows": len(catalog_rows),
                "catalog_unique_names": len(catalog_names),
                "semantic_catalog_groups": len(rows),
                "merged_catalog_groups": merged_catalog_groups,
                "total": len(rows),
                **summary,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
