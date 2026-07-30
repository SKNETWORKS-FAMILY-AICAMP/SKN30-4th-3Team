"""
7종 제외 및 최종 데이터 통합 파이프라인 스크립트

제외 대상 7종:
1. 생육 부족 2종: 187 (마리모), 194 (아가베 나나)
2. 병해충/농약 부족 5종: 018 (난), 126 (휘커스 아르테시마), 132 (유칼립투스 폴리안), 142 (파키포디움 라모숨), 193 (세네시오 은월)

생성 및 갱신 파일:
1. docs/master_plant_catalog_187.md (194 - 7 = 187종 최종 마스터 카탈로그)
2. data/processed/integrated_growth_care_187plants.jsonl (생육/케어 통합 192종/187종)
3. data/processed/integrated_disease_187plants.jsonl (병 데이터 글로벌+국내 융합)
4. data/processed/integrated_pest_187plants.jsonl (해충 데이터 글로벌+국내 융합)
5. data/processed/integrated_pesticide_187plants.jsonl (농약 데이터 국내 단독 187종)
"""
import json
from pathlib import Path

REPO_ROOT = Path(".").resolve()
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
DOCS_DIR = REPO_ROOT / "docs"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# 7종 제외 대상 ID
EXCLUDE_IDS = {"018", "126", "132", "142", "187", "193", "194"}

# 1. 194종 마스터 카탈로그 파싱 -> 187종으로 마스터 카탈로그 재작성
master_187 = []
md_file_194 = DOCS_DIR / "master_plant_catalog_194.md"

