# 4차 프로젝트 Backend·RAG 품질 개선 계획

## 1. 문서 목적

이 문서는 4차 프로젝트의 목표인 LLM 연동 내·외부 문서 기반 질의응답 웹 애플리케이션 고도화 중 Backend와 RAG 영역의 개발 방향을 정의한다.

이번 개선의 핵심은 기존 Farm하니 기능을 유지하면서 다음 문제를 재현 가능하게 측정하고 줄이는 것이다.

- 사용자가 `a` 항목을 질문했지만 답변이 `b` 항목을 중심으로 생성되는 문제
- 질문한 식물과 다른 식물의 문서가 검색되는 문제
- 질문 의도와 관련 없는 RAG 문서가 citation으로 사용되는 문제
- 근거 문서가 부족한데도 확정적인 답변이 생성되는 문제
- 이전 대화, 이미지 분석, 재배일지의 노이즈가 현재 질문의 검색 의도를 덮는 문제

이 계획은 root `AGENTS.md`, `backend/AGENTS.md`, `backend/README.md`, `docs/rag-langgraph.md`, `contracts/api/openapi.yaml`의 규칙을 우선 적용한다.

> 이 문서는 코드 레벨 진단(4.6절, Phase 0.5)과 3차 프로젝트 평가 피드백 분석(8장)을 통합한 단일 계획 문서다. 별도로 존재하던 `docs/RAG품질개선계획(5차).md`의 내용은 이 문서로 병합되었다.

## 2. 작업 원칙

### 2.1 담당 범위

- 주 수정 범위: `backend/`
- API 계약 변경이 필요한 경우: `contracts/api/`
- RAG 설계 설명 갱신이 필요한 경우: `docs/rag-langgraph.md`
- `frontend/`, `server/`, root 문서, `.env.example`, 공유 아키텍처 문서는 담당자 및 팀장 합의 없이 수정하지 않는다.
- 작업 브랜치: `feature/backend-rag-quality-v4` (main 직접 커밋 금지, PR로 병합하고 설명에 영향 범위 명시)

### 2.2 보안·안전 원칙

- request body의 `user_id`를 신뢰하지 않고 검증된 Supabase JWT의 사용자 ID만 사용한다.
- `.env`, API key, service role key, DB password, access token을 커밋하거나 로그에 출력하지 않는다.
- RAG 답변에는 사용한 문서의 `sourceId`, `title`, `publisher` 또는 `url`을 포함한다.
- 병명 확정, 농약 직접 처방, 과장 표현을 허용하지 않는다.
- 농약·방제 정보에는 전문가 확인, 제품 라벨 및 안전사용기준 확인 안내를 포함한다.
- 근거가 부족하면 추측으로 채우지 않고 부족함을 명시하며 추가 사진이나 관찰 정보를 요청한다.

### 2.3 기존 파일 백업 원칙

기존 파일을 수정하기 전 같은 디렉터리에 작업 직전 원본 백업을 만든다.

권장 이름:

```text
<원본파일명>.backup-before-rag-v4
```

예:

```text
backend/app/services/rag/nodes_retrieval.py.backup-before-rag-v4
backend/app/services/rag/vectorstore.py.backup-before-rag-v4
```

운영 원칙:

1. 백업을 생성한 뒤 원본 파일만 수정한다.
2. 백업 파일은 실행·import·pytest 수집 대상에서 제외한다.
3. 백업에는 secret, 로그, 사용자 데이터가 포함되지 않도록 확인한다.
4. PR 전 백업 파일의 보관 또는 제거 여부를 팀장과 합의한다.
5. 변경 보고서에 원본 파일, 백업 파일, 변경 목적을 기록한다.

신규 파일은 기존 원본이 없으므로 별도 백업을 만들지 않는다.

## 3. 현재 기준선

현재 RAG는 다음 10단계 LangGraph 흐름을 사용한다.

```text
validate_input
→ load_chat_history
→ extract_image_signals
→ summarize_user_context
→ build_retrieval_query
→ retrieve_docs
→ grade_or_rerank
→ generate_answer
→ safety_review
→ persist_result
```

이미 적용된 개선:

- LLM 기반 Multi-Query 생성
- vector + keyword 하이브리드 검색
- Reciprocal Rank Fusion
- 조사 제거 및 식물명 관련 휴리스틱
- 후보 문서의 LLM 관련도 판정
- 답변 citation 구성
- 검색 결과 부족 시 fallback
- Vision 신호와 사용자 식물·재배일지 맥락 결합
- 안전성 검토
- LLM-as-a-Judge 평가 도구 및 평가 데이터셋

따라서 4차 개선에서는 위 기능을 다시 구현하기보다 오매칭 원인을 세분화하고 단계별 검증을 강화한다.

## 4. 문제 유형과 원인 가설

### 4.1 질문 의도 오분류

예:

- 물주기 질문에 병해충 답변
- 햇빛 질문에 비료 답변
- 현재 증상 질문에 일반 식물 소개 답변

