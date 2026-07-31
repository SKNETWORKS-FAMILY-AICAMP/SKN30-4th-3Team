# 🔬 EPPO Global Database 병해충 및 방제 데이터 수집 명세서 (EPPO Diseases & Pests)

## 📌 개요
- **출처명**: EPPO Global Database / EPPO Datasheets (https://gd.eppo.int)
- **수집 대상**: 373종 전체 통합 식물 종 (187종 서비스 마스터 + plant_data_coverage.md 271종 비중복 통합 목록)
- **데이터 카테고리**: `pest_control` (병해충 발생, 증상, 지리적 분포, 검역 및 화학/생물적 방제)
- **API Key 사용**: **EPPO_API_TOKEN** (`bf79d48e40ac436c8058e67f8035a783` - `.env`에 설정 완료)

---

## 📊 데이터 필드 명세

| 필드명 | 타입 | 설명 | 예시 |
|---|---|---|---|
| `doc_id` | string | EPPO 식별 ID | `eppo:001_apple` |
| `crop_or_plant` | list[string] | 대상 작물/식물명 (한글+영문) | `["사과", "Apple"]` |
| `source_id` | string | 데이터 출처 ID | `eppo_global_database` |
| `publisher` | string | 데이터 발행 기관 | `EPPO (European and Mediterranean Plant Protection Organization)` |
| `category` | string | 정보 분류 | `pest_control` |
| `eppo_code` | string | EPPO 고유 식물/병원체 코드 | `EPPO-APPL` |
| `disease_or_pest_name` | string | 병해충/병원체 공식 명칭 | `사과 (Apple) Pathogen & Pest Protection Record` |
| `pest_type` | string | 병해충 분류 타입 | `Fungi / Bacteria / Virus / Insect` |
| `control_measures` | list[string] | 생물적/화학적/경작적 방제 솔루션 | `["Use certified pathogen-free seeds", "Apply preventive fungicides"]` |
| `quarantine_status` | string | 국제 검역 및 위험 등급 | `Regulated non-quarantine pest (RNQP)` |
| `safety_tags` | list[string] | 안전 주의 태그 | `["expert_check_required", "pesticide_caution"]` |

---

## 🛠️ 수집 및 추출 파이프라인
1. **실행 스크립트**: [collect_eppo_diseases_pests.py](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/collect_eppo_diseases_pests.py)
2. **산출 데이터**: [eppo_diseases_pests.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/eppo_diseases_pests.jsonl) **(총 373종 전수 데이터 수집 완료)**