if md_file_194.exists():
    with open(md_file_194, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("|") and len(line.split("|")) >= 4:
                cols = [c.strip() for c in line.split("|")]
                num_str = cols[1].strip()
                if num_str.isdigit():
                    pid = num_str.zfill(3)
                    if pid not in EXCLUDE_IDS:
                        raw_name = cols[2].replace("**", "").strip()
                        base_name = raw_name.split("(")[0].strip()
                        sci_name = cols[3].replace("*", "").strip() if len(cols) > 3 and cols[3].startswith("*") else ""
                        master_187.append({
                            "id": pid,
                            "full_name": raw_name,
                            "base_name": base_name,
                            "scientific_name": sci_name
                        })

# 187종 마스터 카탈로그 MD 작성
md_lines = []
md_lines.append("# 🌱 187종 정제 완료 최종 마스터 식물 카탈로그 (Master Plant Catalog 187)\n")
md_lines.append("> **작성일**: 2026-07-29  ")
md_lines.append("> **설명**: 생육 데이터 및 병해충/농약 데이터 미보유 식물 7종(마리모, 아가베 나나, 난, 휘커스 아르테시마, 유칼립투스 폴리안, 파키포디움 라모숨, 세네시오 은월)을 정제 제외한 **최종 187종 서비스 마스터 카탈로그**입니다.\n")
md_lines.append("## 📋 187종 마스터 식물 목록\n")
md_lines.append("| 연번 | 식물 ID | 식물명 (Korean Name) | 학명 (Scientific Name) | 비고 |")
md_lines.append("| :-: | :-: | :--- | :--- | :--- |")

for idx, p in enumerate(master_187, 1):
    md_lines.append(f"| {idx} | {p['id']} | **{p['full_name']}** | *{p['scientific_name']}* | 정제 완결 |")

(DOCS_DIR / "master_plant_catalog_187.md").write_text("\n".join(md_lines), encoding="utf-8")
print(f"[1/5] docs/master_plant_catalog_187.md 생성 완료 (총 {len(master_187)}종)")

# 매칭용 이름 셋
master_names_187 = set()
for p in master_187:
    master_names_187.add(p["full_name"])
    master_names_187.add(p["base_name"])

# 헬퍼: 7종 제외 대상 식물이 켜져있는지 체크
def is_excluded_record(row: dict) -> bool:
    doc_id = row.get("doc_id", "")
    if ":" in doc_id:
        pid = doc_id.split(":")[-1].zfill(3)
        if pid in EXCLUDE_IDS:
            return True
    crops = row.get("crop_or_plant", [])
    for c in crops:
        c_clean = c.strip()
        for ex_p in master_187:
            # 187종에속하는지
            if c_clean == ex_p["full_name"] or c_clean == ex_p["base_name"]:
                return False
    # 만약 crop_or_plant 중 7종 식물명이 포함되어 있다면
    for c in crops:
        if any(ex_name in c for ex_name in ["마리모", "아가베 나나", "아가베나나", "동양난", "서양난", "아르테시마", "폴리안", "라모숨", "은월"]):
            return True
    return False

# 2. 생육/케어 jsonl 생성 (global_plant_growth_care_194plants.jsonl 에서 7종 제외)
growth_in = RAW_DIR / "global_plant_growth_care_194plants.jsonl"
growth_out = PROCESSED_DIR / "integrated_growth_care_187plants.jsonl"
growth_count = 0

if growth_in.exists():
    with open(growth_in, "r", encoding="utf-8") as fin, open(growth_out, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip(): continue
            row = json.loads(line)
            if not is_excluded_record(row):
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                growth_count += 1

print(f"[2/5] {growth_out.name} 생성 완료 (총 {growth_count}건 레코드)")

# 3. 병(Disease) jsonl 생성 (국내 disease_194plants.jsonl + 글로벌 perenual / eppo datasheets 융합, 7종 제외)
disease_out = PROCESSED_DIR / "integrated_disease_187plants.jsonl"
disease_count = 0

with open(disease_out, "w", encoding="utf-8") as fout:
    # 3-1 국내 disease_194plants.jsonl
    if (RAW_DIR / "disease_194plants.jsonl").exists():
        with open(RAW_DIR / "disease_194plants.jsonl", "r", encoding="utf-8") as fin:
            for line in fin:
                if not line.strip(): continue
                row = json.loads(line)
                if not is_excluded_record(row):
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                    disease_count += 1

    # 3-2 글로벌 perenual_disease_data_194plants.jsonl
    if (RAW_DIR / "perenual_disease_data_194plants.jsonl").exists():
        with open(RAW_DIR / "perenual_disease_data_194plants.jsonl", "r", encoding="utf-8") as fin:
            for line in fin:
                if not line.strip(): continue
                row = json.loads(line)
                if not is_excluded_record(row):
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                    disease_count += 1

    # 3-3 글로벌 eppo_datasheets_control_194plants.jsonl
    if (RAW_DIR / "eppo_datasheets_control_194plants.jsonl").exists():
        with open(RAW_DIR / "eppo_datasheets_control_194plants.jsonl", "r", encoding="utf-8") as fin:
            for line in fin:
                if not line.strip(): continue
                row = json.loads(line)
                if not is_excluded_record(row):
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                    disease_count += 1

print(f"[3/5] {disease_out.name} 생성 완료 (글로벌+국내 병 데이터 융합, 총 {disease_count}건 레코드)")

# 4. 해충(Pest) jsonl 생성 (국내 pest_194plants.jsonl + 글로벌 eppo_data_services_pests_194plants.jsonl 융합, 7종 제외)
pest_out = PROCESSED_DIR / "integrated_pest_187plants.jsonl"
pest_count = 0

with open(pest_out, "w", encoding="utf-8") as fout:
    # 4-1 국내 pest_194plants.jsonl
    if (RAW_DIR / "pest_194plants.jsonl").exists():
        with open(RAW_DIR / "pest_194plants.jsonl", "r", encoding="utf-8") as fin:
            for line in fin:
                if not line.strip(): continue
                row = json.loads(line)
                if not is_excluded_record(row):
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                    pest_count += 1

    # 4-2 글로벌 eppo_data_services_pests_194plants.jsonl
    if (RAW_DIR / "eppo_data_services_pests_194plants.jsonl").exists():
        with open(RAW_DIR / "eppo_data_services_pests_194plants.jsonl", "r", encoding="utf-8") as fin:
            for line in fin:
                if not line.strip(): continue
                row = json.loads(line)
                if not is_excluded_record(row):
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                    pest_count += 1

print(f"[4/5] {pest_out.name} 생성 완료 (글로벌+국내 해충 데이터 융합, 총 {pest_count}건 레코드)")

# 5. 농약(Pesticide) jsonl 생성 (순수 국내 PSIS 농약/방제약제 지침 단독, 7종 제외)
pesticide_out = PROCESSED_DIR / "integrated_pesticide_187plants.jsonl"
pesticide_count = 0

if (RAW_DIR / "pest_194plants.jsonl").exists():
    with open(RAW_DIR / "pest_194plants.jsonl", "r", encoding="utf-8") as fin, open(pesticide_out, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip(): continue
            row = json.loads(line)
            if not is_excluded_record(row):
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                pesticide_count += 1

print(f"[5/5] {pesticide_out.name} 생성 완료 (순수 국내 PSIS 농약 데이터 단독, 총 {pesticide_count}건 레코드)")
