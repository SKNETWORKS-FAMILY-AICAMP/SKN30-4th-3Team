"""5~7단계: 검색 쿼리 생성, 문서 검색, 문서 적합성 필터링 노드."""
import logging
import os
from typing import Dict, Any

from app.core.config import settings
from app.services.llm import chat_completion
from app.services.llm.schemas import (
    QueryExpansion,
    QuestionScopeDecision,
    RerankDecision,
    parse_json_object,
)
from app.services.rag.common import (
    AgentState,
    QUESTION_SCOPE_OUT_OF_SCOPE,
    QUESTION_SCOPE_PLANT_CARE,
    QUESTION_SCOPE_UNDETERMINED,
    classify_question_scope,
)
from app.services.rag.pesticide_guard import apply_pesticide_guard
from app.services.rag.plant_terms import resolve_target_crop_terms
from app.services.rag.vectorstore import CANDIDATE_POOL_SIZE, search_documents

logger = logging.getLogger(__name__)

RERANK_INPUT_COUNT = 8


def classify_question_scope_semantically(state: AgentState) -> str:
    """불명확한 질문을 검색 전에 의미 기반으로 분류한다.

    등록 식물 정보가 프롬프트에 있더라도 현재 질문이 그 식물의 관리와 직접
    관련되지 않으면 out_of_scope여야 한다. 호출이나 파싱이 실패하면 무관 문서가
    검색되는 것보다 답변을 제한하는 편이 안전하므로 fail-closed 처리한다.
    """
    question = state.get("question") or ""
    plant = state.get("plant_data") or {}
    history = state.get("chat_history") or []
    history_text = "\n".join(
        f"{item.get('role')}: {item.get('content')}"
        for item in history[-4:]
        if item.get("content")
    )
    try:
        completion = chat_completion(
            primary_model=state.get("llm_model") or os.getenv("CHAT_MODEL") or settings.CHAT_MODEL,
            local_model=settings.LOCAL_CHAT_MODEL,
            preferred_provider=state.get("llm_provider") or "openai",
            temperature=0.0,
            response_format={"type": "json_object"},
            primary_timeout=10.0,
            max_tokens=100,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 식물·텃밭 관리 상담 서비스의 질문 범위 분류기입니다. "
                        "현재 질문의 실제 의도만 보고 다음 셋 중 하나로 분류하세요. "
                        "plant_care: 등록 식물/텃밭의 재배, 생육, 환경, 병해충, 사진 상태를 묻거나 직전 식물 상담의 자연스러운 후속 질문. "
                        "smalltalk: 인사, 감사, 이름 기억, 가벼운 대화. "
                        "out_of_scope: 그 밖의 일반 지식, 역사, 과학, 금융, 전자기기, 의료, 요리 등 식물 관리에 답할 필요가 없는 질문. "
                        "상담 대상 식물명이 별도로 제공됐다는 이유만으로 plant_care를 선택하지 마세요. "
                        "JSON {\"scope\": \"plant_care|smalltalk|out_of_scope\"}만 반환하세요."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"상담 대상: {plant.get('name') or ''} / {plant.get('species') or ''}\n"
                        f"사진 첨부: {'있음' if state.get('photo_data') else '없음'}\n"
                        f"직전 대화:\n{history_text or '없음'}\n\n"
                        f"현재 질문: {question}"
                    ),
                },
            ],
        )
        raw = str(completion.response.choices[0].message.content or "").strip()
        return QuestionScopeDecision.model_validate(parse_json_object(raw)).scope
    except Exception as exc:
        logger.warning("Question scope classification failed; blocking retrieval: %s", exc)
        return QUESTION_SCOPE_OUT_OF_SCOPE


