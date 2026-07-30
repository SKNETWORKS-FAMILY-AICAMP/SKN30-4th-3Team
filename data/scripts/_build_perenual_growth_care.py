import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

from common import load_env

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

# 1. Load 187 Master Plants & plant_data_coverage.md
CATALOG_187_PATH = RAW_DIR / "master_plant_catalog_187.md"
COVERAGE_MD_PATH = RAW_DIR / "plant_data_coverage.md"

target_plants = []
target_names_set = set()

# Load 187 Master Plants
if CATALOG_187_PATH.exists():
    with open(CATALOG_187_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("|") and len(line.split("|")) >= 4:
                cols = [c.strip() for c in line.split("|")]
                num_str = cols[1].strip()
                if num_str.isdigit():
                    name_ko = cols[2].replace("**", "").strip()
                    base_name = name_ko.split("(")[0].strip()
                    sci_name = cols[3].replace("*", "").strip() if len(cols) >= 4 else ""
                    target_plants.append({
                        "id": num_str.zfill(3),
                        "name_ko": base_name,
                        "raw_name": name_ko,
                        "scientific": sci_name
                    })
                    target_names_set.add(base_name.lower())

# Load plant_data_coverage.md
if COVERAGE_MD_PATH.exists():
    with open(COVERAGE_MD_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("|") and len(line.split("|")) >= 6:
                cols = [c.strip() for c in line.split("|")]
                name = cols[1].strip()
                if name and name != "대표 식물명 (실제 DB 표기)" and not name.startswith("---"):
                    base_name = name.split("(")[0].strip()
                    if base_name.lower() not in target_names_set:
                        target_plants.append({
                            "id": None,
                            "name_ko": base_name,
                            "raw_name": name,
                            "scientific": ""
                        })
                        target_names_set.add(base_name.lower())

print(f"조사 대상 전체 식물 목록: {len(target_plants)}종")

# 2. Perenual Care Guide API Paging Fetch
PERENUAL_KEY = load_env().get("PERENUAL_API_KEY", "").strip()
if not PERENUAL_KEY:
    raise RuntimeError("PERENUAL_API_KEY must be set in .env or the process environment.")

def http_get_json(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if "429" in str(e):
                time.sleep(2 * (attempt + 1))
            else:
                break
    return None

print("Perenual Care Guide List 수집 시작 (최대 100페이지)...")
all_care_guides = []

# Page 1 to 50 for deep coverage
for page in range(1, 51):
    url = f"https://perenual.com/api/species-care-guide-list?key={PERENUAL_KEY}&page={page}"
    data = http_get_json(url)
    if not data or not data.get("data"):
        break
    items = data["data"]
    all_care_guides.extend(items)
    print(f"[{page}/50] 수집 완료 ({len(items)}개 항목 추가, 누적: {len(all_care_guides)}개)")
    time.sleep(0.3)

print(f"\nPerenual Care Guide 전체 수집 항목: 총 {len(all_care_guides)}개")

# 3. Filter valid care guides (No null sections, No upgrade messages)
def is_valid_care_guide(item: dict) -> bool:
    if not isinstance(item, dict): return False
    sections = item.get("section")
    if not sections or not isinstance(sections, list) or len(sections) == 0:
        return False
    
    valid_section_count = 0
    for sec in sections:
        desc = sec.get("description")
        stype = sec.get("type")
        if desc and isinstance(desc, str) and len(desc.strip()) > 10:
            if "upgrade plans to premium" not in desc.lower() and desc != "null":
                valid_section_count += 1
                
    return valid_section_count > 0

valid_guides = [g for g in all_care_guides if is_valid_care_guide(g)]
print(f"유효한 비-null 케어 섹션을 보유한 Perenual 식물 가이드: {len(valid_guides)}개")

# 4. Match Perenual Care Guides with Target Plants
matched_results = []
matched_species_ids = set()

for plant in target_plants:
    p_ko = plant["name_ko"].lower().strip()
    p_sci = plant["scientific"].lower().strip()
    
    matched_guide = None
    
    # 1) 학명 매칭
    if p_sci and len(p_sci) > 3:
        p_genus = p_sci.split()[0]
        for g in valid_guides:
            g_sci_list = g.get("scientific_name") or []
            if isinstance(g_sci_list, str): g_sci_list = [g_sci_list]
            g_sci_str = " ".join(g_sci_list).lower()
            if p_sci in g_sci_str or (len(p_genus) >= 4 and p_genus in g_sci_str):
                matched_guide = g
                break
                
    # 2) common_name 매칭
    if not matched_guide and p_ko:
        for g in valid_guides:
            if g.get("id") in matched_species_ids: continue
            g_cname = (g.get("common_name") or "").lower()
            if p_ko in g_cname or g_cname in p_ko:
                matched_guide = g
                break
                
    if matched_guide:
        gid = matched_guide.get("id")
        matched_species_ids.add(gid)
        
        # Section 정리
        clean_sections = {}
        for sec in matched_guide.get("section", []):
            stype = sec.get("type")
            sdesc = sec.get("description", "").strip()
            if stype and sdesc and "upgrade plans to premium" not in sdesc.lower():
                clean_sections[stype] = sdesc
                
        matched_results.append({
            "master_id": plant["id"],
            "crop_or_plant": [plant["name_ko"]],
            "common_name": matched_guide.get("common_name"),
            "scientific_name": matched_guide.get("scientific_name"),
            "perenual_species_id": matched_guide.get("species_id"),
            "source_id": "perenual_plant_api",
            "publisher": "Perenual Care Guide API",
            "category": "growth_care",
            "care_sections": clean_sections
        })

print(f"조사 대상 식물 중 Perenual 생육 케어 데이터 매칭 성공: {len(matched_results)}종")

# 매칭되지 않은 나머지 Perenual 유효 케어 가이드도 추가 포함
additional_count = 0
for g in valid_guides:
    gid = g.get("id")
    if gid not in matched_species_ids:
        additional_count += 1
        cname = g.get("common_name") or f"Perenual Plant #{g.get('species_id')}"
        clean_sections = {}
        for sec in g.get("section", []):
            stype = sec.get("type")
            sdesc = sec.get("description", "").strip()
            if stype and sdesc and "upgrade plans to premium" not in sdesc.lower():
                clean_sections[stype] = sdesc
                
        matched_results.append({
            "master_id": None,
            "crop_or_plant": [cname],
            "common_name": cname,
            "scientific_name": g.get("scientific_name"),
            "perenual_species_id": g.get("species_id"),
            "source_id": "perenual_plant_api",
            "publisher": "Perenual Care Guide API",
            "category": "growth_care",
            "care_sections": clean_sections
        })

print(f"추가 수집된 유효 Perenual 케어 가이드 식물: {additional_count}종")
print(f"최종 생성 대상 perenual_growth_care.jsonl 총 식물 수: {len(matched_results)}종")

# perenual_growth_care.jsonl 저장
out_path = RAW_DIR / "perenual_growth_care.jsonl"
with open(out_path, "w", encoding="utf-8") as f:
    for item in matched_results:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")

print(f"\n[성공] {out_path} 저장 완료! 총 {len(matched_results)}개 식물 생육 데이터 저장됨.")
