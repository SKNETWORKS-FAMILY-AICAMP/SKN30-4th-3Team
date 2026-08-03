# 📊 3대 해외 수집 데이터 분리 전처리 파이프라인 평가 및 Supabase DB 반영 보고서

## 1. 개요 (Overview)
본 보고서는 해외 3개 데이터 출처(PlantSolve, EPPO Global Database, Perenual Plant API)의 원시 데이터 6개 파일(.md, .jsonl)을 대상으로 데이터 도메인 영역별(① 병과 해충 통합, ② 농약/방제자재 독립, ③ 생육/케어 데이터 독립) 분리 파이프라인을 구축하여 파싱, 정규화, 청킹, 임베딩 전 과정을 수행하고, 최종 Supabase Vector DB 반영 여부 및 품질 검증 결과를 종합 정리한 문서입니다.

---

## 📁 2. 원시 데이터 및 도메인별 전처리 파이프라인 체계

### 2.1 6대 원시 수집 파일 (Raw Data Files)
| 출처 (Source) | 원시 데이터 파일 (.jsonl) | 명세서 문서 (.md) | 수집 도메인 분류 | 1차 추출 건수 |
|---|---|---|---|:-:|
| PlantSolve | [plantsolve_growth_care.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/plantsolve_growth_care.jsonl) | [plantsolve_growth_care.md](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/plantsolve_growth_care.md) | 생육/케어 (Growth Care) | 98건 |
| EPPO | [eppo_diseases_pests.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/eppo_diseases_pests.jsonl) | [eppo_diseases_pests.md](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/eppo_diseases_pests.md) | 병해충 & 방제자재(PPP) | 373건 |
| Perenual | [perenual_diseases_pests.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/perenual_diseases_pests.jsonl) | [perenual_diseases_pests.md](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/perenual_diseases_pests.md) | 병해충 & 농약 솔루션 | 373건 |

---

## 🛠️ 3. 도메인별 분리 파이프라인 산출 데이터셋 (Category-Split Datasets)

데이터의 성격에 따라 병/해충 통합 파이프라인, 농약/방제약제 독립 파이프라인, 생육/케어 독립 파이프라인 3가지로 완전 분리하여 정규화·청킹·임베딩 파일 세트를 생성하였습니다.

### 3.1 🟢 [카테고리 1] 생육 & 케어 데이터 (Growth Care Pipeline) - PlantSolve
- 정규화 산출물: [integrated_growth_care_normalized.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_growth_care_normalized.jsonl)
- 청크 산출물: [integrated_growth_care_chunks.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_growth_care_chunks.jsonl) (98건)
- 임베딩 산출물: [integrated_growth_care_embedded.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_growth_care_embedded.jsonl) (98건)

### 3.2 🔴 [카테고리 2] 병과 해충 통합 데이터 (Disease & Pest Pipeline) - EPPO + Perenual
- 정규화 산출물: [integrated_disease_pest_normalized.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_disease_pest_normalized.jsonl)
- 청크 산출물: [integrated_disease_pest_chunks.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_disease_pest_chunks.jsonl) (746건)
- 임베딩 산출물: [integrated_disease_pest_embedded.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_disease_pest_embedded.jsonl) (746건)

### 3.3 🔵 [카테고리 3] 농약 및 방제자재 독립 데이터 (Pesticide Pipeline) - EPPO PPP + Perenual Remedies
- 정규화 산출물: [integrated_pesticide_normalized.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_pesticide_normalized.jsonl)
- 청크 산출물: [integrated_pesticide_chunks.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_pesticide_chunks.jsonl) (746건)
- 임베딩 산출물: [integrated_pesticide_embedded.jsonl](file:///c:/Users/playdata2/OneDrive/Desktop/프로젝트/4차 단위 프로젝트/SKN30-4th-3Team/data/raw/integrated_pesticide_embedded.jsonl) (746건)

---

## 🗄️ 4. Supabase Vector DB 최종 반영 및 평가 결과

> [!IMPORTANT]
> 데이터 품질 검증(Quality Audit) 및 프로젝트 RAG 안전 가이드라인에 따라, 독립 파이프라인을 거친 데이터 중 최종 품질 및 고유성이 입증된 PlantSolve 28개 종의 생육 데이터만 Supabase pgvector DB에 최종 반영하였으며, EPPO 및 Perenual 데이터(병해충/농약 746건)는 전량 미반영(0건 제척) 처리하였습니다.

### 4.1 최종 DB 반영 내역
- 반영 출처: PlantSolve 생육 파이프라인 (`integrated_growth_care_chunks.jsonl`)
- 최종 반영 레코드: 28개 종 (28건)
- 주요 반영 식물(예시): 몬스테라, 알로카시아, 베고니아, 보스턴고사리, 칼라데아, 드라세나, 고무나무, 로즈마리, 라벤더, 스파티필룸, 페페로미아, 필로덴드론, 금전수, 산세베리아, 싱고니움, 접란 등 28종

---

## 💔 5. 결론 및 회고 (Conclusion & Retrospective)

### EPPO & Perenual 미반영에 대한 아쉬움
- EPPO Global Database와 Perenual API로부터 총 746건에 달하는 방대한 해외 병해충 및 농약 데이터를 수집하여 파싱, 정규화, 청킹, 임베딩 파이프라인까지 완성하였으나, 최종 서비스 DB에 한 건도 반영하지 못하고 전량 제외(0건)하게 된 점은 매우 아쉬운 부분입니다.
- 수집된 해외 병해충 데이터의 대다수가 특정 식물별 고유 정보 대신 공통 분류군 템플릿 문구로 충진되어 있어 텍스트 중복도가 지나치게 높았으며, 배포된 웹 사이트에서 유저에게 RAG 답변으로 노출될 경우 잘못되거나 일반화된 농약 처방이 안내되어 웹 서비스 전체의 신뢰도(Trustworthiness)와 답변 정확도를 크게 떨어뜨릴 위험이 존재하였습니다.
- 서비스 안정성과 유저 안전 가이드라인을 최우선으로 고려한 불가피한 제척 결정이었지만, 공들여 구축한 746개 레코드의 해외 데이터셋이 최종 백엔드 RAG 인덱스에 활용되지 못한 점은 이번 파이프라인 구축 과정에서 큰 한계와 아쉬움으로 남습니다.
