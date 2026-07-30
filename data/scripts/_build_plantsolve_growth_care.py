import json
import time
import urllib.request
from pathlib import Path

# 경로 설정
RAW_DIR = Path("data/raw")
INPUT_FILE = RAW_DIR / "previous_total_growth_care_187plants.jsonl"
OUT_FILE = RAW_DIR / "plantsolve_growth_care.jsonl"

# 187종 식물 정보 로드
master_plants = []
if INPUT_FILE.exists():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            row = json.loads(line)
            crop = row.get("crop_or_plant", [""])[0]
            sci = row.get("scientific_name", "")
            doc_id = row.get("doc_id", "")
            master_plants.append({
                "doc_id": doc_id,
                "name_ko": crop,
                "scientific": sci
            })

print(f"기본 식물 리스트 로드 완료: 총 {len(master_plants)}종")

# PlantSolve Index 다운로드
INDEX_URL = "https://www.plantsolve.com/api/v1/plants/index.json"
req = urllib.request.Request(INDEX_URL, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=15) as resp:
    ps_index = json.loads(resp.read().decode("utf-8"))

print(f"PlantSolve DB 전체 등록 식물: {len(ps_index)}개")

# 130개 식물 상세 데이터 다운로드
ps_details = {}
for i, item in enumerate(ps_index, 1):
    slug = item.get("slug")
    if not slug: continue
    detail_url = f"https://www.plantsolve.com/api/v1/plants/{slug}.json"
    try:
        dreq = urllib.request.Request(detail_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(dreq, timeout=10) as dresp:
            data = json.loads(dresp.read().decode("utf-8"))
            ps_details[slug] = data
    except Exception as e:
        print(f"[{i}/{len(ps_index)}] {slug} 다운로드 실패: {e}")
    time.sleep(0.01)

print(f"PlantSolve 상세 데이터 완료: 총 {len(ps_details)}개 식물")

# 하나 이상 non-null 요소가 존재하는지 검증 (all-null 제외)
def has_valid_care_data(data: dict) -> bool:
    if not isinstance(data, dict): return False
    care = data.get("care") or {}
    growth = data.get("growthCharacteristics") or {}
    lighting = data.get("lighting") or {}
    params = data.get("parameters") or {}
    trouble = data.get("troubleshooting") or []

    for d in [care, growth, lighting, params]:
        if isinstance(d, dict):
            for k, v in d.items():
                if v is not None and v != "" and v != {}:
                    return True
    return len(trouble) > 0

valid_ps_plants = {slug: data for slug, data in ps_details.items() if has_valid_care_data(data)}
print(f"유효한 생육 데이터(최소 1개 이상 non-null) 보유 식물: {len(valid_ps_plants)}개")

results = []
matched_slugs = set()

# 마스터 식물 매핑
for master in master_plants:
    m_sci = master["scientific"].lower().strip()
    m_ko = master["name_ko"].lower().strip()

    matched_data = None
    matched_slug = None

    if m_sci and len(m_sci) > 3:
        m_genus = m_sci.split()[0]
        for slug, data in valid_ps_plants.items():
            p_sci = (data.get("scientificName") or "").lower().strip()
            if m_sci == p_sci or (len(m_genus) >= 4 and m_genus in p_sci):
                matched_data = data
                matched_slug = slug
                break

    if not matched_data and m_ko:
        for slug, data in valid_ps_plants.items():
            if slug in matched_slugs: continue
            c_name = (data.get("commonName") or "").lower().strip()
            if m_ko in c_name or slug.replace("-", " ") in m_ko:
                matched_data = data
                matched_slug = slug
                break

    if matched_data and matched_slug:
        matched_slugs.add(matched_slug)
        results.append({
            "doc_id": master["doc_id"],
            "crop_or_plant": [master["name_ko"]],
            "scientific_name": master["scientific"] or matched_data.get("scientificName", ""),
            "common_name": matched_data.get("commonName", ""),
            "slug": matched_slug,
            "source_id": "plantsolve_api",
            "publisher": "PlantSolve Global",
            "category": "growth_care",
            "care": matched_data.get("care"),
            "lighting": matched_data.get("lighting"),
            "parameters": matched_data.get("parameters"),
            "growthCharacteristics": matched_data.get("growthCharacteristics"),
            "troubleshooting": matched_data.get("troubleshooting"),
            "summary": matched_data.get("summary")
        })

print(f"마스터 187종 중 매칭된 식물: {len(results)}종")

# 매칭되지 않은나머지 PlantSolve 생육 데이터 식물들도 전부 추가 (누락 없음)
unmatched_count = 0
for slug, data in valid_ps_plants.items():
    if slug not in matched_slugs:
        unmatched_count += 1
        c_name = data.get("commonName") or slug.replace("-", " ").title()
        results.append({
            "doc_id": f"plantsolve:{slug}",
            "crop_or_plant": [c_name],
            "scientific_name": data.get("scientificName", ""),
            "common_name": c_name,
            "slug": slug,
            "source_id": "plantsolve_api",
            "publisher": "PlantSolve Global",
            "category": "growth_care",
            "care": data.get("care"),
            "lighting": data.get("lighting"),
            "parameters": data.get("parameters"),
            "growthCharacteristics": data.get("growthCharacteristics"),
            "troubleshooting": data.get("troubleshooting"),
            "summary": data.get("summary")
        })

print(f"PlantSolve 추가 수집 식물 (유효 생육데이터 보유): {unmatched_count}종")
print(f"최종 생성된 plantsolve_growth_care.jsonl 총 식물 수: {len(results)}종")

with open(OUT_FILE, "w", encoding="utf-8") as f:
    for item in results:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")

print(f"성공적으로 파일 저장 완료: {OUT_FILE}")