원인 가설:

- 질문의 핵심 주제와 과거 대화·재배일지·Vision 신호가 한 검색 문자열에서 혼합됨
- 검색과 생성 단계가 현재 질문의 최우선 의도를 명시적으로 공유하지 않음
- 문서 관련도 판정이 단순 yes/no여서 주제 일치 정도를 구분하지 못함

### 4.2 식물종 오매칭

예:

- 몬스테라 질문에 무화과 또는 골드크레스트 문서 사용

원인 가설:

- 증상과 관리 용어의 의미 유사도가 식물종 불일치보다 높은 점수를 받음
- `crop_or_plant`, 식물 alias, 학명 등 metadata가 검색 필터로 충분히 사용되지 않음
- 일반 관리 문서와 특정 식물 전용 문서를 구분하는 정책이 부족함

### 4.3 근거 문서 품질 부족

원인 가설:

- `usage_scope`, `category`, `safety_tags`, publisher 등의 metadata gate가 약함
- 동일 출처·중복 chunk가 상위 후보를 차지해 근거 다양성이 떨어짐
- 질문에 답할 수 있는 문장 자체가 없는 문서도 제목이나 키워드만으로 통과함
- DB/RPC 결과의 score 의미와 임계값이 검색 방식별로 일관되지 않음

### 4.4 답변의 근거 이탈

원인 가설:

- 검색 문서에는 없는 행동을 LLM의 일반 지식으로 추가함
- 답변 항목별로 어떤 citation이 근거인지 연결되어 있지 않음
- 최종 답변 생성 후 각 주장과 문서의 entailment 검증이 없음

### 4.5 평가 사각지대

원인 가설:

- 기본 pytest에서 일부 RAG 테스트 파일이 수집되지 않음
- 실험 스크립트가 import 시 외부 API를 호출해 전체 테스트를 불안정하게 만듦
- 성공 사례 중심 데이터셋으로 hard negative와 no-answer 사례가 부족함
- 검색 품질과 답변 품질을 분리하지 않고 최종 점수만 평가함

### 4.6 코드 분석으로 확인된 근거 (가설 검증 결과)

4.1~4.5의 원인 가설 중 다음 항목은 실제 코드(`backend/app/services/rag/`)를 직접 읽어 **확인(CONFIRMED)**했다. Phase 0에서 재현 실험을 할 때 아래 지점을 우선 계측 대상으로 삼는다.

| 가설 | 상태 | 코드 근거 | 설명 |
|---|---|---|---|
| 4.2 식물종 오매칭 | **확인** | `nodes_retrieval.py:158-160` (`grade_or_rerank`) | 문서 관련성 판정 LLM 호출이 타임아웃(20초)·API 오류·JSON 파싱 실패 등 **어떤 이유로든 실패하면 필터링 없이 원본 후보 상위 4개를 그대로 통과**시키는 fail-open 구조. `max_retries=0`이라 일시 오류에도 즉시 이 경로로 빠진다. 이 노드는 식물종 오매칭을 막기 위한 핵심 안전장치이므로, 조용히 우회되는 순간 "가끔씩" 오매칭이 재발하는 패턴과 정확히 일치한다. |
| 4.2 식물종 오매칭 | **확인** | `vectorstore.py:78-114` (`filter_by_specific_terms`) | 식물의 사용자 지정 별명이나 `plant_catalog`에 없는 품종은 `known_plant_terms`에 걸리지 않아 `plant_terms`가 빈 리스트가 되고, 이 경우 느슨한 일반 토큰 매칭으로 전환되어 도감 미등록 식물에서는 다른 식물 문서 오매칭 사각지대가 남는다. |
| 4.1 질문 의도 오분류 | **확인** | `nodes_context.py:181-185` (`extract_image_signals`) | 현재 질문과 무관하게 **최근 재배일지 3건의 자유 텍스트(메모 포함)가 무조건 `image_signals`에 추가**되고, 이는 `retrieve_docs`의 `compact_query`(`nodes_retrieval.py:74-76`)에 그대로 들어가 검색 임베딩을 오염시킨다. 이미 `summarize_user_context`가 같은 정보를 `user_context`(답변 생성 전용)로 전달하므로 검색 쿼리 쪽 중복 주입은 제거 대상이다. |
| 4.3 근거 문서 품질 부족 | **부분 확인** | `nodes_retrieval.py:44-94`, `vectorstore.py:293-344` | `build_retrieval_query`가 생성한 키워드 3개는 실제로는 `" ".join(queries)`로 **한 문자열로 합쳐진 뒤 단 한 번의 임베딩**으로 검색된다("멀티 쿼리"라는 이름과 달리 팬아웃이 구현되어 있지 않음). 서로 다른 관점의 키워드가 하나의 벡터로 뭉개져 의미가 희석될 수 있다. |
| 4.5 평가 사각지대 | **확인** | `backend/tests/eval_dataset.json` | 골든셋이 12개뿐이며(12.2 A 참고), `grade_or_rerank`의 fail-open 케이스처럼 예외 상황을 강제로 재현하는 회귀 테스트가 없다. |
| (신규) | **확인** | `common.py:63-78` (`is_smalltalk_question`) | 8자 이하 질문에서 인사말 단어가 부분 문자열로 포함되면 스몰토크로 분류되어 검색 자체를 건너뛴다(`retrieved_docs: []`). 발생 빈도는 낮지만 짧은 후속 질문이 우연히 인사말 문자열을 포함하면 정상 질문인데도 검색이 생략된다. |
| 4.3 근거 문서 품질 부족 (검색 실패 방향) | **가설 유지** | `nodes_retrieval.py:131-137` (`grade_or_rerank` 프롬프트) | "같은 식물/작물이거나 질문에 직접 도움이 되는 일반 관리 원칙을 담을 때만 관련"이라는 기준이 모호해, 종을 명시하지 않은 일반 문서를 LLM 판사가 보수적으로 탈락시켜 `retrieved_docs`가 불필요하게 비어버릴 가능성이 있다(`docs/RAG품질개선보고서(4차).md` 9장의 `multi_1`, `multi_3` 하락 사례와 같은 유형). 실제 재현 데이터로 추가 확인 필요. |

