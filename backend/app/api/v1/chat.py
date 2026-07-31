import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, status, Depends, HTTPException, Path, Query
from fastapi.responses import Response, StreamingResponse
from supabase import Client
from app.auth.security import get_current_user
from app.core.config import settings
from app.core.ratelimit import rate_limit_chat
from app.db.session import get_supabase_client
from app.schemas.chat import (
    PlantCareChatRequest,
    PlantCareChatResponse,
    Citation,
    ChatSession,
    ChatMessage,
    ChatModelInfo,
    ChatFeedbackRequest,
    ChatFeedbackResponse,
    ChatFeedbackItem,
    SessionFeedbackStats,
)
from app.services.rag.pipeline import chat_mode_prefix, run_rag_workflow, run_rag_workflow_stream
from app.services.llm import SELECTABLE_OPENAI_CHAT_MODELS, get_llm_runtime_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Plant Care RAG Chat"])


def fallback_session_title(db: Client, plant_id: str | None, created_at: str, garden_id: str | None = None) -> str:
    if plant_id:
        try:
            plant = db.table("plants").select("name,species").eq("id", plant_id).limit(1).execute()
            if plant.data:
                label = plant.data[0].get("name") or plant.data[0].get("species")
                if label:
                    return f"{label} 상담"
        except Exception:
            pass
    if garden_id:
        try:
            garden = db.table("gardens").select("name").eq("id", garden_id).limit(1).execute()
            if garden.data and garden.data[0].get("name"):
                return f"{garden.data[0]['name']} 텃밭 상담"
        except Exception:
            pass
    try:
        return f"상담 {datetime.fromisoformat(created_at).strftime('%m/%d %H:%M')}"
    except Exception:
        return "식물 상담"

@router.get("/model-info", response_model=ChatModelInfo, summary="식물 상담 AI 모델 정보 조회")
def get_chat_model_info():
    runtime = get_llm_runtime_status(probe_local=True)
    return ChatModelInfo(
        chatModel=os.getenv("CHAT_MODEL") or settings.CHAT_MODEL,
        visionModel=os.getenv("VISION_MODEL") or settings.VISION_MODEL,
        fallbackEnabled=runtime["fallbackEnabled"],
        localChatModel=runtime["localChatModel"],
        localVisionModel=runtime["localVisionModel"],
        localAuxiliaryEnabled=runtime["localAuxiliaryEnabled"],
        primaryCircuit=runtime["primaryCircuit"],
        primaryConfigured=runtime["primaryConfigured"],
        primaryAvailable=runtime["primaryAvailable"],
        localAvailable=runtime["localAvailable"],
        availableOpenAIModels=list(SELECTABLE_OPENAI_CHAT_MODELS),
    )

