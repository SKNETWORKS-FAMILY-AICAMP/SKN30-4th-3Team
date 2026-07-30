"""
Plant image analysis helpers for multimodal RAG.

The vision step converts an uploaded plant photo into objective observation text.
It must not make a final disease diagnosis or pesticide recommendation.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from supabase import Client

from app.core.config import settings
from app.db.session import get_supabase_service_client
from app.services.llm import chat_completion
from app.services.llm.schemas import VisionObservation, parse_json_object

logger = logging.getLogger(__name__)

SIGNED_URL_EXPIRES_IN = 600

VISION_SYSTEM_PROMPT = (
    "당신은 식물 상태를 관찰하는 원예 보조 분석가입니다. "
    "제공된 식물 사진을 보고 눈에 보이는 이상 징후만 객관적으로 기술하세요.\n"
    "규칙:\n"
    "- 질병명이나 병해충 종을 확정하지 마세요. '~가능성', '~로 보이는 증상', '~가 관찰됨' 수준으로만 표현합니다.\n"
    "- 농약/약제 처방을 하지 마세요.\n"
    "- 사진만으로 식물 종류, 증상의 원인, 흔히 발생하는 식물군을 추정하지 마세요.\n"
    "- description에는 색, 형태, 위치처럼 사진에서 직접 확인되는 사실만 쓰고 원인이나 관리법을 넣지 마세요.\n"
    "- 사진에 식물이 없거나 너무 흐릿하면 severity를 '판독불가'로 두세요.\n"
    "- 반드시 JSON 객체만 출력하세요.\n\n"
    "출력 JSON 스키마:\n"
    "{\n"
    '  "observedSymptoms": ["관찰된 증상 1", "관찰된 증상 2"],\n'
    '  "affectedParts": ["잎", "줄기", "흙 표면"],\n'
    '  "severity": "경미 | 보통 | 심각 | 판독불가",\n'
    '  "description": "사진 속 식물 상태에 대한 2~3문장 한국어 서술"\n'
    "}"
)


class VisionAnalysisError(RuntimeError):
    """Raised when the optional photo analysis step cannot complete."""


def _extract_signed_url(response: Any) -> str | None:
    if isinstance(response, dict):
        return response.get("signedURL") or response.get("signedUrl") or response.get("signed_url")
    return getattr(response, "signed_url", None) or getattr(response, "signedURL", None) or getattr(response, "signedUrl", None)


def create_signed_image_url(db: Client, storage_path: str) -> str:
    """Create a short-lived read URL for a Supabase Storage object."""
    bucket = settings.SUPABASE_STORAGE_BUCKET
    last_error: Exception | None = None

    clients = [db]
    if settings.SUPABASE_SERVICE_ROLE_KEY:
        try:
            clients.append(get_supabase_service_client())
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    for client in clients:
        try:
            response = client.storage.from_(bucket).create_signed_url(storage_path, SIGNED_URL_EXPIRES_IN)
            signed_url = _extract_signed_url(response)
            if signed_url:
                return signed_url
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    raise VisionAnalysisError("사진 접근용 signed URL 발급에 실패했습니다.") from last_error


def analyze_plant_image(
    db: Client,
    storage_path: str,
    question: str,
    *,
    primary_model: str | None = None,
    preferred_provider: Literal["openai", "local"] = "openai",
) -> dict[str, Any]:
    """
    Analyze an uploaded plant image and return observation signals for RAG.

    Returns:
        signals, description, affectedParts, severity
    """
    image_url = create_signed_image_url(db, storage_path)

    try:
        completion = chat_completion(
            primary_model=primary_model or settings.VISION_MODEL,
            local_model=settings.LOCAL_VISION_MODEL,
            temperature=0.1,
            response_format={"type": "json_object"},
            primary_timeout=18.0,
            max_tokens=700,
            preferred_provider=preferred_provider,
            messages=[
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"사용자 질문: {question}\n사진에서 관찰되는 식물 상태를 JSON으로 정리해 주세요.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": "low"},
                        },
                    ],
                },
            ],
        )
        parsed = VisionObservation.model_validate(
            parse_json_object(str(completion.response.choices[0].message.content or ""))
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision image analysis failed", exc_info=True)
        raise VisionAnalysisError(f"멀티모달 사진 분석에 실패했습니다: {exc}") from exc

    symptoms = parsed.observedSymptoms
    affected_parts = parsed.affectedParts
    severity = parsed.severity
    description = parsed.description

    signals = [str(item) for item in symptoms if str(item).strip()]
    if affected_parts:
        signals.append("영향 부위: " + ", ".join(str(item) for item in affected_parts))
    if severity:
        signals.append(f"사진상 심각도: {severity}")

    return {
        "signals": signals,
        "description": description,
        "affectedParts": affected_parts,
        "severity": severity,
    }