## 5. 목표 지표

기존 평가 결과를 새 기준선 실행으로 다시 확정한 후 아래 목표를 적용한다.

| 지표 | 정의 | 1차 목표 |
|---|---|---:|
| Plant Match Accuracy | 최종 근거가 질문 대상 식물 또는 허용된 일반 관리 문서와 일치 | 95% 이상 |
| Intent Match Accuracy | 답변 중심 항목이 현재 질문 의도와 일치 | 95% 이상 |
| Retrieval Precision@5 | 상위 5개 후보 중 질문에 직접 유용한 문서 비율 | 80% 이상 |
| Context Recall | 정답 근거 문서가 후보군에 최소 1개 포함 | 90% 이상 |
| Citation Correctness | citation이 실제 답변 주장을 지원 | 95% 이상 |
| Groundedness | 주요 행동·원인 설명이 근거 또는 사용자 관찰에서 도출 | 90% 이상 |
| Wrong-topic Rate | `a` 질문에 `b` 중심 답변이 생성되는 비율 | 3% 이하 |
| Unsupported-answer Rate | 근거 부족 상황에서 확정 답변을 생성하는 비율 | 2% 이하 |
| Safety Compliance | 확정 진단·농약 직접 처방 제한 준수 | 100% |

품질뿐 아니라 다음 운영 지표도 기록한다.

- 전체 RAG 응답 시간
- 쿼리 확장 호출 시간
- reranking 호출 시간
- 검색 후보 수와 최종 채택 수
- fallback 비율
- OpenAI 호출 실패율
- 질문 1건당 LLM 호출 수와 예상 비용

## 6. 평가 데이터셋 설계

### 6.1 질문 분류

최소 다음 의도별로 평가 사례를 구성한다.

- 물주기
- 햇빛·광량
- 토양·배수
- 온도·습도·통풍
- 잎 황화
- 갈변·마름
- 반점·병해충 의심
- 영양·비료
- 분갈이
- 생육·새잎
- 일반 식물 정보
- 인사·잡담
- 정보 부족/no-answer

### 6.2 난이도 분류

- 단일 의도 질문
- 두 개 이상의 의도가 포함된 복합 질문
- 짧고 모호한 질문
- 이전 대화와 현재 질문의 주제가 달라지는 topic shift
- 과거 사진과 현재 질문이 충돌하는 경우
- 잘못된 전제를 포함한 질문
- 유사 증상을 공유하는 다른 식물이 hard negative로 검색되는 경우
- RAG 데이터에 해당 식물 문서가 없는 경우

### 6.3 각 평가 케이스 필수 필드

```json
{
  "id": "case-id",
  "plantName": "몬스테라",
  "species": "Monstera deliciosa",
  "question": "잎이 노란데 물을 더 줘야 하나요?",
  "expectedIntents": ["leaf_yellowing", "watering"],
  "forbiddenIntents": ["pesticide"],
  "expectedSourceKeys": [],
  "allowedCategories": ["indoor_plant_care", "water_management"],
  "forbiddenPlantNames": ["무화과", "골드크레스트"],
  "requiresFallback": false,
  "requiredSafetyTags": [],
  "notes": "과습 가능성을 단정하지 않고 흙 상태 추가 확인"
}
```

실제 사용자 질문은 익명화하고 개인 식별 정보와 업로드 URL을 제거한 뒤 회귀 사례로 추가한다.

## 7. 구현 단계

### Phase 0. 기준선 재현과 관측성 확보

목표:

- 현재 오답을 재현하고 어느 단계에서 주제가 바뀌는지 확인한다.

작업:

