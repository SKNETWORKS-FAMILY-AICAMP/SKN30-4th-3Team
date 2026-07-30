"""1~4단계: 입력 검증, 대화 이력, 이미지 신호, 사용자 맥락 요약 노드."""
import logging
from typing import Dict, Any, Optional

from app.services.rag.common import AgentState, chat_mode_prefix
from app.services.rag.vision import VisionAnalysisError, analyze_plant_image

logger = logging.getLogger(__name__)


# 1. validate_input 노드
def validate_input(state: AgentState) -> Dict[str, Any]:
    db = state["db_client"]
    plant_id = state["plant_id"]
    garden_id = state.get("garden_id")
    user_id = state["user_id"]

    if garden_id:
        garden_response = db.table("gardens").select("*").eq("id", garden_id).eq("user_id", user_id).execute()
        if not garden_response.data:
            raise ValueError("텃밭을 찾을 수 없거나 해당 텃밭에 접근할 권한이 없습니다.")

        member_response = db.table("plants").select("*").eq("garden_id", garden_id).eq("user_id", user_id).execute()
        members = member_response.data or []
        member_ids = [item["id"] for item in members]
        logs = []
        if member_ids:
            logs_response = (
                db.table("care_logs")
                .select("*")
                .in_("plant_id", member_ids)
                .order("created_at", desc=True)
                .limit(12)
                .execute()
            )
            logs = logs_response.data or []

        garden = garden_response.data[0]
        member_labels = [
            " / ".join(part for part in [item.get("name"), item.get("species")] if part)
            for item in members
        ]
        photo_data = {}
        if state.get("photo_id"):
            photo_response = (
                db.table("plant_photos")
                .select("*")
                .eq("id", state["photo_id"])
                .eq("garden_id", garden_id)
                .execute()
            )
            if not photo_response.data:
                raise ValueError("텃밭 사진을 찾을 수 없거나 해당 텃밭에 연결된 사진이 아닙니다.")
            photo_data = photo_response.data[0]

        recent_photos_response = (
            db.table("plant_photos")
            .select("*")
            .eq("garden_id", garden_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )

        return {
            "plant_data": {
                "id": garden["id"],
                "name": garden.get("name"),
                "species": ", ".join(label for label in member_labels if label) or garden.get("representative_crop") or "등록된 식물 없음",
                "location": garden.get("location"),
                "sunlight": garden.get("sunlight"),
                "soil_type": garden.get("soil_type"),
                "description": garden.get("description"),
                "cultivation_type": garden.get("cultivation_type") or "mixed",
                "representative_crop": garden.get("representative_crop"),
                "context_type": "garden",
                "member_plants": members,
            },
            "care_logs": logs,
            "photo_data": photo_data,
            "recent_photos": recent_photos_response.data or [],
        }

    plant_response = db.table("plants").select("*").eq("id", plant_id).eq("user_id", user_id).execute()
    if not plant_response.data:
        raise ValueError("식물을 찾을 수 없거나 해당 식물에 대한 접근 권한이 없습니다.")
    
    if state.get("care_log_id"):
        selected_log_res = (
            db.table("care_logs")
            .select("*")
            .eq("id", state["care_log_id"])
            .eq("plant_id", plant_id)
            .execute()
        )
        if not selected_log_res.data:
            raise ValueError("재배 일지를 찾을 수 없거나 해당 식물에 대한 접근 권한이 없습니다.")
        logs = selected_log_res.data
    else:
        logs_res = db.table("care_logs").select("*").eq("plant_id", plant_id).order("created_at", desc=True).limit(5).execute()
        logs = logs_res.data or []
    
    photo_data = {}
    if state.get("photo_id"):
        photo_res = db.table("plant_photos").select("*").eq("id", state["photo_id"]).eq("plant_id", plant_id).execute()
        if photo_res.data:
            photo_data = photo_res.data[0]

    recent_photos_res = db.table("plant_photos").select("*").eq("plant_id", plant_id).order("created_at", desc=True).limit(5).execute()

    return {
        "plant_data": plant_response.data[0],
        "care_logs": logs,
        "photo_data": photo_data,
        "recent_photos": recent_photos_res.data or []
    }

def load_chat_history(state: AgentState) -> Dict[str, Any]:
    db = state["db_client"]
    user_id = state["user_id"]
    plant_id = state["plant_id"]
    garden_id = state.get("garden_id")
    context_column = "garden_id" if garden_id else "plant_id"
    context_id = garden_id or plant_id
    response_mode = state.get("response_mode") or "expert"
    prefix = chat_mode_prefix(response_mode)
    request_history = [
        {"role": item.get("role"), "content": str(item.get("content") or "")}
        for item in state.get("request_chat_history") or []
        if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
    ]

    if state.get("new_session"):
        return {"chat_history": request_history[-12:], "session_id": None}

    try:
        target_session_id = state.get("target_session_id")
        if target_session_id:
            session_res = (
                db.table("chat_sessions")
                .select("id")
                .eq("id", target_session_id)
                .eq("user_id", user_id)
                .eq(context_column, context_id)
                .limit(1)
                .execute()
            )
            if not session_res.data:
                raise ValueError("상담 세션을 찾을 수 없거나 해당 세션에 대한 권한이 없습니다.")
        else:
            try:
                session_res = (
                    db.table("chat_sessions")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq(context_column, context_id)
                    .eq("response_mode", response_mode)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
            except Exception:
                # response_mode 컬럼 미적용(마이그레이션 전) 환경: title 접두사로 폴백
                session_res = (
                    db.table("chat_sessions")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq(context_column, context_id)
                    .like("title", f"{prefix}%")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
        if not session_res.data:
            return {"chat_history": request_history[-12:], "session_id": None}

        session_id = session_res.data[0]["id"]
        messages_res = (
            db.table("chat_messages")
            .select("role,sender,content,created_at")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(12)
            .execute()
        )
        history = []
        for item in reversed(messages_res.data or []):
            content = item.get("content")
            if isinstance(content, dict):
                text = content.get("text", "")
                image_analysis = content.get("imageAnalysis")
                image_signals = content.get("imageSignals")
                if image_analysis:
                    text = f"{text}\n[이전에 첨부한 사진 분석: {image_analysis}]".strip()
                if image_signals:
                    text = f"{text}\n[이전 사진 관찰 신호: {', '.join(str(signal) for signal in image_signals)}]".strip()
            else:
                text = str(content or "")
            if text.strip():
                history.append({
                    "role": item.get("role") or item.get("sender") or "user",
                    "content": text.strip()
                })
        merged = [*history, *request_history]
        deduped = []
        seen = set()
        for item in merged:
            key = (item.get("role"), item.get("content"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return {"chat_history": deduped[-12:], "session_id": session_id}
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        logger.warning("Chat memory unavailable: %s", exc)
        return {"chat_history": request_history[-12:], "session_id": None}

# 2. extract_image_signals 노드
def extract_image_signals(state: AgentState) -> Dict[str, Any]:
    db = state["db_client"]
    photo = state.get("photo_data")
    logs = state.get("care_logs") or []
    question = state["question"].lower()
    signals = []
    image_description = ""
    vision_error: Optional[str] = None
    
    # 간단한 키워드 추출 (Fallback)
    if "노랗" in question or "황화" in question or "색이 바" in question:
        signals.append("잎 변색 (황화)")
    if "시들" in question or "말라" in question or "힘이 없" in question:
        signals.append("줄기/잎 시듦 및 탈수")
    if "반점" in question or "벌레" in question or "응애" in question:
        signals.append("병해충 의심 반점/흔적")
        
    # 이미지 설명이 있으면 추가
    if photo and photo.get("note"):
        signals.append(f"사용자 사진 메모: {photo['note']}")

    if photo and photo.get("storage_path"):
        try:
            if state.get("llm_provider"):
                analysis = analyze_plant_image(
                    db,
                    photo["storage_path"],
                    state["question"],
                    primary_model=state.get("llm_model"),
                    preferred_provider=state["llm_provider"],
                )
            else:
                analysis = analyze_plant_image(db, photo["storage_path"], state["question"])
            signals.extend(analysis.get("signals") or [])
            image_description = analysis.get("description") or ""
        except VisionAnalysisError as exc:
            vision_error = str(exc)
            logger.warning("Vision analysis skipped: %s", exc)

    for log in logs[:3]:
        for label, field in [("잎 상태", "leaf_condition"), ("흙 상태", "soil_condition"), ("재배 메모", "memo")]:
            value = log.get(field)
            if value:
                signals.append(f"최근 {label}: {value}")
        
    return {
        "image_signals": signals,
        "image_description": image_description,
        "vision_error": vision_error,
    }

# 3. summarize_user_context 노드
def summarize_user_context(state: AgentState) -> Dict[str, Any]:
    plant = state["plant_data"]
    logs = state["care_logs"]
    photo = state.get("photo_data") or {}
    
    context_parts = [
        f"식물 별명: {plant.get('name') or '알 수 없음'}",
        f"식물 품종: {plant.get('species') or '알 수 없음'}",
        f"키우는 위치: {plant.get('location') or '미지정'}",
        f"조도/햇빛: {plant.get('sunlight') or '미지정'}"
    ]

    if plant.get("context_type") == "garden":
        context_parts = [
            f"텃밭 이름: {plant.get('name') or '이름 없음'}",
            f"텃밭 위치: {plant.get('location') or '미등록'}",
            f"공통 일조 환경: {plant.get('sunlight') or '미등록'}",
            f"토양: {plant.get('soil_type') or '미등록'}",
            f"설명: {plant.get('description') or '미등록'}",
            f"재배 유형: {'단일 작물' if plant.get('cultivation_type') == 'single' else '여러 작물'}",
            f"대표 작물: {plant.get('representative_crop') or '미등록'}",
            "구성 식물: " + (", ".join(
                " / ".join(part for part in [member.get("name"), member.get("species")] if part)
                for member in plant.get("member_plants") or []
            ) or "등록된 식물 없음"),
        ]

    if plant.get("health_score") is not None:
        context_parts.append(f"앱 건강 점수: {plant.get('health_score')}")
    if plant.get("moisture"):
        context_parts.append(f"앱 수분 상태: {plant.get('moisture')}")
    if plant.get("next_task"):
        context_parts.append(f"다음 관리 작업: {plant.get('next_task')}")
    
    if logs:
        member_names = {
            str(member.get("id")): member.get("name") or member.get("species") or "이름 없는 식물"
            for member in plant.get("member_plants") or []
        }
        for index, log in enumerate(logs[:3], start=1):
            log_parts = [
                f"#{index}",
                f"물 준 날짜={log.get('watered_at') or '기록 없음'}",
                f"잎={log.get('leaf_condition') or '기록 없음'}",
                f"흙={log.get('soil_condition') or '기록 없음'}",
                f"메모={log.get('memo') or '없음'}"
            ]
            if member_names.get(str(log.get("plant_id"))):
                log_parts.insert(1, f"식물={member_names[str(log.get('plant_id'))]}")
            context_parts.append("최근 재배 일지 " + ", ".join(log_parts))

    if photo:
        context_parts.append(
            f"상담 첨부 사진: 촬영일={photo.get('captured_at') or '미지정'}, 메모={photo.get('note') or '없음'}, 저장경로={photo.get('storage_path') or '없음'}"
        )
    return {"user_context": " / ".join(context_parts)}
