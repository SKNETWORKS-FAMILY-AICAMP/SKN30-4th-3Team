"""LangGraph RAG 파이프라인 조립 및 실행 진입점.

노드 구현은 단계별 모듈(nodes_context / nodes_retrieval / nodes_generation /
nodes_persistence)로 분리되어 있으며, 이 모듈은 워크플로우 조립과 실행,
그리고 기존 임포트/몽키패치 경로 호환을 위한 재노출(façade)을 담당한다.
"""
import logging
from typing import Dict, Any, List, Optional

from supabase import Client
from langgraph.graph import StateGraph, END

from app.core.config import settings  # noqa: F401 - 테스트가 pipeline.settings를 참조
from app.services.rag.vectorstore import search_documents  # noqa: F401 - 하위 호환 재노출
from app.services.rag.vision import analyze_plant_image  # noqa: F401 - 하위 호환 재노출
from app.services.rag import nodes_context as _nodes_context
from app.services.rag.common import (  # noqa: F401 - 하위 호환 재노출
    AgentState,
    chat_mode_prefix,
    classify_question_scope,
    extract_user_name,
    is_smalltalk_question,
    is_user_name_question,
    make_excerpt,
    make_mode_session_title,
    make_session_title,
    recall_user_name,
)
from app.services.rag.nodes_context import load_chat_history, summarize_user_context, validate_input
from app.services.rag.nodes_retrieval import (
    build_retrieval_query,
    grade_or_rerank,
    retrieve_docs,
)
from app.services.rag.nodes_generation import generate_answer, safety_review
from app.services.rag.nodes_persistence import persist_result

logger = logging.getLogger(__name__)


def extract_image_signals(state: AgentState) -> Dict[str, Any]:
    """Compatibility wrapper for tests that monkeypatch pipeline.analyze_plant_image."""
    original = _nodes_context.analyze_plant_image
    _nodes_context.analyze_plant_image = analyze_plant_image
    try:
        return _nodes_context.extract_image_signals(state)
    finally:
        _nodes_context.analyze_plant_image = original

# 스트리밍 진행 표시용 노드 순서 및 사용자 안내 라벨
WORKFLOW_NODE_LABELS: List[tuple] = [
    ("validate_input", "식물 정보를 확인하고 있어요"),
    ("load_chat_history", "이전 대화를 불러오고 있어요"),
    ("extract_image_signals", "사진을 분석하고 있어요"),
    ("summarize_user_context", "식물 상태를 정리하고 있어요"),
    ("build_retrieval_query", "검색어를 만들고 있어요"),
    ("retrieve_docs", "공식 문서를 검색하고 있어요"),
    ("grade_or_rerank", "문서 적합성을 검증하고 있어요"),
    ("generate_answer", "답변을 작성하고 있어요"),
    ("safety_review", "안전성을 검토하고 있어요"),
    ("persist_result", "상담 내역을 저장하고 있어요"),
]


def _build_workflow_app():
    workflow = StateGraph(AgentState)

    workflow.add_node("validate_input", validate_input)
    workflow.add_node("load_chat_history", load_chat_history)
    workflow.add_node("extract_image_signals", extract_image_signals)
    workflow.add_node("summarize_user_context", summarize_user_context)
    workflow.add_node("build_retrieval_query", build_retrieval_query)
    workflow.add_node("retrieve_docs", retrieve_docs)
    workflow.add_node("grade_or_rerank", grade_or_rerank)
    workflow.add_node("generate_answer", generate_answer)
    workflow.add_node("safety_review", safety_review)
    workflow.add_node("persist_result", persist_result)

    workflow.set_entry_point("validate_input")
    workflow.add_edge("validate_input", "load_chat_history")
    workflow.add_edge("load_chat_history", "extract_image_signals")
    workflow.add_edge("extract_image_signals", "summarize_user_context")
    workflow.add_edge("summarize_user_context", "build_retrieval_query")
    workflow.add_edge("build_retrieval_query", "retrieve_docs")
    workflow.add_edge("retrieve_docs", "grade_or_rerank")
    workflow.add_edge("grade_or_rerank", "generate_answer")
    workflow.add_edge("generate_answer", "safety_review")
    workflow.add_edge("safety_review", "persist_result")
    workflow.add_edge("persist_result", END)

    return workflow.compile()