1. pytest가 외부 API 없이 안정적으로 수집되도록 테스트와 실험 스크립트의 실행 경계를 정리한다.
2. 기존 `backend/tests/test.py`의 수집 누락과 현재 구현 불일치를 분류한다.
3. 검색 단계별 결과를 평가 모드에서 기록한다.
   - 원 질문
   - 추출된 대상 식물
   - 분류된 질문 의도
   - 생성된 multi-query
   - 각 검색기의 후보와 원 점수
   - RRF 순위
   - rerank 판정과 탈락 이유
   - 최종 citation
4. 운영 로그에는 원문 질문, token, signed URL을 그대로 남기지 않고 request ID와 비식별 평가 정보만 남긴다.
5. 현재 평가 데이터셋으로 기준선 보고서를 생성한다.

완료 조건:

- 같은 입력으로 오매칭을 재현할 수 있다.
- 오매칭 발생 노드를 식별할 수 있다.
- 단위 테스트는 네트워크 없이 실행된다.

### Phase 0.5. 저위험 즉시 수정 (Quick Wins)

Phase 1~3의 구조적 개편(query plan, 다채널 검색, 다단계 reranking)은 설계·구현에 시간이 걸린다. 그 전에 4.6절에서 코드로 **확인된** 결함 중 위험도가 낮고 수정 방향이 명확한 항목을 먼저 반영해 빠르게 체감 품질을 개선하고, 이후 Phase의 기준선을 더 깨끗한 상태에서 재측정한다.

| # | 대상 결함(4.6절) | 조치 | 대상 파일 | 위험도 |
|---|---|---|---|---|
| QW-1 | `grade_or_rerank` fail-open | 실패 시 원본 미필터 문서를 그대로 통과시키지 않는다. 1회 재시도 후에도 실패하면 필터링 이전 후보군 전체가 아니라 **빈 리스트로 안전하게 축소(fail-closed)**하고 경고 로그를 남긴다 | `nodes_retrieval.py` | 낮음 |
| QW-2 | 재배일지 텍스트의 검색 쿼리 오염 | 재배일지 메모/잎상태/흙상태 텍스트를 `image_signals`(검색 쿼리 구성용)에서 제거하고, 이미 존재하는 `user_context`(답변 생성 전용)에만 남긴다 | `nodes_context.py` | 낮음 |
| QW-3 | 스몰토크 오탐 | 부분 문자열 대신 단어 경계 기준으로 인사말 판정을 강화한다 | `common.py` | 낮음 |
| QW-4 | 관측성 부재 | `retrieve_docs`/`grade_or_rerank`에 디버그 로그(쿼리, 후보 수, 필터 결과, fallback 발생 여부)를 추가한다 — Phase 0의 계측 요구사항과 동일 목적 | `nodes_retrieval.py` | 낮음 |

QW-1~QW-4는 Phase 1~3에서 다루는 다음 항목의 선행 조치이며 서로 충돌하지 않는다. 즉시 실행 검증 절차:

```bash
# 1. 백업 (2.3절 명명 규칙)
cp backend/app/services/rag/nodes_retrieval.py backend/app/services/rag/nodes_retrieval.py.backup-before-rag-v4
cp backend/app/services/rag/nodes_context.py backend/app/services/rag/nodes_context.py.backup-before-rag-v4
cp backend/app/services/rag/common.py backend/app/services/rag/common.py.backup-before-rag-v4

# 2. 회귀 테스트
cd backend && pytest tests -q

# 3. 골든셋 재평가 (Before/After 비교, 12.2 A 확장 전 기준)
python scripts/evaluate_rag.py
```

완료 조건:

- 기존 pytest 46개 테스트가 회귀 없이 통과한다.
- `grade_or_rerank`가 예외를 던지도록 강제한 테스트에서 무관 문서가 더 이상 통과하지 않는다(4.6절 CONFIRMED 항목의 회귀 테스트화).
- `evaluate_rag.py` 4대 지표가 기존 대비 하락하지 않는다.

### Phase 1. 질문 이해 구조화

목표:

- 현재 질문의 대상과 핵심 의도가 과거 맥락에 의해 덮이지 않게 한다.

계획:

1. `build_retrieval_query` 전에 또는 내부에서 구조화된 query plan을 생성한다.
2. query plan은 최소 다음 필드를 가진다.
   - `targetPlant`
   - `targetSpecies`
   - `primaryIntent`
   - `secondaryIntents`
   - `observedSymptoms`
   - `currentQuestion`
   - `contextTerms`
   - `excludedTopics`
   - `needsClarification`
3. 현재 질문을 primary signal로 두고, 대화 이력·재배일지·Vision 결과는 보조 signal로 구분한다.
4. topic shift가 감지되면 이전 대화의 검색 키워드를 축소한다.
5. LLM query parsing 실패 시 결정적인 rule-based fallback을 사용한다.

검증:

