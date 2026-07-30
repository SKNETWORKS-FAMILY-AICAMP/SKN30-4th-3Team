# 4차 프로젝트 Backend·RAG 품질 개선 계획 (v2 — 통합판)

> 이 문서는 아래 두 개의 독립적인 분석 문서를 비교·검증하여 하나로 통합한 결과다.
> 1. `backend/RAG_QUALITY_IMPROVEMENT_PLAN.md` (v1) — 코드 실행 흐름 레벨 진단 + 3차 평가 피드백 반영, Phase 0~6 실행 계획
> 2. `RAG_원인분석_및_개선보고서.md` (강성준, `~/Downloads`) — 임베딩 공간/IR 이론 레벨 진단, 토마토→감자 오매칭 실사례 기반 6대 솔루션
>
> v1은 삭제하지 않는다. 두 문서 모두 유효한 관점이며, v1의 Phase 구조를 그대로 뼈대로 유지하면서 신규 보고서의 발견을 어디에 편입할지 명시하는 것이 이 문서의 목적이다. **실행 시에는 이 v2 문서를 기준으로 삼는다.**

---

## 0. 왜 통합이 필요한가

두 문서는 같은 현상("질문 A인데 답변 B", "식물종 오매칭")을 서로 다른 층위에서 진단했다.

| | v1 (`RAG_QUALITY_IMPROVEMENT_PLAN.md`) | 신규 보고서 (`RAG_원인분석_및_개선보고서.md`) |
|---|---|---|
| 진단 층위 | **파이프라인 코드 실행 흐름** — 특정 함수의 설계 결함 | **임베딩 표현 공간 / IR 이론** — 모델과 검색 구조 자체의 한계 |
| 대표 사례 | 일반화된 "a 질문 → b 답변" 패턴 | 구체적 실사례: 토마토 질문 → 감자(가지과) 문서 |
| 핵심 발견 | `grade_or_rerank`의 fail-open(오류 시 무필터 통과), 재배일지 텍스트가 검색 쿼리를 오염, 멀티쿼리가 실제로는 단일 임베딩으로 뭉쳐짐 | 가지과(Solanaceae) 식물 간 코사인 유사도가 **0.88~0.93**으로 식물종 차이보다 증상 유사성을 더 크게 반영 — Dense Embedding의 근본적 한계. 벡터 검색(RPC) 단계에 **metadata 사전 필터가 아예 없음** |
| 제안 해법의 성격 | 기존 코드/인프라 재사용 위주 — 저위험, 빠른 반영 가능 | 신규 구조 도입 — BM25/FTS 인덱스, Cross-Encoder reranker, 청크 재임베딩, 벡터 RPC에 metadata 필터 추가 |
| 팀 의존성 | 대부분 backend 단독 작업 가능 | Data 팀(청킹/재임베딩), 경우에 따라 Server 팀(reranker 인프라) 협조 필요 |

**결론: 두 진단은 모순되지 않고 같은 문제의 다른 단면이다.** v1이 찾은 것은 "이미 있는 안전장치가 조용히 우회되는 버그"이고, 신규 보고서가 찾은 것은 "애초에 안전장치가 충분히 강하지 않다"는 구조적 한계다. 후자가 해결되면 전자의 영향(빈도)도 줄어들고, 전자가 먼저 해결되면 후자를 구현하는 동안의 회귀를 방지할 수 있다. 따라서 **순서는 v1의 저위험 수정을 먼저 적용하고, 신규 보고서의 구조적 개선을 이어서 반영**한다.

---

## 1. 코드 검증 — 신규 보고서 주장의 사실 확인

신규 보고서를 그대로 받아들이기 전에, 실제 코드(`backend/app/services/rag/vectorstore.py`)를 다시 확인했다. 보고서의 일부 전제는 **수정이 필요**하다.

### 1.1 "식물종 메타데이터가 없다" → 부분적으로 사실이 아님

