"""RAG 파이프라인 공용 헬퍼 및 상태 정의."""
import re
from datetime import date, datetime, timezone
from typing import Dict, Any, List, Optional, TypedDict

from supabase import Client


def plant_persona_status(plant: Dict[str, Any], care_logs: List[Dict[str, Any]]) -> str:
    """
    companion(내 식물과 대화하기) 모드에서 식물이 1인칭으로 말할 수 있는
    실제 케어 상태 요약을 만든다. 예: "나 8일째 물 못 마셨어. 권장 주기 7일이 지났어."
    """
    from app.services.rag.plant_terms import watering_interval_days

    today = datetime.now(timezone.utc).date()
    lines: List[str] = []

    # 마지막 물주기 경과 vs 권장 주기
    interval = watering_interval_days(plant.get("name"), plant.get("species"))
    watered_dates = []
    for log in care_logs or []:
        raw = log.get("watered_at")
        if raw:
            try:
                watered_dates.append(date.fromisoformat(str(raw)[:10]))
            except ValueError:
                continue
    if watered_dates:
        days_since = (today - max(watered_dates)).days
        if days_since <= 0:
            lines.append(f"오늘 물을 마셨어. 권장 주기는 {interval}일이야.")
        elif days_since >= interval:
            lines.append(f"물 마신 지 {days_since}일째야. 권장 주기 {interval}일이 지나서 목이 마른 상태야.")
        elif interval - days_since <= 1:
            lines.append(f"물 마신 지 {days_since}일 됐어. 권장 주기가 {interval}일이라 내일쯤 물이 필요해.")
        else:
            lines.append(f"물 마신 지 {days_since}일 됐고 권장 주기는 {interval}일이라 아직 괜찮아.")
    else:
        lines.append(f"아직 물 준 기록이 없어서 내 물 컨디션은 정확히 몰라. 권장 주기는 {interval}일이야.")

    # 함께한 기간
    created_raw = plant.get("created_at")
    if created_raw:
        try:
            adopted = date.fromisoformat(str(created_raw)[:10])
            days_together = (today - adopted).days
            if days_together >= 1:
                lines.append(f"주인님과 함께한 지 {days_together}일째야.")
        except ValueError:
            pass

    return " ".join(lines)


def document_safety_tags(doc: Any) -> List[str]:
    """검색 문서 하나에서 안전 태그를 읽는다.

    로더(data/scripts/load_supabase_pgvector.py)가 최상위 snake_case 컬럼 대신
    metadata의 camelCase 키에만 값을 넣는 경우가 있어 양쪽을 모두 확인한다.
    """
    if not isinstance(doc, dict):
        return []
    metadata = doc.get("metadata") or {}
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("safety_tags") or metadata.get("safetyTags") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(tag).strip() for tag in raw if str(tag).strip()]


def make_excerpt(text: str, max_len: int = 220) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_len:
        return clean
    return clean[:max_len].rstrip() + "..."


def is_smalltalk_question(question: str) -> bool:
    normalized = " ".join((question or "").strip().lower().split())
    if not normalized:
        return True
    greetings = {
        "hi",
        "hello",
        "hey",
        "안녕",
        "안녕하세요",
        "안녕?",
        "안녕하세요?",
        "고마워",
        "감사합니다",
    }
    return normalized in greetings or (len(normalized) <= 8 and any(word in normalized for word in greetings))


def extract_user_name(text: str) -> Optional[str]:
    patterns = [
        r"(?:내\s*이름은|제\s*이름은)\s*([가-힣A-Za-z0-9_]{2,20}?)(?:이야|야|입니다|이에요|예요|라고|$)",
        r"(?:나는|전|저는)\s*([가-힣A-Za-z0-9_]{2,20}?)(?:이야|야|입니다|이에요|예요|라고|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip())
        if match:
            return match.group(1).strip()
    return None


def recall_user_name(question: str, chat_history: List[Dict[str, Any]]) -> Optional[str]:
    current_name = extract_user_name(question)
    if current_name:
        return current_name
    for item in reversed(chat_history):
        if item.get("role") != "user":
            continue
        remembered = extract_user_name(str(item.get("content") or ""))
        if remembered:
            return remembered
    return None


def is_user_name_question(question: str) -> bool:
    normalized = " ".join((question or "").strip().split())
    return bool(re.search(r"(내|제)\s*이름.*(뭐|누구|기억|알)", normalized))


def make_session_title(plant: Dict[str, Any], question: str) -> str:
    plant_label = (plant.get("name") or plant.get("species") or "식물").strip()
    clean_question = " ".join((question or "").split())
    if len(clean_question) > 24:
        clean_question = clean_question[:24].rstrip() + "..."
    if not clean_question:
        clean_question = "상담"
    return f"{plant_label} · {clean_question}"

def chat_mode_prefix(response_mode: str) -> str:
    return "[내 식물]" if response_mode == "companion" else "[전문가]"

def make_mode_session_title(plant: Dict[str, Any], question: str, response_mode: str) -> str:
    return f"{chat_mode_prefix(response_mode)} {make_session_title(plant, question)}"

class AgentState(TypedDict):
    # 입력 정보
    db_client: Client
    user_id: str
    plant_id: Optional[str]
    garden_id: Optional[str]
    context_type: str
    care_log_id: Optional[str]
    photo_id: Optional[str]
    question: str
    response_mode: str
    request_chat_history: List[Dict[str, Any]]
    chat_history: List[Dict[str, Any]]
    target_session_id: Optional[str]
    
    # 런타임 획득 정보
    plant_data: Dict[str, Any]
    care_logs: List[Dict[str, Any]]
    photo_data: Dict[str, Any]
    recent_photos: List[Dict[str, Any]]
    
    # 노드 결과물
    image_signals: List[str]
    image_description: str
    vision_error: Optional[str]
    user_context: str
    search_query: str
    retrieved_docs: List[Dict[str, Any]]
    # 농약 무관 질문이라 근거에서 제외한 농약 문서 수 (pesticide_guard)
    pesticide_docs_filtered: int
    draft_answer: Dict[str, Any]
    final_answer: Dict[str, Any]
    generation_notice: Optional[str]
    session_id: Optional[str]
    message_id: Optional[str]
    new_session: bool