- 물주기 질문의 `primaryIntent`가 병해충로 바뀌지 않는다.
- 현재 질문과 무관한 이전 대화 키워드가 multi-query에 반복되지 않는다.

### Phase 2. 검색 후보 품질 개선

목표:

- 정답 문서 recall을 유지하면서 잘못된 식물·주제 문서를 후보 단계에서 줄인다.

계획:

1. 검색을 다음 채널로 분리한다.
   - vector semantic search
   - keyword/BM25 성격의 lexical search
   - 식물명·학명·alias 검색
   - category/intent 검색
2. 특정 식물 문서는 `crop_or_plant`, `source_key`, 학명, alias metadata를 우선 활용한다.
3. 식물 전용 문서가 없을 때만 일반 식물 관리 문서를 허용하는 계층적 fallback을 적용한다.
4. `usage_scope`를 강제한다.
   - `rag`: 일반 답변 근거 가능
   - `reference_only`: 제한적으로 사용
   - `safety_reference_only`: 안전 검토 외 직접 답변 근거 제한
5. 동일 source/chunk 중복을 제거하고 출처 다양성을 확보한다.
6. RRF 입력 순위와 벡터 similarity를 함께 보존해 디버깅 가능하게 한다.
7. 임계값은 전체 공통값 하나로 정하지 않고 검색 채널 및 문서 유형별 calibration을 검토한다.

검증:

- 질문 식물과 명백히 다른 식물의 전용 문서는 최종 후보에서 제외된다.
- 일반 관리 문서는 특정 식물 전용 문서가 없거나 내용이 보편적일 때만 허용된다.
- 정답 문서 recall이 기존 기준선보다 하락하지 않는다.

### Phase 3. 다단계 reranking과 품질 gate

목표:

- 단순 yes/no 판정을 넘어 식물·의도·근거 충분성을 각각 검증한다.

계획:

1. 저비용 deterministic pre-filter:
   - 금지된 `usage_scope`
   - 명백한 식물종 불일치
   - 빈 본문·필수 metadata 누락
   - 중복 chunk
2. reranker 결과를 구조화한다.

```json
{
  "plantMatch": 0,
  "intentMatch": 0,
  "answerability": 0,
  "sourceQuality": 0,
  "decision": "keep",
  "reason": "..."
}
```

3. `plantMatch`, `intentMatch`, `answerability` 중 핵심 조건을 만족하지 못한 문서는 제거한다.
4. hard negative 문서를 별도 평가하여 판정 프롬프트를 회귀 테스트한다.
5. 모든 문서가 탈락하면 상위 문서를 강제로 살리지 않고 clarification/fallback 분기로 이동한다.
6. 평가 모델 실패 시 낮은 신뢰도의 문서를 자동 승인하지 않는다.

검증:

- 식물은 맞지만 질문 주제가 다른 문서가 탈락한다.
- 질문 주제는 비슷하지만 다른 식물 전용 문서가 탈락한다.
- 문서가 하나도 남지 않는 상황이 정상적인 fallback으로 처리된다.

### Phase 4. 근거 기반 답변 생성

목표:

- 최종 답변이 현재 질문과 채택된 문서 범위를 벗어나지 않게 한다.

계획:

1. 생성 프롬프트에 `primaryIntent`와 답변 범위를 명시한다.
2. 각 답변 항목을 사용자 관찰 또는 citation에 연결한다.
3. 문서에 없는 수치, 주기, 농약명, 희석 배수는 생성하지 않는다.
4. 복합 질문은 의도별로 답하되 질문하지 않은 항목을 중심 답변으로 확장하지 않는다.
5. citation은 실제 사용한 문서만 포함하고 단순 검색 후보는 포함하지 않는다.
6. 현재 API 응답 형식을 유지하는 것을 기본으로 하며, 계약 변경이 필요하면 프론트 및 팀장 승인 후 진행한다.

검증:

- `todayActions`가 질문 의도와 관련된다.
- 원인 후보와 행동이 citation 또는 사용자 관찰로 뒷받침된다.
- citation 제목만 관련 있고 본문은 무관한 사례가 통과하지 않는다.

### Phase 5. 답변 후 검증과 안전한 fallback

목표:

- 생성 후 남은 주제 이탈과 근거 없는 주장을 최종 차단한다.

계획:

1. `safety_review`를 다음 두 검증으로 확장하는 방안을 평가한다.
   - safety/policy 검증
   - groundedness/intent alignment 검증
2. 답변 후 검증 항목:
   - 질문의 primary intent에 답했는가
   - 다른 식물 정보가 섞였는가
   - 주요 주장마다 근거가 있는가
   - 확정 진단 또는 직접 처방 표현이 있는가
3. 검증 실패 시 한 번만 제한적으로 재생성하고, 다시 실패하면 구조화된 fallback을 반환한다.
4. fallback에는 다음을 포함한다.
   - 현재 근거로 판단하기 어렵다는 안내
   - 확인 가능한 관찰 내용
   - 필요한 추가 사진·흙 상태·최근 환경 변화
   - 최소 1개의 허용 가능한 일반 출처 또는 명시적인 근거 부족 상태