- 보고서는 "`crop_name`이나 `plant_species`와 같은 식물종 메타데이터 조건 없이 전체 1,682개 청크를 대상으로 전역 검색"한다고 진단했다.
- 실제로는 **`crop_or_plant`라는 배열 컬럼이 이미 `rag_chunks` 테이블에 존재**하며 (`vectorstore.py:170` `KEYWORD_SELECT`), 키워드 검색 경로(`supabase_keyword_search`, `vectorstore.py:217`)에서는 `crop_or_plant.cs.{term}` (배열 포함 검증)로 이미 사용되고 있다.
- **그러나 벡터 검색 RPC(`match_rag_chunks`) 호출에는 이 필터가 전달되지 않는다** (`vectorstore.py:314-321`, 파라미터는 `query_embedding`, `match_threshold`, `match_count`뿐). 즉 1차 벡터 검색(top 80 후보 수집) 단계에서는 가지과 식물 전체가 무차별적으로 경쟁하고, `filter_by_specific_terms`(`vectorstore.py:78-114`)는 이미 뽑힌 80개 후보에 대해서만 사후(post-hoc) 필터링을 한다.
- **의미:** 정답 문서가 상위 80위 안에 아예 못 들어올 정도로 감자 문서가 압도적으로 많거나 유사도가 높으면, 사후 필터링으로는 애초에 복구할 수 없다. 신규 보고서의 "재적재(P0)"는 과장이지만, **"벡터 RPC 자체에 metadata 필터를 추가해야 한다"는 핵심 주장은 코드로 확인된 진짜 gap이다.**

### 1.2 "BM25/희소 키워드 가중치가 없다" → 부분적으로 사실이 아님

- `supabase_keyword_search`(`vectorstore.py:198-252`)가 이미 존재하고, `merge_results`의 RRF(`vectorstore.py:146-168`)로 벡터 검색과 병합된다.
- 다만 이는 PostgREST의 `ilike`/배열 포함 기반 검색이라 정식 BM25/전문검색 랭킹 함수(`ts_rank`, `pg_trgm` 등)가 아니며, 식물명(Entity) 매칭에 벡터 검색보다 유의하게 높은 가중치를 주는 구조도 아니다. **"경량 키워드 필터는 있으나 진짜 BM25 랭킹은 없다"**가 정확한 현재 상태다.

### 1.3 "청크 내 식물명 누락 시 LLM 필터가 식별 못 함" → 코드로 확인 불가, 데이터 검증 필요

- 이는 `data/scripts/chunk_documents.py`의 청킹 결과물에 달린 문제라 backend 코드만으로는 확인할 수 없다. Data 담당자에게 샘플 청크를 요청해 청크 경계에서 식물명이 잘리는 비율을 확인해야 한다.

---

## 2. 통합 근본 원인 목록

기존 v1의 4.6절(코드 확인 항목)과 신규 보고서의 원인을 하나의 목록으로 재분류한다.

### 2.1 로직/코드 결함 (backend 단독 수정 가능, 저위험) — v1 Phase 0.5 대상

| 원인 | 근거 |
|---|---|
| `grade_or_rerank` fail-open | `nodes_retrieval.py:158-160` |
| 재배일지 텍스트의 검색 쿼리 오염 | `nodes_context.py:181-185` |
| 멀티쿼리가 단일 임베딩으로 뭉개짐 | `nodes_retrieval.py:44-94` |
| 스몰토크 오탐으로 검색 생략 | `common.py:63-78` |

### 2.2 검색 구조의 근본 한계 (신규 보고서 제기, 구조 개편 필요) — v1 Phase 1~3 대상으로 편입