def _build_initial_state(
    db_client: Client,
    user_id: str,
    plant_id: Optional[str],
    garden_id: Optional[str],
    care_log_id: Optional[str],
    photo_id: Optional[str],
    question: str,
    new_session: bool,
    response_mode: str,
    recent_messages: Optional[List[Dict[str, Any]]],
    session_id: Optional[str],
    llm_provider: Optional[str],
    llm_model: Optional[str],
) -> Dict[str, Any]:
    return {
        "db_client": db_client,
        "user_id": user_id,
        "plant_id": plant_id,
        "garden_id": garden_id,
        "context_type": "garden" if garden_id else "plant",
        "care_log_id": care_log_id,
        "photo_id": photo_id,
        "question": question,
        "question_scope": classify_question_scope(question),
        "response_mode": response_mode if response_mode in {"expert", "companion"} else "expert",
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "request_chat_history": recent_messages or [],
        "new_session": new_session,
        "target_session_id": session_id
    }


def _final_answer_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    final_answer = dict(state["final_answer"])
    final_answer["sessionId"] = state.get("session_id")
    final_answer["messageId"] = state.get("message_id")
    final_answer["llmProvider"] = state.get("llm_provider_used")
    final_answer["llmModel"] = state.get("llm_model_used")
    return final_answer


def run_rag_workflow(
    db_client: Client,
    user_id: str,
    plant_id: Optional[str],
    care_log_id: Optional[str],
    photo_id: Optional[str],
    question: str,
    new_session: bool = False,
    response_mode: str = "expert",
    recent_messages: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    garden_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> Dict[str, Any]:
    app = _build_workflow_app()
    initial_state = _build_initial_state(
        db_client, user_id, plant_id, garden_id, care_log_id, photo_id,
        question, new_session, response_mode, recent_messages, session_id,
        llm_provider, llm_model
    )
    result = app.invoke(initial_state)
    return _final_answer_from_state(result)


def run_rag_workflow_stream(
    db_client: Client,
    user_id: str,
    plant_id: Optional[str],
    care_log_id: Optional[str],
    photo_id: Optional[str],
    question: str,
    new_session: bool = False,
    response_mode: str = "expert",
    recent_messages: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    garden_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
):
    """
    파이프라인을 노드 단위로 실행하며 진행 이벤트를 순차 반환하는 제너레이터.

    yield 형식:
      {"type": "progress", "step": n, "total": 10, "node": 이름, "label": 안내문}
      {"type": "result", "data": 최종 답변}
    """
    app = _build_workflow_app()
    initial_state = _build_initial_state(
        db_client, user_id, plant_id, garden_id, care_log_id, photo_id,
        question, new_session, response_mode, recent_messages, session_id,
        llm_provider, llm_model
    )

    node_order = {name: idx for idx, (name, _) in enumerate(WORKFLOW_NODE_LABELS)}
    node_labels = dict(WORKFLOW_NODE_LABELS)
    total = len(WORKFLOW_NODE_LABELS)

    merged_state: Dict[str, Any] = dict(initial_state)
    # 첫 진행 이벤트를 즉시 내보내 사용자에게 시작을 알린다
    yield {"type": "progress", "step": 1, "total": total,
           "node": "validate_input", "label": node_labels["validate_input"]}

    for update in app.stream(initial_state):
        # update: {완료된_노드명: 해당_노드_출력}
        for node_name, node_output in update.items():
            if isinstance(node_output, dict):
                merged_state.update(node_output)
            completed_idx = node_order.get(node_name)
            if completed_idx is not None and completed_idx + 1 < total:
                next_name, next_label = WORKFLOW_NODE_LABELS[completed_idx + 1]
                yield {"type": "progress", "step": completed_idx + 2, "total": total,
                       "node": next_name, "label": next_label}

    yield {"type": "result", "data": _final_answer_from_state(merged_state)}
