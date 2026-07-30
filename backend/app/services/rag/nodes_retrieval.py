"""5~7단계: 검색 쿼리 생성, 문서 검색, 문서 적합성 필터링 노드."""
import logging
import os
from typing import Dict, Any

from app.core.config import settings
from app.services.rag.common import AgentState, is_smalltalk_question
from app.services.rag.pesticide_guard import apply_pesticide_guard
from app.services.rag.plant_terms import resolve_target_crop_terms
from app.services.rag.vectorstore import search_documents

logger = logging.getLogger(__name__)


# 4. build_retrieval_query 노드
def build_retrieval_query(state: AgentState) -> Dict[str, Any]:
    plant = state["plant_data"]
    question = state["question"]
    signals = ", ".join(state["image_signals"])
    context = state.get("user_context", "")
    image_description = state.get("image_description") or ""

    if is_smalltalk_question(question):
        return {"search_query": ""}
    
    openai_key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    if openai_key:
        try:
            import json
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, timeout=12.0, max_retries=0)
            prompt = (
                "당신은 식물 관리 RAG 시스템의 검색 쿼리 생성기입니다. "
                "주어진 상황에서 가장 관련성 높은 문서를 찾기 위한 검색 키워드 3개를 만드세요. "
                "JSON 형식으로 {'queries': ['키워드1', '키워드2', '키워드3']} 반환하세요."
            )
            res = client.chat.completions.create(
                model=os.getenv("CHAT_MODEL") or settings.CHAT_MODEL,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"질문: {question}\n식물: {plant.get('species') or plant.get('name')}\n징후: {signals}\n사진: {image_description}"}
                ]
            )
            raw_content = str(res.choices[0].message.content or "").strip()
            if raw_content.startswith("```"):
                raw_content = raw_content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            ans = json.loads(raw_content)
            queries = ans.get("queries") or []
            if queries:
                return {"search_query": " ".join(queries)}
        except Exception as e:
            logger.warning("Query expansion failed: %s", e)

    query_text = (
        f"식물: {plant.get('species') or plant.get('name')}. "
        f"사용자 질문: {question}. "
        f"관찰 징후: {signals}. "
        f"사진 분석: {image_description}. "
        f"관리 맥락: {context}"
    )
    return {"search_query": query_text}

# 5. retrieve_docs 노드
def retrieve_docs(state: AgentState) -> Dict[str, Any]:
    if is_smalltalk_question(state.get("question") or ""):
        return {"retrieved_docs": []}
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
    search_results = search_documents(query, top_k=8, target_crop_terms=target_crop_terms)

    docs = []
    for res in search_results:
        docs.append({
            "content": res.content,
            "metadata": res.metadata,
            "score": res.score
        })
    return {"retrieved_docs": docs}

def _finalize_docs(question: str, docs: list) -> Dict[str, Any]:
    """grade_or_rerank의 단일 출구.

    LLM 리랭커가 예외/키없음으로 fail-open 하는 경로에서도 농약 가드는 반드시
    지나가야 하므로, 모든 반환을 이 함수로 모은다.
    """
    kept, excluded = apply_pesticide_guard(question, docs)
    if excluded:
        logger.info(
            "Pesticide guard: dropped %d off-topic pesticide doc(s) for a non-pesticide question",
            excluded,
        )
    return {"retrieved_docs": kept, "pesticide_docs_filtered": excluded}


# 6. grade_or_rerank 노드
def grade_or_rerank(state: AgentState) -> Dict[str, Any]:
    docs = state["retrieved_docs"]
    question = state["question"]
    plant = state.get("plant_data") or {}
    plant_label = " ".join(
        str(part).strip()
        for part in [plant.get("name"), plant.get("species")]
        if part
    ) or "unknown plant"
    openai_key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    
    if not openai_key or not docs:
        return _finalize_docs(question, docs[:4])
        
    try:
        import json as _json
        from openai import OpenAI
        client = OpenAI(api_key=openai_key, timeout=20.0, max_retries=0)

        # 후보 문서 전체를 한 프롬프트에 담아 1회 호출로 배치 채점한다.
        # (문서당 개별 호출 대비 지연/비용을 문서 수만큼 절감)
        doc_blocks = []
        for idx, doc in enumerate(docs):
            title = (doc.get("metadata") or {}).get("title") or "제목 없음"
            content = str(doc.get("content") or "")[:1200]
            doc_blocks.append(f"[문서 {idx}]\n제목: {title}\n내용: {content}")

        res = client.chat.completions.create(
            model=os.getenv("CHAT_MODEL") or settings.CHAT_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
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
        raw = str(res.choices[0].message.content or "").strip()
        parsed = _json.loads(raw)
        relevant_indices = parsed.get("relevant") or []

        filtered_docs = []
        for idx in relevant_indices:
            if isinstance(idx, int) and 0 <= idx < len(docs):
                filtered_docs.append(docs[idx])
                if len(filtered_docs) >= 4:
                    break

        # 모두 무관 판정이면 빈 리스트 유지 — 무관 문서를 억지로 주입하지 않는다 (환각 방지)
        return _finalize_docs(question, filtered_docs)
    except Exception as e:
        logger.warning("Reranking failed: %s", e)
        return _finalize_docs(question, docs[:4])