### Phase 6. 회귀 테스트와 평가 보고서

테스트 계층:

- Unit: query parsing, 식물 alias, intent 분류, pre-filter, RRF, citation dedup
- Retrieval: Precision@k, Recall@k, hard negative rejection
- Generation: intent match, groundedness, citation correctness
- Safety: 확정 진단, 농약 직접 처방, 근거 부족 fallback
- Memory: topic shift, 세션 이어가기, 이전 이미지 맥락
- API: 인증 실패, 소유권 차단, 정상 상담, SSE
- Evaluation: 실제 Supabase/OpenAI를 사용하는 명시적 실행

비교 방법:

1. 동일한 고정 데이터셋과 설정으로 개선 전 기준선 측정
2. 한 번에 한 변경만 적용하는 ablation 평가
3. 개선 후 동일 데이터셋 재평가
4. 점수가 낮아진 항목은 원인과 trade-off 기록
5. 최종 보고서에 평균뿐 아니라 의도별·식물별·난이도별 결과 포함

## 8. 첨부 평가계획서 분석 및 추가 보완사항

분석 문서:

```text
SKN 30기_LLM(초거대언어모델)_평가계획서_3팀.pdf
```

### 8.1 기존 평가 결과 요약

기존 프로젝트의 총점은 95/100점이며 항목별 결과는 다음과 같다.

| 평가 영역 | 배점 | 기존 점수 | 확인된 강점 |
|---|---:|---:|---|
| 수집 데이터 및 전처리 문서 | 20 | 19 | 신뢰 가능한 출처, 출처별 청킹·오버랩 차등 적용, 문장 경계 보존 |
| 시스템 아키텍처 | 30 | 29 | Supabase pgvector, OpenAI, Vision, FastAPI, React의 실서비스형 연결 |
| RAG·Vector DB 구현 | 30 | 28 | 출처 기반 답변, 문서 외 질문 제한, 모듈화된 RAG 구조 |
| 테스트 계획 및 결과 | 20 | 19 | LLM-as-a-Judge, 12개 골든셋, 4대 지표, 개선 과정 정량 추적 |

평가에서 특히 긍정적으로 확인된 요소는 다음과 같다.

- 공식 원예·농업 자료와 식물 프로필, 재배일지, 사진을 결합한 문제 정의
- 문서에 없는 내용을 답하지 않는 환각 방지
- 출처 특성을 반영한 전처리와 청킹
- `contracts/api/openapi.yaml`을 통한 API 계약 관리
- migration 기반 DB 스키마 변경 이력 관리
- `services/rag/` 하위 기능 분리로 확보한 유지보수성
- 배포 환경과 검증 흐름을 함께 고려한 실무형 구조

이 강점은 4차 개선에서도 훼손하지 않고 회귀 테스트로 보호한다.

### 8.2 평가 의견에서 도출된 필수 보완

#### A. 테스트 질문셋 규모 확대

기존 12개 골든셋은 개선 방향을 확인하기에는 유용하지만 식물종, 질문 의도, 난이도, 실패 유형별 성능을 판단하기에는 작다.

추가 계획:

- (교차 참조) Phase 0.5는 이 확장 이전에 먼저 반영할 저위험 코드 수정을 정리해 둔 선행 단계다.
- 1차 목표로 최소 50개 이상의 고정 회귀 질문셋을 구축한다.
- 가능하면 100개까지 확장하되 평가 API 비용과 수동 검수 가능 범위를 함께 고려한다.
- 식물종과 질문 유형별 분포를 함께 공개해 특정 식물 또는 물주기 질문에 편향되지 않게 한다.
- 실제 오답 사례를 익명화해 hard negative 회귀셋에 포함한다.
- train/tuning 용도와 최종 holdout 평가셋을 분리해 평가 데이터에 대한 과적합을 줄인다.

#### B. 평가표가 요구하는 질문 유형 명시

평가계획서의 테스트 기준에 따라 다음 유형을 반드시 포함한다.

| 질문 유형 | 평가 목적 | 예시 검증 |
|---|---|---|
| 사실 확인형 | 특정 관리 정보의 정확성 | 문서에 명시된 광량·온도·관리 조건과 일치 |
| 요약형 | 여러 근거의 핵심 정리 | 문서의 핵심을 왜곡 없이 간결하게 요약 |
| 비교형 | 둘 이상의 조건·식물·관리법 구분 | 비교 대상의 근거가 섞이지 않고 각각 citation 연결 |
| 문서에 없는 질문 | 환각 억제와 fallback | 모른다는 점과 필요한 추가 정보를 명확히 반환 |
| 복합형 | 여러 의도의 우선순위 처리 | 의도별로 답하되 질문하지 않은 주제로 이탈하지 않음 |
| 멀티턴형 | 이전 맥락 활용과 topic shift | 과거 문맥을 활용하되 현재 질문을 우선 |
| 멀티모달형 | 사진과 문서 근거의 결합 | 사진 관찰과 문서 사실을 구분하여 표현 |

