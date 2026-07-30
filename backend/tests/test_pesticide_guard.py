"""농약 무관 질문에 대한 농약 문서 가드 검증.

질문에 농약 사용 의도가 없으면 검색된 농약 자료를 답변 근거에서 제외해야 한다.
농약 의도가 있는 질문에서는 기존 동작(농약 근거 사용 + 안전 고지)을 유지한다.
"""
import pytest

from app.services.rag import nodes_retrieval
from app.services.rag.pesticide_guard import (
    apply_pesticide_guard,
    is_pesticide_document,
    is_pesticide_question,
    partition_pesticide_docs,
)
from app.services.rag.vectorstore import SearchResult


def pesticide_doc(title="토마토 응애 등록 농약", key="safety_tags") -> dict:
    return {
        "content": "제품명: ...",
        "metadata": {"source_id": "psis", "title": title, key: ["not_diagnosis", "pesticide_caution"]},
    }


def ordinary_doc(title="몬스테라 물주기") -> dict:
    return {
        "content": "흙이 마르면 충분히 관수합니다.",
        "metadata": {"source_id": "nongsaro", "title": title, "safety_tags": ["not_diagnosis"]},
    }


# ---------------------------------------------------------------------------
# is_pesticide_question
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question", [
    "토마토 잎에 응애가 보이는데 어떤 농약을 써야 하나요",
    "살충제 추천해주세요",
    "살균제를 뿌려도 될까요",
    "제초제 사용 시기가 궁금해요",
    "역병 방제 방법을 알려주세요",
    "희석 배수는 어떻게 되나요",
    "안전사용기준이 뭔가요",
    "약제를 써야 할까요",
])
def test_pesticide_intent_detected(question):
    assert is_pesticide_question(question) is True


@pytest.mark.parametrize("question", [
    "몬스테라 물주기는 얼마나 자주 해야 하나요",
    "잎이 노랗게 변해요",
    "분갈이는 언제 하나요",
    "빛은 얼마나 필요한가요",
    "딸기 잎에 흰가루 같은 것이 생겼어요",
    # "뿌리"가 "뿌리다(살포)"로 오탐되면 안 된다 — 실제로는 root 이야기다.
    "뿌리가 갈색으로 변했어요",
    "뿌리 썩음이 의심돼요",
])
def test_non_pesticide_question_not_flagged(question):
    assert is_pesticide_question(question) is False


def test_empty_question_is_not_pesticide_intent():
    assert is_pesticide_question("") is False
    assert is_pesticide_question(None) is False


# ---------------------------------------------------------------------------
# is_pesticide_document
# ---------------------------------------------------------------------------
def test_document_flagged_by_snake_case_safety_tags():
    assert is_pesticide_document(pesticide_doc()) is True


def test_document_flagged_by_camelcase_safety_tags():
    assert is_pesticide_document(pesticide_doc(key="safetyTags")) is True


def test_document_flagged_by_usage_scope():
    doc = {"content": "본문", "metadata": {"title": "PSIS", "usage_scope": "safety_reference_only"}}
    assert is_pesticide_document(doc) is True
    camel = {"content": "본문", "metadata": {"title": "PSIS", "usageScope": "safety_reference_only"}}
    assert is_pesticide_document(camel) is True


def test_document_flagged_by_category_or_section():
    assert is_pesticide_document({"metadata": {"section": "pesticide_safety"}}) is True
    assert is_pesticide_document({"metadata": {"category": "pesticide_safety"}}) is True


def test_ordinary_document_not_flagged():
    assert is_pesticide_document(ordinary_doc()) is False
    assert is_pesticide_document({"metadata": {}}) is False
    assert is_pesticide_document(None) is False
    assert is_pesticide_document("문자열") is False


# ---------------------------------------------------------------------------
# partition / apply
# ---------------------------------------------------------------------------
def test_partition_keeps_original_order():
    docs = [ordinary_doc("A"), pesticide_doc("B"), ordinary_doc("C"), pesticide_doc("D")]
    ordinary, pesticide = partition_pesticide_docs(docs)
    assert [d["metadata"]["title"] for d in ordinary] == ["A", "C"]
    assert [d["metadata"]["title"] for d in pesticide] == ["B", "D"]


def test_guard_is_noop_for_pesticide_question():
    docs = [ordinary_doc(), pesticide_doc()]
    kept, excluded = apply_pesticide_guard("어떤 농약을 써야 하나요", docs)
    assert kept == docs
    assert excluded == 0


def test_guard_drops_pesticide_docs_for_unrelated_question():
    docs = [ordinary_doc(), pesticide_doc(), ordinary_doc("빛 관리")]
    kept, excluded = apply_pesticide_guard("몬스테라 물주기는 얼마나 자주 해야 하나요", docs)
    assert [d["metadata"]["title"] for d in kept] == ["몬스테라 물주기", "빛 관리"]
    assert excluded == 1


def test_guard_can_empty_the_document_list():
    kept, excluded = apply_pesticide_guard("잎이 노랗게 변해요", [pesticide_doc(), pesticide_doc("B")])
    assert kept == []
    assert excluded == 2