| 원인 | 근거 | 편입 대상 |
|---|---|---|
| 벡터 RPC에 식물종 metadata 사전 필터 부재 | 1.1절 검증 결과, `vectorstore.py:314-321` | **Phase 2(검색 후보 품질 개선)** — 이미 계획에 "특정 식물 문서는 `crop_or_plant`, `source_key`, 학명, alias metadata를 우선 활용"이 있으나, 이번 검증으로 **"RPC 파라미터로 직접 전달"**까지 구체화 필요 |
| 가지과 등 근연종 간 Dense Embedding 유사도 자체가 높음(0.88~0.93) | 신규 보고서 2장, 생물분류학적으로 타당한 지적 | Phase 2의 hard negative 필터링 근거로 채택. 완전히 없앨 수는 없는 임베딩 모델의 본질적 특성이므로 **필터/재정렬로 보완**하는 게 목표이지 "해결"은 아님 |
| 진짜 BM25 랭킹 부재 | 1.2절 검증 결과 | Phase 2 검색 채널 분리 항목에 "PostgreSQL FTS(`ts_rank`)로 교체 검토" 추가 |
| Cross-Encoder reranker 부재(LLM judge만 존재) | 신규 보고서 Solution 4 | **Phase 3(다단계 reranking)**의 대안/보완 옵션으로 편입. 단, Cohere API 비용 또는 BGE 자체 호스팅 인프라가 필요해 **Server 담당자 협의 필수** — P2로 유지 |
| 청크 재임베딩(제목/식물명 prepend) | 신규 보고서 Solution 2 | Data 팀 작업. 4차 backend 단독 범위 밖이므로 **cross-team 의존 항목**으로 별도 관리 |

### 2.3 데이터/청킹 이슈 (Data 담당자 검증 필요)

| 원인 | 근거 | 액션 |
|---|---|---|
| 청크 경계에서 식물명 누락 가능성 | 신규 보고서 2장 4)항, 코드로 검증 불가 | Data 담당자에게 감자/토마토 관련 청크 샘플 10~20건 요청, 식물명 미포함 비율 확인 |
| 감자/토마토 등 근연종 데이터 불균형 | 신규 보고서 2장 2)항 | `data/catalog/`에서 작물별 청크 수 집계 요청 |

---

## 3. 통합 실행 계획 — v1 Phase 구조에 신규 항목 배치

기존 v1의 순서(`기준선 측정 → 테스트 격리 → 질문 구조화 → metadata/pre-filter → reranking 개선 → 근거 기반 생성 → 답변 후 검증 → 통합·성능 평가 → 문서화`)는 그대로 유지한다. 아래는 신규 보고서 내용을 어느 Phase에 넣을지에 대한 매핑이다.

- **Phase 0 (기준선 재현)**: 토마토→감자 사례를 재현 케이스로 반드시 포함. 신규 보고서가 제시한 구체적 재현 질문("토마토 잎에 붉은 반점이 생기고 말라가는데...")을 `eval_dataset.json`에 hard negative 케이스로 추가.
- **Phase 0.5 (Quick Wins, 저위험)**: 변경 없음 — 2.1절 항목 그대로 유지, 먼저 적용.
- **Phase 1 (질문 이해 구조화)**: 신규 보고서 Solution 6(Multi-Query에 식물명을 필수 키워드로 고정)을 `build_retrieval_query` 프롬프트 규칙에 반영 — query plan의 `targetPlant`/`targetSpecies` 필드가 이미 이 역할을 하도록 설계되어 있으므로, 생성되는 각 쿼리 문자열에 `targetPlant`를 강제 접두하는 구현 규칙만 추가.
- **Phase 2 (검색 후보 품질 개선)**: **가장 중요한 편입 지점.**
  - 신규 보고서 Solution 1(metadata 사전 필터링)을 `match_rag_chunks` RPC 파라미터 확장으로 구체화 (`supabase/migrations/`에 RPC 함수 시그니처 변경 마이그레이션 필요 — Data/DB 담당자 협의).
  - 신규 보고서 Solution 3(BM25 가중치)을 PostgreSQL FTS 도입 검토 항목으로 추가.
  - RPC에 필터를 못 넣는 경우의 대안으로, `VECTOR_CANDIDATE_COUNT`(현재 80)를 식물명이 불확실한 질의에서만 상향하는 절충안도 병기.