@router.post("/plant-care", response_model=PlantCareChatResponse, status_code=status.HTTP_200_OK, summary="식물 케어 RAG 상담 실행")
def consult_plant_care(
    request: PlantCareChatRequest,
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
    _rate_limit: None = Depends(rate_limit_chat)
):
    """
    제공된 식물 ID, 최근 재배 일지, 업로드 사진을 기반으로 공공 원예 문서 RAG 모델을 구동하여 상태 진단 및 처방 가이드를 반환합니다.
    """
    try:
        final_answer = run_rag_workflow(
            db_client=db,
            user_id=str(current_user_id),
            plant_id=str(request.plantId) if request.plantId else None,
            garden_id=str(request.gardenId) if request.gardenId else None,
            care_log_id=str(request.careLogId) if request.careLogId else None,
            photo_id=str(request.photoId) if request.photoId else None,
            question=request.question,
            new_session=request.newSession,
            response_mode=request.responseMode,
            recent_messages=[message.model_dump() for message in request.recentMessages],
            session_id=str(request.sessionId) if request.sessionId else None,
            llm_provider=request.llmProvider,
            llm_model=request.llmModel,
        )
        
        citations = []
        for cit in final_answer["citations"]:
            citations.append(Citation(
                sourceId=cit["sourceId"],
                title=cit["title"],
                url=cit.get("url"),
                publisher=cit.get("publisher"),
                excerpt=cit.get("excerpt"),
                section=cit.get("section")
            ))
            
        return PlantCareChatResponse(
            summary=final_answer["summary"],
            possibleCauses=final_answer["possibleCauses"],
            todayActions=final_answer["todayActions"],
            observationChecklist=final_answer["observationChecklist"],
            citations=citations,
            safetyNotice=final_answer.get("safetyNotice"),
            sessionId=uuid.UUID(final_answer["sessionId"]) if final_answer.get("sessionId") else None,
            messageId=uuid.UUID(final_answer["messageId"]) if final_answer.get("messageId") else None,
            llmProvider=final_answer.get("llmProvider"),
            llmModel=final_answer.get("llmModel"),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception:
        logger.exception("API 처리 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="식물 상담 RAG 수행 중 오류가 발생했습니다."
        )

@router.post("/plant-care/stream", summary="식물 케어 RAG 상담 실행 (SSE 진행 스트리밍)")
def consult_plant_care_stream(
    request: PlantCareChatRequest,
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
    _rate_limit: None = Depends(rate_limit_chat)
):
    """
    RAG 파이프라인을 실행하며 노드별 진행 상황을 Server-Sent Events로 스트리밍합니다.
    이벤트: progress(단계 안내) → result(최종 답변) / error(오류 안내)
    """
    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    def event_source():
        try:
            for event in run_rag_workflow_stream(
                db_client=db,
                user_id=str(current_user_id),
                plant_id=str(request.plantId) if request.plantId else None,
                garden_id=str(request.gardenId) if request.gardenId else None,
                care_log_id=str(request.careLogId) if request.careLogId else None,
                photo_id=str(request.photoId) if request.photoId else None,
                question=request.question,
                new_session=request.newSession,
                response_mode=request.responseMode,
                recent_messages=[message.model_dump() for message in request.recentMessages],
                session_id=str(request.sessionId) if request.sessionId else None,
                llm_provider=request.llmProvider,
                llm_model=request.llmModel,
            ):
                yield sse(event)
        except ValueError as e:
            # 파이프라인의 사용자 안내용 검증 오류 (예: 식물 없음)
            yield sse({"type": "error", "status": 404, "detail": str(e)})
        except Exception:
            logger.exception("SSE 상담 스트리밍 중 오류 발생")
            yield sse({"type": "error", "status": 500, "detail": "식물 상담 RAG 수행 중 오류가 발생했습니다."})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@router.get("/sessions", response_model=List[ChatSession], summary="상담 세션 목록 조회")
def list_chat_sessions(
    gardenId: uuid.UUID | None = Query(None),
    plantId: uuid.UUID | None = Query(None, description="특정 식물의 상담 세션만 조회"),
    responseMode: str | None = Query(None, pattern="^(expert|companion)$", description="상담 모드별 세션 필터"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    try:
        def build_query(use_mode_column: bool):
            query = db.table("chat_sessions").select("*").eq("user_id", str(current_user_id))
            if plantId:
                query = query.eq("plant_id", str(plantId))
            if gardenId:
                query = query.eq("garden_id", str(gardenId))
            if responseMode:
                if use_mode_column:
                    query = query.eq("response_mode", responseMode)
                else:
                    query = query.like("title", f"{chat_mode_prefix(responseMode)}%")
            return query.order("created_at", desc=True)

        try:
            response = build_query(use_mode_column=True).execute()
        except Exception:
            # response_mode 컬럼 미적용(마이그레이션 전) 환경: title 접두사로 폴백
            response = build_query(use_mode_column=False).execute()
        sessions = []
        for item in response.data:
            sessions.append(ChatSession(
                id=uuid.UUID(item["id"]),
                userId=uuid.UUID(item["user_id"]),
                plantId=uuid.UUID(item["plant_id"]) if item.get("plant_id") else None,
                gardenId=uuid.UUID(item["garden_id"]) if item.get("garden_id") else None,
                title=item.get("title") or fallback_session_title(db, item.get("plant_id"), item.get("created_at", ""), item.get("garden_id")),
                createdAt=datetime.fromisoformat(item["created_at"])
            ))
        return sessions
    except Exception:
        logger.exception("API 처리 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="상담 세션 목록 조회 중 오류가 발생했습니다."
        )


@router.delete("/sessions/{sessionId}", status_code=status.HTTP_204_NO_CONTENT, summary="상담 세션 삭제")
def delete_chat_session(
    sessionId: uuid.UUID = Path(..., description="삭제할 상담 세션 UUID"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    """현재 사용자가 소유한 상담방과 연관 메시지·피드백을 삭제합니다."""
    try:
        session_check = (
            db.table("chat_sessions")
            .select("id")
            .eq("id", str(sessionId))
            .eq("user_id", str(current_user_id))
            .limit(1)
            .execute()
        )
        if not session_check.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="상담방을 찾을 수 없거나 삭제할 권한이 없습니다."
            )

        # chat_messages와 chat_feedback은 DB 외래 키의 ON DELETE CASCADE로 함께 정리됩니다.
        db.table("chat_sessions").delete().eq("id", str(sessionId)).eq("user_id", str(current_user_id)).execute()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except Exception:
        logger.exception("상담 세션 삭제 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="상담방을 삭제하는 중 오류가 발생했습니다."
        )

@router.post("/messages/{messageId}/feedback", response_model=ChatFeedbackResponse, status_code=status.HTTP_200_OK, summary="AI 답변 피드백 저장")
def save_chat_feedback(
    feedback: ChatFeedbackRequest,
    messageId: uuid.UUID = Path(..., description="피드백을 남길 assistant 메시지 UUID"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    try:
        try:
            message_res = (
                db.table("chat_messages")
                .select("id,session_id,role")
                .eq("id", str(messageId))
                .limit(1)
                .execute()
            )
        except Exception:
            # Older local schemas briefly used sender instead of role.
            message_res = (
                db.table("chat_messages")
                .select("id,session_id,sender")
                .eq("id", str(messageId))
                .limit(1)
                .execute()
            )
        if not message_res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="피드백을 남길 답변을 찾을 수 없습니다."
            )

        message = message_res.data[0]
        sender = message.get("role") or message.get("sender")
        if sender != "assistant":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI 답변 메시지에만 피드백을 남길 수 있습니다."
            )

        session_res = (
            db.table("chat_sessions")
            .select("user_id")
            .eq("id", message["session_id"])
            .limit(1)
            .execute()
        )
        if not session_res.data or session_res.data[0].get("user_id") != str(current_user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="피드백을 남길 답변을 찾을 수 없습니다."
            )

        payload = {
            "message_id": str(messageId),
            "user_id": str(current_user_id),
            "rating": feedback.rating,
            "comment": feedback.comment,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        db.table("chat_feedback").upsert(payload, on_conflict="message_id,user_id").execute()
        return ChatFeedbackResponse(messageId=messageId, rating=feedback.rating)
    except HTTPException:
        raise
    except Exception:
        logger.exception("AI 답변 피드백 저장 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI 답변 피드백 저장 중 오류가 발생했습니다."
        )


@router.get("/sessions/{sessionId}/feedback", response_model=List[ChatFeedbackItem], summary="세션 내 내 피드백 조회")
def list_session_feedback(
    sessionId: uuid.UUID = Path(..., description="세션 UUID"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    """
    특정 상담 세션에서 현재 사용자가 남긴 답변 피드백 목록을 반환합니다.
    대화 이력을 다시 열 때 이미 평가한 답변의 버튼 상태를 복원하는 데 사용합니다.
    """
    try:
        session_check = db.table("chat_sessions").select("user_id").eq("id", str(sessionId)).execute()
        if not session_check.data or session_check.data[0]["user_id"] != str(current_user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="상담 세션을 찾을 수 없거나 해당 세션에 대한 권한이 없습니다."
            )

        messages_res = db.table("chat_messages").select("id").eq("session_id", str(sessionId)).execute()
        message_ids = [item["id"] for item in messages_res.data or []]
        if not message_ids:
            return []

        feedback_res = (
            db.table("chat_feedback")
            .select("message_id,rating,comment")
            .eq("user_id", str(current_user_id))
            .in_("message_id", message_ids)
            .execute()
        )
        return [
            ChatFeedbackItem(
                messageId=uuid.UUID(item["message_id"]),
                rating=item["rating"],
                comment=item.get("comment")
            )
            for item in feedback_res.data or []
        ]
    except HTTPException:
        raise
    except Exception:
        logger.exception("세션 피드백 조회 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="세션 피드백 조회 중 오류가 발생했습니다."
        )


@router.get("/feedback/summary", response_model=List[SessionFeedbackStats], summary="세션별 피드백 통계")
def get_feedback_summary(
    plantId: uuid.UUID | None = Query(None, description="특정 식물의 세션만 집계"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    """
    현재 사용자가 남긴 답변 피드백을 상담 세션 단위로 집계해 반환합니다.
    (helpful/not_helpful/unsafe/irrelevant 별 개수, 총합 내림차순)
    """
    try:
        feedback_res = (
            db.table("chat_feedback")
            .select("message_id,rating")
            .eq("user_id", str(current_user_id))
            .execute()
        )
        feedback_rows = feedback_res.data or []
        if not feedback_rows:
            return []

        message_ids = list({item["message_id"] for item in feedback_rows})
        messages_res = (
            db.table("chat_messages")
            .select("id,session_id")
            .in_("id", message_ids)
            .execute()
        )
        session_of_message = {item["id"]: item["session_id"] for item in messages_res.data or []}

        session_ids = list({sid for sid in session_of_message.values() if sid})
        if not session_ids:
            return []
        sessions_query = (
            db.table("chat_sessions")
            .select("id,title,plant_id")
            .eq("user_id", str(current_user_id))
            .in_("id", session_ids)
        )
        if plantId:
            sessions_query = sessions_query.eq("plant_id", str(plantId))
        sessions_res = sessions_query.execute()
        session_titles = {item["id"]: item.get("title") for item in sessions_res.data or []}

        rating_field = {
            "helpful": "helpful",
            "not_helpful": "notHelpful",
            "unsafe": "unsafe",
            "irrelevant": "irrelevant",
        }
        stats: dict[str, dict] = {}
        for item in feedback_rows:
            session_id = session_of_message.get(item["message_id"])
            # plantId 필터에 걸러졌거나 세션이 삭제된 피드백은 제외
            if not session_id or session_id not in session_titles:
                continue
            entry = stats.setdefault(session_id, {"helpful": 0, "notHelpful": 0, "unsafe": 0, "irrelevant": 0, "total": 0})
            field = rating_field.get(item["rating"])
            if field:
                entry[field] += 1
            entry["total"] += 1

        results = [
            SessionFeedbackStats(
                sessionId=uuid.UUID(session_id),
                title=session_titles.get(session_id),
                **counts
            )
            for session_id, counts in stats.items()
        ]
        results.sort(key=lambda item: item.total, reverse=True)
        return results
    except HTTPException:
        raise
    except Exception:
        logger.exception("피드백 통계 조회 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="피드백 통계 조회 중 오류가 발생했습니다."
        )


@router.get("/sessions/{sessionId}/messages", response_model=List[ChatMessage], summary="세션별 대화 메시지 이력 조회")
def list_chat_messages(
    sessionId: uuid.UUID = Path(..., description="세션 UUID"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    try:
        session_check = db.table("chat_sessions").select("user_id").eq("id", str(sessionId)).execute()
        if not session_check.data or session_check.data[0]["user_id"] != str(current_user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="상담 세션을 찾을 수 없거나 해당 세션에 대한 권한이 없습니다."
            )
            
        response = db.table("chat_messages").select("*").eq("session_id", str(sessionId)).order("created_at", desc=False).execute()
        messages = []
        for item in response.data:
            citations_data = item.get("citations") or []
            citations = []
            for cit in citations_data:
                citations.append(Citation(
                    sourceId=cit.get("sourceId") or cit.get("source_id"),
                    title=cit.get("title"),
                    url=cit.get("url"),
                    publisher=cit.get("publisher"),
                    excerpt=cit.get("excerpt"),
                    section=cit.get("section")
                ))
                
            db_content = item.get("content")
            content_text = ""
            if isinstance(db_content, dict):
                content_text = db_content.get("text", "")
            else:
                content_text = str(db_content) if db_content is not None else ""

            messages.append(ChatMessage(
                id=uuid.UUID(item["id"]),
                sessionId=uuid.UUID(item["session_id"]),
                sender=item.get("role") or item.get("sender") or "user",
                content=content_text,
                citations=citations,
                createdAt=datetime.fromisoformat(item["created_at"])
            ))
        return messages
    except HTTPException:
        raise
    except Exception:
        logger.exception("API 처리 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="대화 메시지 조회 중 오류가 발생했습니다."
        )