# 4. build_retrieval_query 노드
def build_retrieval_query(state: AgentState) -> Dict[str, Any]:
    plant = state["plant_data"]
    question = state["question"]
    signals = ", ".join(state["image_signals"])
    context = state.get("user_context", "")
    image_description = state.get("image_description") or ""

    question_scope = state.get("question_scope") or classify_question_scope(question)
    if question_scope == QUESTION_SCOPE_UNDETERMINED:
        question_scope = classify_question_scope_semantically(state)
    if question_scope != QUESTION_SCOPE_PLANT_CARE:
        return {"search_query": "", "question_scope": question_scope}
    
    openai_key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    if openai_key or (settings.LLM_FALLBACK_ENABLED and settings.LOCAL_LLM_AUXILIARY_ENABLED):
        try:
            prompt = (
                "당신은 식물 관리 RAG 시스템의 검색 쿼리 생성기입니다. "
                "주어진 상황에서 가장 관련성 높은 문서를 찾기 위한 검색 키워드 3개를 만드세요. "
                "JSON 형식으로 {'queries': ['키워드1', '키워드2', '키워드3']} 반환하세요."
            )
            completion = chat_completion(
                primary_model=os.getenv("CHAT_MODEL") or settings.CHAT_MODEL,
                local_model=settings.LOCAL_CHAT_MODEL,
                temperature=0.1,
                response_format={"type": "json_object"},
                primary_timeout=12.0,
                max_tokens=300,
                allow_local_fallback=settings.LOCAL_LLM_AUXILIARY_ENABLED,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"질문: {question}\n식물: {plant.get('species') or plant.get('name')}\n징후: {signals}\n사진: {image_description}"}
                ]
            )
            raw_content = str(completion.response.choices[0].message.content or "").strip()
            ans = QueryExpansion.model_validate(parse_json_object(raw_content))
            queries = ans.queries[:3]
            if queries:
                return {
                    "search_query": " ".join(queries),
                    "question_scope": question_scope,
                }
        except Exception as e:
            logger.warning("Query expansion failed: %s", e)

    query_text = (
        f"식물: {plant.get('species') or plant.get('name')}. "
        f"사용자 질문: {question}. "
        f"관찰 징후: {signals}. "
        f"사진 분석: {image_description}. "
        f"관리 맥락: {context}"
    )
    return {"search_query": query_text, "question_scope": question_scope}

# 5. retrieve_docs 노드
def retrieve_docs(state: AgentState) -> Dict[str, Any]:
    question_scope = state.get("question_scope") or classify_question_scope(state.get("question") or "")
    if question_scope != QUESTION_SCOPE_PLANT_CARE:
        return {"retrieved_docs": [], "pesticide_docs_filtered": 0}
    plant = state.get("plant_data") or {}
    question = state.get("question") or ""
    compact_query = " ".join(
        str(part)
        for part in [
            plant.get("name"),
            plant.get("species"),
            question,
            ", ".join(state.get("image_signals") or []),
            state.get("image_description") or "",
        ]
        if part
    )
    generated_query = state.get("search_query") or ""
    query_parts = [compact_query, generated_query]
    query = " ".join(dict.fromkeys(part.strip() for part in query_parts if part and part.strip()))
    if not query.strip():
        query = generated_query

    # 사용자가 등록한 식물의 name/species를 도감 용어로 정규화해, rag_chunks의
    # crop_or_plant 구조화 태그와 직접 비교할 작물명 후보를 만든다. 자유 텍스트 쿼리
    # 파싱보다 신뢰도가 높아 근연종(가지과 등) 오매칭을 결정적으로 차단할 수 있다.
    target_crop_terms = resolve_target_crop_terms(plant.get("name"), plant.get("species"))
    # DB 검색은 이미 최대 CANDIDATE_POOL_SIZE개의 후보를 수집한다. 농약 문서가
    # 상위 결과를 차지하더라도 일반 공식 문서가 재정렬 입력에서 밀려나지 않도록,
    # 후보 전체에서 무관한 농약 문서를 먼저 제외한 뒤 상위 8개를 선택한다.
    search_results = search_documents(
        query,
        top_k=CANDIDATE_POOL_SIZE,
        target_crop_terms=target_crop_terms,
    )

    docs = []
    for res in search_results:
        docs.append({
            "content": res.content,
            "metadata": res.metadata,
            "score": res.score
        })
    docs, excluded = apply_pesticide_guard(question, docs)
    if excluded:
        logger.info(
            "Pesticide guard: removed %d off-topic pesticide candidate(s) before reranking",
            excluded,
        )
    return {
        "retrieved_docs": docs[:RERANK_INPUT_COUNT],
        "pesticide_docs_filtered": excluded,
    }