#### C. 검색 결과와 최종 답변을 분리 평가

평가표의 관련성·정확도·근거성·환각 여부를 하나의 총점으로만 합치지 않는다.

평가 계층:

1. Retrieval
   - 식물종 일치
   - 질문 의도 일치
   - Precision@k
   - Recall@k
   - 중복 문서 비율
2. Generation
   - 사실 정확성
   - 질문 응답성
   - 문서 기반성
   - citation 정확성
3. Safety/Fallback
   - 문서 외 주장 여부
   - 확정 진단 여부
   - 농약 직접 처방 여부
   - 근거 부족 시 거절·추가 질문 품질

각 실패가 검색 실패인지 생성 실패인지 구분할 수 있도록 케이스별 trace를 보고서에 남긴다.

#### D. 모델·Vector DB 선정 근거 보완

시스템 아키텍처 평가 항목에 맞춰 다음을 최신 상태로 문서화한다.

- 채팅·Vision 모델 선정 이유와 대안 모델 대비 품질·비용·지연시간
- `text-embedding-3-small` 및 1536차원 사용 근거
- Supabase pgvector 선정 이유와 데이터 규모·운영 편의성
- vector, keyword, metadata 검색을 함께 사용하는 이유
- LangGraph 노드 분리와 품질 gate가 유지보수성에 주는 효과

모델 또는 임베딩 모델을 변경할 경우 동일 평가셋으로 품질·비용·지연시간을 비교한 뒤 결정한다.

#### E. 운영 지연 대응 전략

재직자 평가 의견에 따라 사용자 증가 시 응답 지연을 별도 운영 요구사항으로 관리한다.

측정 항목:

- API 전체 p50, p95 응답 시간
- query planning 시간
- vector/keyword 검색 시간
- reranking 시간
- Vision 및 답변 생성 시간
- Supabase 조회 시간
- SSE 첫 progress 이벤트까지의 시간

검토할 개선안:

- 식물 alias·카탈로그와 반복 query 결과의 제한적 캐시
- 독립적인 검색 채널의 병렬 실행
- rerank 후보 수 상한 설정
- timeout과 retry를 노드별로 구분
- Vision이 필요 없는 요청에서 이미지 분석 호출 방지
- 문서 판정의 batch 처리 가능성 검토
- DB index와 RPC 실행 계획 확인
- 동시 사용자 수별 부하 테스트와 병목 기록

정확도를 낮추는 성급한 캐시는 적용하지 않으며 cache key에는 식물종, 질문 의도, 데이터 버전을 포함한다.

#### F. API 호출 비용 관리 전략

다음 비용을 질문 1건 단위로 분리 측정한다.

- query planning
- 문서 reranking
- Vision
- 답변 생성
- LLM-as-a-Judge 평가
- embedding 또는 재검색

비용 관리 방안:

- 결정적 규칙으로 처리 가능한 식물명·의도 필터를 LLM 호출 전에 수행
- reranking 대상 문서 수 제한
- 중복 chunk 제거 후 LLM 판정
- query planning 실패 시 무제한 재시도 금지
- Vision은 실제 사진이 있고 상담에 필요한 경우에만 호출
- 운영 모델과 평가 Judge 모델 호출을 분리 집계
- 월별 또는 발표 환경의 호출 예산과 경고 기준 정의
- 품질 개선 전후에 질문당 호출 수, token, 예상 비용을 함께 보고

#### G. 오류 처리와 실행 안정성 보완

평가 항목의 실무적 완성도를 위해 다음을 명시적으로 검증한다.

- OpenAI timeout, rate limit, 일시 장애
- Supabase 연결 실패
- vector RPC 실패
- Vision 실패
- JSON 구조화 응답 파싱 실패
- reranker 전체 탈락
- citation metadata 누락
- SSE 처리 중 오류

외부 서비스 장애가 잘못된 확정 답변으로 이어지지 않도록 실패 유형별 fallback과 사용자 메시지를 정의한다.

**코드 레벨로 이미 확인된 사례**: `grade_or_rerank`(`nodes_retrieval.py:158-160`)는 LLM 판정 호출이 실패하면 필터링 없이 원본 후보 문서를 그대로 통과시키는 fail-open 구조다(4.6절, Phase 0.5 QW-1 참조). 이는 이 평가 항목의 감점(30점 중 28점)과 직접 관련될 가능성이 높은 가장 구체적인 사례이므로, Phase 0.5·Phase 3의 최우선 회귀 테스트로 고정한다.

#### H. 멀티모달 평가 확대

기존 평가 의견의 이미지·영상 확장 제안을 반영하되 4차 범위에서는 이미지 기반 품질을 먼저 안정화한다.

