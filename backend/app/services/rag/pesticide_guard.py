"""농약과 무관한 질문에 농약 문서가 답변 근거로 섞이는 것을 막는 가드.

2026-07-30 마이그레이션으로 `match_rag_chunks` RPC가 `psis_pesticide_chunks`를
UNION하면서 농약 문서가 모든 질의의 벡터 후보군에 들어온다. 그런데
- 작물 게이트(`filter_by_specific_terms`)는 작물 일치만 검사하고,
- `grade_or_rerank`의 LLM 판정 기준에는 농약 의도가 없으며 실패 시 fail-open 한다.
따라서 "물주기" 같은 질문에도 농약 문서가 근거로 남을 수 있다.

질문에 농약 의도가 없으면 농약 문서를 근거에서 제외하고, 제외 건수를 돌려줘
`safety_review`가 사용자에게 고지할 수 있게 한다. 농약 의도가 있는 질문에서는
아무 것도 하지 않는다 — 기존 `PESTICIDE_CAUTION_NOTICE` 경로를 그대로 둔다.
"""
import logging
from typing import Any, Dict, List, Tuple

from app.services.rag.common import document_safety_tags

logger = logging.getLogger(__name__)


# 질문에 농약 의도가 있다고 볼 한국어 키워드.
# 명사 위주로 좁게 잡는다. "뿌리"(root/spray 동음)나 단독 "약"처럼 오탐 위험이
# 큰 토큰은 의도적으로 제외한다 — "뿌리가 갈색이에요"는 농약 질문이 아니다.
# 부분 문자열로 매칭하므로 "살충"이 살충제/살충 효과를 모두 덮는다.
PESTICIDE_INTENT_KEYWORDS: Tuple[str, ...] = (
    "농약",
    "약제",
    "살충",
    "살균",
    "제초",
    "살비",
    "방제",
    "희석",
    "안전사용기준",
    "수확전일수",
    "수확 전 일수",
    "훈증",
    "약해",
    "등록약제",
    "등록 약제",
    "pls",
)

PESTICIDE_SAFETY_TAG = "pesticide_caution"
PESTICIDE_USAGE_SCOPE = "safety_reference_only"


def is_pesticide_question(question: str) -> bool:
    """질문에 농약/약제 사용 의도가 드러나면 True."""
    normalized = " ".join(str(question or "").strip().lower().split())
    if not normalized:
        return False
    return any(keyword in normalized for keyword in PESTICIDE_INTENT_KEYWORDS)


def is_pesticide_document(doc: Any) -> bool:
    """검색 문서가 농약 자료인지 판정한다.

    `normalize_metadata`(vectorstore.py)가 보장하는 필드만 사용한다.
    로더가 snake_case 최상위 컬럼 대신 camelCase metadata에만 값을 넣는 경우가
    있어 태그 조회는 `document_safety_tags`에 위임한다.
    """
    if not isinstance(doc, dict):
        return False

    if PESTICIDE_SAFETY_TAG in document_safety_tags(doc):
        return True

    metadata = doc.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False

    usage_scope = metadata.get("usage_scope") or metadata.get("usageScope")
    if str(usage_scope or "").strip() == PESTICIDE_USAGE_SCOPE:
        return True

    # normalize_metadata는 category를 section으로 흘려보낸다. 양쪽을 모두 본다.
    for field in ("section", "category"):
        if "pesticide" in str(metadata.get(field) or "").lower():
            return True

    return False


def partition_pesticide_docs(docs: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(농약 아닌 문서, 농약 문서) 두 리스트로 나눈다. 원 순서를 유지한다."""
    ordinary: List[Dict[str, Any]] = []
    pesticide: List[Dict[str, Any]] = []
    for doc in docs or []:
        if is_pesticide_document(doc):
            pesticide.append(doc)
        else:
            ordinary.append(doc)
    return ordinary, pesticide


def apply_pesticide_guard(question: str, docs: Any) -> Tuple[List[Dict[str, Any]], int]:
    """질문에 농약 의도가 없으면 농약 문서를 제거한다.

    Returns:
        (근거로 사용할 문서, 제외한 농약 문서 수)
    """
    doc_list = list(docs or [])
    if is_pesticide_question(question):
        return doc_list, 0

    ordinary, pesticide = partition_pesticide_docs(doc_list)
    return ordinary, len(pesticide)
