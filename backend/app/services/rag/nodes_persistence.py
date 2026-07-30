"""10단계: 상담 세션/메시지 영속화 노드."""
import logging
import uuid
from typing import Dict, Any

from app.services.rag.common import AgentState, chat_mode_prefix, make_mode_session_title

logger = logging.getLogger(__name__)


# 9. persist_result 노드 (DB 영속화)
def persist_result(state: AgentState) -> Dict[str, Any]:
    db = state["db_client"]
    user_id = state["user_id"]
    plant_id = state["plant_id"]
    garden_id = state.get("garden_id")
    context_column = "garden_id" if garden_id else "plant_id"
    context_id = garden_id or plant_id
    final = state["final_answer"]
    question = state["question"]
    response_mode = state.get("response_mode") or "expert"
    prefix = chat_mode_prefix(response_mode)

    try:
        session_res = None
        session_id = state.get("session_id")
        if session_id and not state.get("new_session"):
            try:
                existing = db.table("chat_sessions").select("title").eq("id", session_id).eq("user_id", user_id).limit(1).execute()
                if existing.data and not existing.data[0].get("title"):
                    db.table("chat_sessions").update({"title": make_mode_session_title(state["plant_data"], question, response_mode)}).eq("id", session_id).execute()
            except Exception:
                pass
        elif not state.get("new_session"):
            try:
                session_res = (
                    db.table("chat_sessions")
                    .select("id,title")
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
                    .select("id,title")
                    .eq("user_id", user_id)
                    .eq(context_column, context_id)
                    .like("title", f"{prefix}%")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )

        if session_id:
            pass
        elif session_res and session_res.data:
            session_id = session_res.data[0]["id"]
            if not session_res.data[0].get("title"):
                try:
                    db.table("chat_sessions").update({"title": make_mode_session_title(state["plant_data"], question, response_mode)}).eq("id", session_id).execute()
                except Exception:
                    pass
        else:
            new_session = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "plant_id": plant_id,
                "title": make_mode_session_title(state["plant_data"], question, response_mode),
                "response_mode": response_mode
            }
            if garden_id:
                new_session["garden_id"] = garden_id
            try:
                db.table("chat_sessions").insert(new_session).execute()
            except Exception:
                # response_mode 컬럼 미적용(마이그레이션 전) 환경 폴백
                new_session.pop("response_mode", None)
                db.table("chat_sessions").insert(new_session).execute()
            session_id = new_session["id"]

        user_msg_id = str(uuid.uuid4())
        user_content: Dict[str, Any] = {"text": question}
        if state.get("photo_id"):
            user_content["photoId"] = state.get("photo_id")
        if state.get("image_description"):
            user_content["imageAnalysis"] = state.get("image_description")
        if state.get("image_signals"):
            user_content["imageSignals"] = state.get("image_signals")
        user_message_payload = {
            "id": user_msg_id,
            "session_id": session_id,
            "role": "user",
            "content": user_content,
            "citations": []
        }
        try:
            db.table("chat_messages").insert(user_message_payload).execute()
        except Exception:
            db.table("chat_messages").insert({
                "id": user_msg_id,
                "session_id": session_id,
                "sender": "user",
                "content": question,
                "citations": []
            }).execute()

        ai_msg_id = str(uuid.uuid4())
        content_text = f"[요약]\n{final['summary']}\n\n[의심 원인]\n" + "\n".join(final['possibleCauses']) + "\n\n[오늘 할 일]\n" + "\n".join(final['todayActions'])

        db_citations = []
        for cit in final["citations"]:
            db_citations.append({
                "source_id": cit["sourceId"],
                "title": cit["title"],
                "url": cit["url"],
                "publisher": cit["publisher"],
                "excerpt": cit.get("excerpt"),
                "section": cit.get("section")
            })

        assistant_message_payload = {
            "id": ai_msg_id,
            "session_id": session_id,
            "role": "assistant",
            "content": {"text": content_text},
            "citations": db_citations
        }
        try:
            db.table("chat_messages").insert(assistant_message_payload).execute()
        except Exception:
            db.table("chat_messages").insert({
                "id": ai_msg_id,
                "session_id": session_id,
                "sender": "assistant",
                "content": content_text,
                "citations": db_citations
            }).execute()

        return {
            "session_id": session_id,
            "message_id": ai_msg_id
        }
    except Exception as exc:
        logger.warning("Chat persistence skipped after answer generation: %s", exc)
        return {
            "session_id": None,
            "message_id": None
        }