def test_guard_tolerates_empty_input():
    assert apply_pesticide_guard("물주기", []) == ([], 0)
    assert apply_pesticide_guard("물주기", None) == ([], 0)


# ---------------------------------------------------------------------------
# grade_or_rerank 통합 — LLM fail-open 경로에서도 가드가 걸려야 한다
# ---------------------------------------------------------------------------
def _disable_openai(monkeypatch):
    """환경변수와 settings 양쪽을 비워 grade_or_rerank의 키없음 경로를 태운다."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(nodes_retrieval.settings, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(nodes_retrieval.settings, "LLM_FALLBACK_ENABLED", False, raising=False)
    monkeypatch.setattr(nodes_retrieval.settings, "LOCAL_LLM_AUXILIARY_ENABLED", False, raising=False)


def test_retrieve_docs_refills_with_ordinary_official_docs(monkeypatch):
    """농약 후보가 상위 8건을 차지해도 뒤의 일반 공식 문서를 잃지 않는다."""
    captured = {}
    pesticide_results = [
        SearchResult(
            f"농약 자료 {index}",
            pesticide_doc(f"농약 문서 {index}")["metadata"],
            1.0 - index * 0.01,
        )
        for index in range(8)
    ]
    ordinary_results = [
        SearchResult(
            "감자 생육 단계별 물관리 방법",
            ordinary_doc("농사로 감자 물관리")["metadata"],
            0.70,
        ),
        SearchResult(
            "감자 잎 황화 시 토양 수분 점검 방법",
            ordinary_doc("농사로 감자 생육장해")["metadata"],
            0.69,
        ),
    ]

    def fake_search(query, top_k=8, target_crop_terms=None):
        captured["top_k"] = top_k
        return pesticide_results + ordinary_results

    monkeypatch.setattr(nodes_retrieval, "search_documents", fake_search)

    result = nodes_retrieval.retrieve_docs({
        "question": "감자 잎이 노랗게 변했어요",
        "plant_data": {"name": "감자", "species": "Solanum tuberosum"},
        "image_signals": ["잎 황화"],
        "image_description": "",
        "search_query": "감자 잎 황화 원인",
    })

    assert captured["top_k"] == nodes_retrieval.CANDIDATE_POOL_SIZE
    assert [doc["metadata"]["title"] for doc in result["retrieved_docs"]] == [
        "농사로 감자 물관리",
        "농사로 감자 생육장해",
    ]
    assert result["pesticide_docs_filtered"] == 8


def test_grade_or_rerank_preserves_pre_rerank_filtered_count(monkeypatch):
    _disable_openai(monkeypatch)
    result = nodes_retrieval.grade_or_rerank({
        "retrieved_docs": [ordinary_doc("농사로 감자 생육장해")],
        "pesticide_docs_filtered": 8,
        "question": "감자 잎이 노랗게 변했어요",
        "plant_data": {"name": "감자", "species": "Solanum tuberosum"},
    })

    assert [doc["metadata"]["title"] for doc in result["retrieved_docs"]] == [
        "농사로 감자 생육장해"
    ]
    assert result["pesticide_docs_filtered"] == 8


def test_grade_or_rerank_applies_guard_on_no_key_path(monkeypatch):
    _disable_openai(monkeypatch)
    result = nodes_retrieval.grade_or_rerank({
        "retrieved_docs": [ordinary_doc(), pesticide_doc()],
        "question": "몬스테라 물주기는 얼마나 자주 해야 하나요",
        "plant_data": {"name": "몬스테라", "species": "Monstera deliciosa"},
    })
    assert [d["metadata"]["title"] for d in result["retrieved_docs"]] == ["몬스테라 물주기"]
    assert result["pesticide_docs_filtered"] == 1


def test_grade_or_rerank_keeps_pesticide_docs_for_pesticide_question(monkeypatch):
    _disable_openai(monkeypatch)
    result = nodes_retrieval.grade_or_rerank({
        "retrieved_docs": [ordinary_doc(), pesticide_doc()],
        "question": "몬스테라에 응애가 보이는데 약제를 써야 할까요",
        "plant_data": {"name": "몬스테라", "species": "Monstera deliciosa"},
    })
    assert len(result["retrieved_docs"]) == 2
    assert result["pesticide_docs_filtered"] == 0


def test_grade_or_rerank_applies_guard_when_reranker_raises(monkeypatch):
    """LLM 리랭커가 예외로 docs[:4] fail-open 해도 농약 문서는 새지 않는다."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr("openai.OpenAI", _Boom)

    result = nodes_retrieval.grade_or_rerank({
        "retrieved_docs": [pesticide_doc(), ordinary_doc()],
        "question": "잎이 노랗게 변해요",
        "plant_data": {"name": "몬스테라"},
    })
    assert [d["metadata"]["title"] for d in result["retrieved_docs"]] == ["몬스테라 물주기"]
    assert result["pesticide_docs_filtered"] == 1
