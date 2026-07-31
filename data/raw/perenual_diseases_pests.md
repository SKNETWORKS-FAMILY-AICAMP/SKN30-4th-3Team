# 🌺 Perenual Plant API 병해충 및 방제 데이터 수집 명세서 (Perenual Diseases & Pests)

## 📌 개요
- **출처명**: Perenual Global Plant API (https://perenual.com)
- **수집 대상**: 373종 전체 통합 식물 종 (187종 서비스 마스터 + plant_data_coverage.md 271종 비중복 통합 목록)
- **데이터 카테고리**: `pest_control` (병해충 증상, 무농약 천연 방제법, 약제 농약 안전 사용법)
- **API Key 사용**: **PERENUAL_API_KEY** (`sk-Bfsd6a66d1ca011e819013` - `.env`에 설정 완료)

---

## 📊 데이터 필드 명세

| 필드명 | 타입 | 설명 | 예시 |
|---|---|---|---|
| `doc_id` | string | Perenual 식별 ID | `perenual:001_monstera` |
| `crop_or_plant` | list[string] | 대상 식물/작물명 (한글+영문) | `["몬스테라", "Monstera"]` |
| `source_id` | string | 출처 식별자 | `perenual_plant_api` |
| `title` | string | 문서 제목 | `몬스테라 (Monstera) - Perenual Global Protection Guide` |
| `category` | string | 정보 카테고리 | `pest_control` |
| `disease_or_pest_name` | string | 주요 병해충 명칭 | `몬스테라 (Monstera) Common Disease & Pest Solutions` |
| `symptom_keywords` | list[string] | 증상 키워드 | `["leaf_spots", "powdery_mildew", "spider_mites", "neem_oil"]` |
| `treatment_solutions` | list[dict] | 재배적/천연/화학 방제 가이드 단계 | `[{"subtitle": "Organic", "description": "Spray Neem Oil"}]` |
| `safety_tags` | list[string] | 실내 안전 처방 태그 | `["indoor_safe_pesticide", "natural_remedies"]` |

---

## 🛠️ 수집 및 추출 파이프라인
1. **실행 스크립트**: [collect_perenual_diseases_pests.py](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/collect_perenual_diseases_pests.py)
2. **산출 데이터**: [perenual_diseases_pests.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/perenual_diseases_pests.jsonl) **(총 373종 전수 데이터 수집 완료)**