- **Phase 3 (다단계 reranking)**: 신규 보고서 Solution 4(Cross-Encoder)를 "LLM judge 대체"가 아니라 **"LLM judge 앞단의 저비용 pre-filter"**로 위치시킨다 (v1 Phase 3의 "저비용 deterministic pre-filter"에 이미 있는 자리). 신규 도입 여부는 Server/비용 협의 후 P2로 유지.
- **Phase 4~5 (생성/검증)**: 신규 보고서 Solution 5(투명한 caveat fallback 문구)를 `generate_answer`의 "근거 부족" 분기 프롬프트에 구체 예시로 반영 — 이미 v1에 "근거 부족 시 명시적 거절"이 있으므로, "동종 데이터 참고" 표현 패턴만 추가.
- **Phase 6 (회귀 테스트)**: 토마토/감자 케이스를 hard negative 골든셋에 고정 포함.

---

## 4. Cross-Team 의존성 (backend 단독으로 끝낼 수 없는 항목)

`backend/AGENTS.md`에 따라 backend 담당자는 `backend/`, `contracts/api/`만 수정할 수 있다. 아래 항목은 **다른 담당자와 사전 합의가 필요**하다.

| 항목 | 필요 협의 대상 | 사유 |
|---|---|---|
| 청크 헤더 prepend 후 재임베딩 (Solution 2) | Data 담당자 | `data/scripts/` 파이프라인 실행, 1,682건 재임베딩 비용 발생 |
| `match_rag_chunks` RPC 시그니처에 필터 파라미터 추가 | Data/DB 담당자 (팀장 승인) | `supabase/migrations/` 마이그레이션, 함수 자체가 공유 자산 |
| Cross-Encoder reranker(Cohere/BGE) 도입 | Server 담당자 | 신규 API 키/과금(Cohere) 또는 신규 호스팅 인프라(BGE 자체 서빙) 필요 |
| 청크 경계 식물명 누락 비율 검증 | Data 담당자 | 청킹 로직/원본 데이터 접근 필요 |

이 문서(backend 담당)는 위 항목에 대해 **구체적인 요청 스펙**(원하는 RPC 파라미터 형태, 필요한 prepend 포맷 등)을 준비하는 역할까지만 수행하고, 실제 반영은 각 담당자 승인 후 진행한다.

---

## 5. 우선순위 재정렬 (통합 P0/P1/P2)

| 우선순위 | 항목 | 출처 | 담당 |
|---|---|---|---|
| P0 | Phase 0.5 Quick Wins 4건 (fail-closed, 컨텍스트 오염 제거, 스몰토크 경계, 로깅) | v1 | Backend 단독 |
| P0 | 토마토/감자 등 hard negative 케이스를 골든셋에 추가 | 신규 보고서 + v1 Phase 0 | Backend 단독 |
| P1 | `build_retrieval_query`에 식물명 필수 포함 규칙 | 신규 보고서 Solution 6 | Backend 단독 |
| P1 | `match_rag_chunks` RPC 필터 파라미터 요청 스펙 작성 및 Data팀 협의 시작 | 신규 보고서 Solution 1 + 1.1절 검증 | Backend → Data 협의 |
| P2 | PostgreSQL FTS 기반 진짜 BM25 검토 | 신규 보고서 Solution 3 | Backend 단독(검토), Server 인프라 확인 |
| P2 | 청크 prepend 재임베딩 요청 | 신규 보고서 Solution 2 | Data팀 주도 |
| P2 | Cross-Encoder reranker 도입 검토 | 신규 보고서 Solution 4 | Server팀 협의 |
| P2 | 투명한 caveat fallback 문구 반영 | 신규 보고서 Solution 5 | Backend 단독 |

---

## 6. 다음 단계

1. 이 문서(v2)에 대한 팀 리뷰 — 특히 4장 cross-team 의존 항목을 Data/Server 담당자와 공유
2. `feature/backend-rag-quality-v4` 브랜치에서 Phase 0.5 Quick Wins부터 백업 후 구현 (`backend/RAG_QUALITY_IMPROVEMENT_PLAN.md` 2.3절 백업 규칙 적용)
3. Phase 0 재현 데이터셋에 토마토→감자 케이스 추가 후 `evaluate_rag.py`로 기준선 재측정
4. Phase 2의 RPC 필터 스펙을 Data/DB 담당자에게 전달하고 마이그레이션 일정 협의