- 정상 잎, 황화, 갈변, 반점, 시듦 등 이미지 유형 확대
- 이미지 관찰 결과와 사용자 텍스트가 일치하는 경우와 충돌하는 경우를 모두 평가
- Vision 관찰을 확정 진단이 아닌 관찰 신호로 제한
- 같은 이미지에 다른 질문을 결합해 질문 의도별 검색 결과가 달라지는지 확인
- 영상은 저장·비용·개인정보·처리 지연을 검토한 후 별도 확장 과제로 둔다.

### 8.3 평가 산출물 보완

최종 테스트 보고서에는 다음을 포함한다.

- 평가셋 구성표와 질문 유형별 개수
- 데이터 출처와 정답 근거 작성 기준
- 개선 전·후 동일 케이스 비교
- 의도별·식물별·난이도별 점수
- 검색 후보와 최종 citation 비교
- 대표 성공 사례와 실패 사례
- 응답 시간과 질문당 API 비용
- fallback 발생률과 원인
- 적용한 프롬프트, 검색 설정, threshold, 후보 수
- 미해결 한계와 후속 개선 계획

LLM-as-a-Judge 점수만 사용하지 않고 중요한 사례는 사람이 citation과 원문을 교차 검수한다. Judge 모델이 답변 생성 모델과 같을 경우 발생할 수 있는 평가 편향도 보고서에 명시한다.

## 9. 예상 수정 후보

실제 수정 여부는 Phase 0 분석 후 확정한다.

| 파일 | 예상 목적 |
|---|---|
| `backend/app/services/rag/common.py` | 구조화된 query plan 및 state 타입 |
| `backend/app/services/rag/nodes_context.py` | 현재 질문과 이전 맥락 분리, topic shift |
| `backend/app/services/rag/nodes_retrieval.py` | 의도 추출, metadata filter, 구조화 rerank |
| `backend/app/services/rag/vectorstore.py` | 검색 채널, 점수 보존, 중복 제거, threshold calibration |
| `backend/app/services/rag/nodes_generation.py` | intent 고정, 근거 기반 생성, 답변 후 검증 |
| `backend/app/services/rag/pipeline.py` | 필요 시 검증/fallback 분기 추가 |
| `backend/tests/` | 단위·검색·회귀·API 테스트 |
| `backend/scripts/evaluate_rag.py` | 세부 평가 지표와 before/after 비교 |
| `backend/tests/eval_dataset.json` | hard negative, topic shift, no-answer 사례 확장 |
| `contracts/api/openapi.yaml` | 응답 계약 변경이 승인된 경우에만 수정 |

## 10. 적용 순서와 중단 기준

권장 적용 순서:

```text
기준선 측정
→ 테스트 격리
→ 질문 구조화
→ metadata/pre-filter
→ reranking 개선
→ 근거 기반 생성
→ 답변 후 검증
→ 통합·성능 평가
→ 문서·결과 보고서
```

다음 상황에서는 다음 단계 적용을 중단하고 원인을 먼저 해결한다.

- Context Recall이 기준선보다 유의미하게 하락
- 응답 시간이 허용 기준을 초과
- LLM 호출 수 또는 비용이 과도하게 증가
- citation 정확도가 하락
- 농약·확정 진단 안전 테스트 실패
- API 계약 변경이 필요한데 프론트/팀장 합의가 없음

## 11. 산출물

4차 프로젝트 필수 산출물과 연결하여 다음 결과를 준비한다.

- 요구사항 정의: 기능·품질·안전·성능 요구사항
- 시스템 구성도: 개선된 RAG 품질 gate와 fallback 분기
- 개발된 LLM 연동 웹 애플리케이션: 개선된 Backend RAG
- 테스트 계획 및 결과 보고서:
  - 개선 전 기준선
  - 단계별 ablation 결과
  - 개선 후 결과
  - 실패 사례와 제한점
- API 계약 영향 보고
- 변경 파일 및 백업 파일 목록
- 환경변수 추가 여부

## 12. 완료 정의

다음 조건을 모두 만족할 때 RAG 품질 개선 작업을 완료로 판단한다.

1. 평가 데이터셋의 모든 질문이 재현 가능한 방식으로 실행된다.
2. 목표 지표를 충족하거나 미충족 사유와 한계를 보고서에 기록한다.
3. `a` 항목 질문에 `b` 중심 답변이 나오는 대표 회귀 사례가 테스트로 고정되고 통과한다.
4. 다른 식물 전용 문서가 최종 citation으로 채택되는 대표 사례가 차단된다.
5. 근거가 없을 때 안전한 fallback이 반환된다.
6. 인증·사용자 격리·citation·안전 테스트가 통과한다.
7. 기본 테스트는 실제 OpenAI/Supabase 네트워크 호출 없이 실행된다.
8. 실제 연동 평가는 별도 명시적 명령으로만 실행된다.
9. 기존 API 호환성을 유지하거나 승인된 계약 변경이 반영된다.
10. 코드 변경 전 백업과 변경 내역이 기록된다.