def _finalize_docs(state: AgentState, docs: list) -> Dict[str, Any]:
    """grade_or_rerank의 단일 출구.

    LLM 리랭커가 예외/키없음으로 fail-open 하는 경로에서도 농약 가드는 반드시
    지나가야 하므로, 모든 반환을 이 함수로 모은다.
    """
    question = state["question"]
    kept, excluded = apply_pesticide_guard(question, docs)
    previously_excluded = int(state.get("pesticide_docs_filtered") or 0)
    if excluded:
        logger.info(
            "Pesticide guard: dropped %d off-topic pesticide doc(s) for a non-pesticide question",
            excluded,
        )
    return {
        "retrieved_docs": kept,
        "pesticide_docs_filtered": previously_excluded + excluded,
    }


# 6. grade_or_rerank 노드
def grade_or_rerank(state: AgentState) -> Dict[str, Any]:
    docs = state["retrieved_docs"]
    question = state["question"]
    question_scope = state.get("question_scope") or classify_question_scope(question)
    if question_scope != QUESTION_SCOPE_PLANT_CARE:
        return _finalize_docs(state, [])
    plant = state.get("plant_data") or {}
    plant_label = " ".join(
        str(part).strip()
        for part in [plant.get("name"), plant.get("species")]
        if part
    ) or "unknown plant"
    openai_key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    
    if not docs or (
        not openai_key
        and not (settings.LLM_FALLBACK_ENABLED and settings.LOCAL_LLM_AUXILIARY_ENABLED)
    ):
        return _finalize_docs(state, docs[:4])
        
    try:
        # 후보 문서 전체를 한 프롬프트에 담아 1회 호출로 배치 채점한다.
        # (문서당 개별 호출 대비 지연/비용을 문서 수만큼 절감)
        doc_blocks = []
        for idx, doc in enumerate(docs):
            title = (doc.get("metadata") or {}).get("title") or "제목 없음"
            content = str(doc.get("content") or "")[:1200]
            doc_blocks.append(f"[문서 {idx}]\n제목: {title}\n내용: {content}")

        completion = chat_completion(
            primary_model=os.getenv("CHAT_MODEL") or settings.CHAT_MODEL,
            local_model=settings.LOCAL_CHAT_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
            primary_timeout=20.0,
            max_tokens=400,
            allow_local_fallback=settings.LOCAL_LLM_AUXILIARY_ENABLED,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 RAG 시스템의 문서 관련성 평가기입니다. 사용자의 질문에 답하기 위해 각 문서가 유용한지 판별합니다. "
                        "사용자가 특정 식물/작물에 대해 묻는 경우, 문서가 같은 식물/작물이거나 질문에 직접 도움이 되는 일반 관리 원칙을 담을 때만 관련 있다고 판단하세요. "
                        "문서가 다른 식물/작물 전용이고 현재 질문과 무관하면 반드시 제외하세요. "
                        "관련 있는 문서의 인덱스만 배열로 담아 JSON 형식 {\"relevant\": [0, 2]} 로만 응답하세요. "
                        "관련 문서가 하나도 없으면 {\"relevant\": []} 를 반환하세요."
                    )
                },
                {
                    "role": "user",
                    "content": f"식물/작물: {plant_label}\n질문: {question}\n\n{chr(10).join(doc_blocks)}"
                }
            ]
        )
        raw = str(completion.response.choices[0].message.content or "").strip()
        parsed = RerankDecision.model_validate(parse_json_object(raw))
        relevant_indices = parsed.relevant

        filtered_docs = []
        for idx in relevant_indices:
            if isinstance(idx, int) and 0 <= idx < len(docs):
                filtered_docs.append(docs[idx])
                if len(filtered_docs) >= 4:
                    break

        # 모두 무관 판정이면 빈 리스트 유지 — 무관 문서를 억지로 주입하지 않는다 (환각 방지)
        return _finalize_docs(state, filtered_docs)
    except Exception as e:
        logger.warning("Reranking failed: %s", e)
        # 범위 밖 질문은 함수 진입 시 이미 빈 문서로 fail-closed 처리됐다.
        # 식물 관리 질문만 기존 가용성 정책에 따라 상위 후보를 보존한다.
        return _finalize_docs(state, docs[:4])
