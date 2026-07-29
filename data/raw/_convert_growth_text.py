import json
from pathlib import Path

REPO_ROOT = Path(".").resolve()
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

growth_file = PROCESSED_DIR / "total_growth_care_187plants.jsonl"
integrated_growth_file = PROCESSED_DIR / "integrated_growth_care_187plants.jsonl"

def enrich_growth_text(row: dict) -> dict:
    crop_name = row.get("crop_or_plant", [""])[0]
    sci_name = row.get("scientific_name", "")
    
    t_data = row.get("trefle_data")
    ps_data = row.get("plantsolve_data")
    nk_data = row.get("nongsaro_korea_data")
    
    text_blocks = []
    text_blocks.append(f"[식물 생육 및 케어 종합 가이드 / Plant Care & Growth Guide]")
    text_blocks.append(f"대상 식물: {crop_name} (학명: {sci_name if sci_name else '정보 미비'})")
    
    # 1. PlantSolve 정밀 케어 데이터가 있는 경우
    if ps_data and ps_data.get("care"):
        care = ps_data["care"]
        text_blocks.append("\n[실내 관엽 케어 지침 (PlantSolve)]")
        if care.get("watering"): text_blocks.append(f"• 물주기 방법: {care['watering']}")
        if care.get("placement"): text_blocks.append(f"• 배치 및 일조량: {care['placement']}")
        if care.get("soil"): text_blocks.append(f"• 추천 토양: {care['soil']}")
        if care.get("pruning"): text_blocks.append(f"• 가지치기/잎 관리: {care['pruning']}")
        if care.get("toxicity"): text_blocks.append(f"• 반려동물 독성 여부: {care['toxicity']} ({care.get('toxicityNote', '')})")
        if care.get("difficulty"): text_blocks.append(f"• 키우기 난이도: {care['difficulty']}")
        
        if ps_data.get("lighting"):
            lt = ps_data["lighting"]
            if lt.get("description"): text_blocks.append(f"• 조명 세부사항: {lt['description']}")
            if lt.get("sunlightHours"): text_blocks.append(f"• 권장 권장 일조시간: {lt['sunlightHours']}")
            
        if ps_data.get("troubleshooting"):
            text_blocks.append("• 증상별 트러블슈팅:")
            for tb in ps_data["troubleshooting"]:
                text_blocks.append(f"  - 증상: {tb.get('symptom')} | 원인: {tb.get('diagnosis')} | 조치: {tb.get('treatment')}")

    # 2. 한국 농사로 텃밭 데이터가 있는 경우
    if nk_data:
        text_blocks.append("\n[국내 텃밭 재배 및 물주기 가이드 (농사로)]")
        text_blocks.append(f"• 물주기 주기: {nk_data.get('watering_interval')}")
        text_blocks.append(f"• 일조량 조건: {nk_data.get('sunlight')}")
        text_blocks.append(f"• 재배 환경: {nk_data.get('environment')}")

    # 3. Trefle 분류학적 메타데이터가 있는 경우
    if t_data and isinstance(t_data, dict):
        text_blocks.append("\n[글로벌 식물 분류 메타데이터 (Trefle)]")
        if t_data.get("common_name"): text_blocks.append(f"• 일반 영문명: {t_data['common_name']}")
        family_info = t_data.get("family")
        if family_info and isinstance(family_info, dict) and family_info.get("name"):
            text_blocks.append(f"• 식물 과(Family): {family_info['name']}")

    # 텍스트 합성
    row["text"] = "\n".join(text_blocks)
    row["title"] = f"{crop_name} 생육 및 케어(물주기, 일조량, 토양) 종합 가이드"
    return row

# 파일 업데이트
for g_path in [growth_file, integrated_growth_file]:
    if g_path.exists():
        updated_rows = []
        with open(g_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                r = json.loads(line)
                updated_rows.append(enrich_growth_text(r))
                
        with open(g_path, "w", encoding="utf-8") as f:
            for r in updated_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("생육 및 케어 데이터의 text 필드를 완전한 자연어 RAG 문장으로 100% 한글 텍스트화 완료!")
