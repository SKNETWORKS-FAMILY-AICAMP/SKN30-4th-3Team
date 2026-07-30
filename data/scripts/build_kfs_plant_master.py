"""KFS 표준식물명 수집 결과 → 정규화 마스터 + plant_catalog 적재용 JSONL.

collect_kfs_standard_plants.py 가 만든 interim/kfs_standard_plants.jsonl 을 입력으로,
우리 데이터의 식물 표기(대표명 + DB표기 변형)를 학명 기준으로 묶어 정규화한다.

출력 2종:
  A) interim/kfs_normalization_master.jsonl
     - 학명당 1행. 표준국명·이명·우리표기·커버리지·해석방식(kpni/fallback/unmatched).
     - 이후 rag_chunks 재정규화, 크롭 스코핑 사전 등에 재사용하는 분석 마스터.
  B) processed/plant_master.kfs.jsonl
     - build_plant_master.py 와 동일 스키마 → 기존 load_plant_catalog.py 로 바로 적재 가능.
       python load_plant_catalog.py --input data/processed/plant_master.kfs.jsonl --dry-run

미매칭 폴백: KPNI 표준목록에 없는 재배/원예 통용명(예: 방울토마토, 꽈리고추)은,
매칭된 표준명을 부분 포함하면 그 종으로 연결한다(방울토마토⊃토마토 → Solanum lycopersicum).
연결 실패 시 학명 없이 이름만 보존(플랜트 카탈로그에는 term 확장용으로 적재).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import INTERIM_DIR, PROCESSED_DIR, read_jsonl, slugify, today, write_jsonl

DEFAULT_INPUT = INTERIM_DIR / "kfs_standard_plants.jsonl"
DEFAULT_MASTER = INTERIM_DIR / "kfs_normalization_master.jsonl"
DEFAULT_CATALOG = PROCESSED_DIR / "plant_master.kfs.jsonl"

SOURCE_ID = "kfs_kpni_standard"
SOURCE_URL = "https://www.data.go.kr/data/15142872/openapi.do"
LICENSE = "KOGL-4 (출처표시·상업적이용금지·변경금지)"
MIN_FALLBACK_LEN = 2  # 부분포함 폴백 시 앵커명 최소 길이(짧은 이름 오매칭 방지)

# 통용명이 표준명과 글자를 공유하지 않아 부분일치로 못 잡는 케이스의 수동 앵커.
# {우리표기: 표준국명}. 표준국명이 실제 매칭 클러스터일 때만 연결됨(아니면 무시).
MANUAL_ANCHOR: dict[str, str] = {
    "대두": "콩",
    "풋콩": "콩",
    "메주콩": "콩",
    "대파": "파",
    "쪽파": "파",
}

# 괄호 안에서 별칭이 아니라 '재배방식' 서술이면 별칭에서 제외.
_CULT_METHOD_RE = re.compile(r"재배|촉성|억제|반촉|균상|원목|노지|시설|고랭지|양액|수경")
_PAREN_RE = re.compile(r"[(（]([^()（）]*)")


def clean_name(name: str) -> tuple[str, list[str]]:
    """인라인 괄호(미완결 포함)를 분리: (기본명, 괄호에서 뽑은 별칭[]).

    예) '모란(목단)'→('모란',['목단']),  '갯기름 나물(식방풍'→('갯기름 나물',['식방풍']),
        '브로콜리(녹색꽃양배추,고랭지재배'→('브로콜리',['녹색꽃양배추'])  # 재배방식 제외
    """
    base = re.split(r"[(（]", name, maxsplit=1)[0].strip()
    aliases: list[str] = []
    for inside in _PAREN_RE.findall(name):
        for part in re.split(r"[,，/]", inside):
            part = part.strip().rstrip(")） ").strip()
            if part and not _CULT_METHOD_RE.search(part) and part not in aliases:
                aliases.append(part)
    return (base or name.strip()), aliases


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KFS 수집결과 → 정규화 마스터 + plant_catalog JSONL")
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--master-output", default=str(DEFAULT_MASTER))
    p.add_argument("--catalog-output", default=str(DEFAULT_CATALOG))
    return p.parse_args()


def _add_unique(seq: list[str], value: str | None) -> None:
    if value and value not in seq:
        seq.append(value)


def resolve_scientific(records: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str | None, str]]:
    """각 레코드를 학명으로 해석: (record, scientific|None, resolution)."""
    matched = [r for r in records if r.get("matched") and r.get("scientific_name")]
    # 매칭된 이름(표준명/질의명) → 학명. 긴 이름 우선(부분포함 폴백의 최장일치용).
    name_to_sci: dict[str, str] = {}
    for r in matched:
        name_to_sci.setdefault(r["standard_korean_name"], r["scientific_name"])
        name_to_sci.setdefault(r["query_name"], r["scientific_name"])
    anchor_names = sorted(name_to_sci, key=len, reverse=True)

    resolved: list[tuple[dict[str, Any], str | None, str]] = []
    for r in records:
        if r.get("matched") and r.get("scientific_name"):
            resolved.append((r, r["scientific_name"], "kpni"))
            continue
        query = r.get("_clean_name") or r["query_name"]
        # 1) 수동 앵커(표준명이 실제 매칭 클러스터일 때만)
        manual = MANUAL_ANCHOR.get(query)
        if manual and manual in name_to_sci:
            resolved.append((r, name_to_sci[manual], f"manual:{manual}"))
            continue
        # 2) 부분포함 폴백(최장일치)
        anchor = next(
            (nm for nm in anchor_names if len(nm) >= MIN_FALLBACK_LEN and nm != query and nm in query),
            None,
        )
        if anchor:
            resolved.append((r, name_to_sci[anchor], f"fallback:{anchor}"))
        else:
            resolved.append((r, None, "unmatched"))
    return resolved


def build_clusters(resolved: list[tuple[dict[str, Any], str | None, str]]):
    clusters: dict[str, dict[str, Any]] = {}
    standalone: list[dict[str, Any]] = []

    for r, sci, res in resolved:
        if sci is None:
            standalone.append(r)
            continue
        cluster = clusters.setdefault(
            sci,
            {
                "scientific_name": sci,
                "standard_korean_name": None,
                "aliases": [],
                "members": [],
                "resolutions": [],
                "coverage": {},
            },
        )
        # 표준국명: KPNI 매칭 멤버(추천명)의 표준국명 우선
        if res == "kpni" and cluster["standard_korean_name"] is None:
            cluster["standard_korean_name"] = r["standard_korean_name"]
        _add_unique(cluster["members"], r["query_name"])
        cluster["resolutions"].append(res)
        alias_sources = (
            [r["query_name"], r.get("standard_korean_name"), r.get("_clean_name")]
            + list(r.get("synonyms", []))
            + list(r.get("db_aliases", []))
            + list(r.get("_extra_aliases", []))
        )
        for value in alias_sources:
            _add_unique(cluster["aliases"], value)
        for key, flag in (r.get("coverage") or {}).items():
            cluster["coverage"][key] = cluster["coverage"].get(key, False) or bool(flag)

    for cluster in clusters.values():
        std = cluster["standard_korean_name"] or (cluster["members"][0] if cluster["members"] else "")
        cluster["standard_korean_name"] = std
        cluster["aliases"] = [a for a in cluster["aliases"] if a != std]
    return clusters, standalone


def master_row(cluster: dict[str, Any]) -> dict[str, Any]:
    kpni = sum(1 for x in cluster["resolutions"] if x == "kpni")
    fallback = sum(1 for x in cluster["resolutions"] if x.startswith(("fallback", "manual")))
    return {
        "scientific_name": cluster["scientific_name"],
        "standard_korean_name": cluster["standard_korean_name"],
        "aliases": cluster["aliases"],
        "our_data_names": cluster["members"],
        "coverage": cluster["coverage"],
        "member_count": len(cluster["members"]),
        "resolution": "kpni" if kpni else "fallback",
        "kpni_members": kpni,
        "fallback_members": fallback,
        "source": "국립수목원 국가표준식물목록",
        "license": LICENSE,
        "collected_at": today(),
    }


def _standalone_name_aliases(record: dict[str, Any]) -> tuple[str, list[str]]:
    name = record.get("_clean_name") or record["query_name"]
    aliases: list[str] = []
    for a in [record["query_name"]] + list(record.get("_extra_aliases", [])) + list(record.get("db_aliases", [])):
        if a and a != name and a not in aliases:
            aliases.append(a)
    return name, aliases


def merge_standalone(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """미해석(학명 없음) 레코드를 정리명 기준으로 병합(브로콜리 중복 등 제거)."""
    merged: dict[str, dict[str, Any]] = {}
    for r in records:
        name, aliases = _standalone_name_aliases(r)
        agg = merged.setdefault(name, {"name": name, "aliases": [], "our_data_names": [], "coverage": {}})
        for a in aliases:
            _add_unique(agg["aliases"], a)
        _add_unique(agg["our_data_names"], r["query_name"])
        for key, flag in (r.get("coverage") or {}).items():
            agg["coverage"][key] = agg["coverage"].get(key, False) or bool(flag)
    return merged


def standalone_master_row(agg: dict[str, Any]) -> dict[str, Any]:
    return {
        "scientific_name": "",
        "standard_korean_name": agg["name"],
        "aliases": agg["aliases"],
        "our_data_names": agg["our_data_names"],
        "coverage": agg["coverage"],
        "member_count": len(agg["our_data_names"]),
        "resolution": "unmatched",
        "kpni_members": 0,
        "fallback_members": 0,
        "source": "국립수목원 국가표준식물목록",
        "license": LICENSE,
        "collected_at": today(),
    }


def catalog_row(name_ko: str, scientific: str, aliases: list[str]) -> dict[str, Any]:
    """build_plant_master.py 와 동일 스키마. plant_id는 kfs- 네임스페이스로 기존 카탈로그와 분리."""
    seed = scientific or name_ko
    return {
        "plant_id": "kfs-" + slugify(seed, "plant"),
        "name_ko": name_ko,
        "name_scientific": scientific,
        "name_en": "",
        "aliases": aliases,
        "family": "",
        "category": ["kfs_standard"],
        "description": "",
        "source_id": SOURCE_ID,
        "source_url": SOURCE_URL,
        "license": LICENSE,
        "collected_at": today(),
        "safety_tags": [],
    }


def main() -> int:
    args = parse_args()
    records = read_jsonl(Path(args.input))
    if not records:
        print(f"입력이 비었습니다: {args.input} (수집이 끝났는지 확인하세요)")
        return 1

    for r in records:  # 인라인 괄호 정리 + 별칭 추출 결과를 레코드에 부착
        base, extra = clean_name(r["query_name"])
        r["_clean_name"] = base
        r["_extra_aliases"] = extra

    resolved = resolve_scientific(records)
    clusters, standalone = build_clusters(resolved)
    standalone_merged = merge_standalone(standalone)

    master_rows = [master_row(c) for c in clusters.values()] + [standalone_master_row(a) for a in standalone_merged.values()]
    master_rows.sort(key=lambda x: (x["resolution"] != "kpni", x["standard_korean_name"]))

    catalog_rows = [catalog_row(c["standard_korean_name"], c["scientific_name"], c["aliases"]) for c in clusters.values()]
    for agg in standalone_merged.values():
        catalog_rows.append(catalog_row(agg["name"], "", agg["aliases"]))

    n_master = write_jsonl(Path(args.master_output), master_rows)
    n_catalog = write_jsonl(Path(args.catalog_output), catalog_rows)

    total_names = len(records)
    fallback_names = sum(1 for _r, _s, res in resolved if res.startswith(("fallback", "manual")))
    print(f"입력 표기 {total_names}종 → 학명 클러스터 {len(clusters)}개 + 미해석 {len(standalone_merged)}개")
    print(f"  폴백/수동 연결: {fallback_names}종  (예: 방울토마토→토마토, 대두→콩)")
    print(f"정규화 마스터 : {n_master}행 → {args.master_output}")
    print(f"카탈로그 적재 : {n_catalog}행 → {args.catalog_output}")
    if standalone_merged:
        names = list(standalone_merged)
        print("미해석(학명 없음):", ", ".join(names[:30]) + (" ..." if len(names) > 30 else ""))
    print("\n다음: python load_plant_catalog.py --input", args.catalog_output, "--dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
