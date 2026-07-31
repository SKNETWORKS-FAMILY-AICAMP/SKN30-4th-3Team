"""식물 상담 범위 밖 질문의 검색·생성·출처 차단 회귀 테스트."""

import json
import types

import pytest

from app.services.rag import nodes_context, nodes_generation, nodes_retrieval
from app.services.rag.common import (
    QUESTION_SCOPE_OUT_OF_SCOPE,
    QUESTION_SCOPE_PLANT_CARE,
    QUESTION_SCOPE_SMALLTALK,
    QUESTION_SCOPE_UNDETERMINED,
    classify_question_scope,
)


@pytest.mark.parametrize(
    "question",
    [
        "요즘 어떤 주식에 투자하면 좋을까요?",
        "비트코인 전망과 매수 시점을 알려주세요.",
        "아이폰 배터리를 오래 쓰는 방법을 알려주세요.",
        "강아지가 초콜릿을 먹었는데 응급처치 방법은?",
        "요즘 기침이 심한데 감기약을 추천해 주세요.",
        "토마토로 파스타 소스 만드는 레시피를 알려주세요.",
    ],
)
def test_unrelated_questions_are_sent_to_semantic_scope_check(question):
    assert classify_question_scope(question) == QUESTION_SCOPE_UNDETERMINED


@pytest.mark.parametrize(
    "question",
    [
        "토마토 잎이 노랗게 변했어요.",
        "화분 흙이 계속 축축한데 물주기를 미뤄야 할까요?",
        "주식물의 잎과 뿌리 상태를 어떻게 관찰하나요?",
        "토마토 수확 시기를 알려주세요.",
        "에어컨 바람이 잎에 직접 닿아도 괜찮나요?",
    ],
)
def test_plant_care_questions_remain_in_scope(question):
    assert classify_question_scope(question) == QUESTION_SCOPE_PLANT_CARE


def test_smalltalk_has_its_own_scope():
    assert classify_question_scope("안녕하세요") == QUESTION_SCOPE_SMALLTALK
    assert classify_question_scope("내 이름 기억해?") == QUESTION_SCOPE_SMALLTALK


def test_registered_plant_name_keeps_contextual_question_in_scope():
    plant = {"name": "김고구", "species": "고구마"}
    assert (
        classify_question_scope("김고구 요즘 괜찮아?", plant_data=plant)
        == QUESTION_SCOPE_PLANT_CARE
    )
    assert (
        classify_question_scope("김고구에게 에어컨 바람이 닿아도 돼?", plant_data=plant)
        == QUESTION_SCOPE_PLANT_CARE
    )


def _scope_completion(scope):
    message = types.SimpleNamespace(content=json.dumps({"scope": scope}))
    return types.SimpleNamespace(
        response=types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message)]
        ),
        provider="openai",
        model="gpt-5.6-sol",
    )


def _out_of_scope_state(**overrides):
    state = {
        "question": "주식 투자 종목을 추천해 주세요.",
        "question_scope": QUESTION_SCOPE_OUT_OF_SCOPE,
        "plant_data": {"name": "토마토", "species": "Solanum lycopersicum"},
        "care_logs": [],
        "image_signals": [],
        "image_description": "",
        "vision_error": None,
        "user_context": "식물: 토마토",
        "chat_history": [],
        "response_mode": "expert",
        "llm_provider": "openai",
        "llm_model": "gpt-5.6-sol",
        "pesticide_docs_filtered": 0,
    }
    state.update(overrides)
    return state


def _tomato_doc():
    return {
        "content": "토마토는 토양 수분과 일조 시간을 함께 점검해야 합니다.",
        "metadata": {
            "source_id": "tomato-official",
            "title": "토마토 재배 관리 자료",
            "publisher": "공공기관",
            "url": "https://example.com/tomato",
        },
        "score": 0.9,
    }


@pytest.mark.parametrize(
    "question",
    [
        "에어컨은 누가 발명했어?",
        "프랑스 수도는 어디인가요?",
        "조선 시대 첫 번째 왕은 누구야?",
        "2 더하기 2는 얼마인가요?",
    ],
)
def test_arbitrary_unrelated_topic_is_blocked_before_search(monkeypatch, question):
    calls = []

    def classify_only(**kwargs):
        calls.append(kwargs)
        return _scope_completion(QUESTION_SCOPE_OUT_OF_SCOPE)

    monkeypatch.setattr(nodes_retrieval, "chat_completion", classify_only)
    state = _out_of_scope_state(
        question=question,
        question_scope=QUESTION_SCOPE_UNDETERMINED,
    )

    result = nodes_retrieval.build_retrieval_query(state)

    assert result == {
        "search_query": "",
        "question_scope": QUESTION_SCOPE_OUT_OF_SCOPE,
    }
    assert len(calls) == 1
    assert "현재 질문" in calls[0]["messages"][-1]["content"]


def test_scope_classifier_failure_blocks_retrieval(monkeypatch):
    monkeypatch.setattr(
        nodes_retrieval,
        "chat_completion",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("scope model unavailable")),
    )
    state = _out_of_scope_state(
        question="에어컨은 누가 발명했어?",
        question_scope=QUESTION_SCOPE_UNDETERMINED,
    )

    result = nodes_retrieval.build_retrieval_query(state)

    assert result["question_scope"] == QUESTION_SCOPE_OUT_OF_SCOPE
    assert result["search_query"] == ""


def test_out_of_scope_skips_query_expansion_and_document_search(monkeypatch):
    def fail_search(*args, **kwargs):
        pytest.fail("범위 밖 질문은 문서 검색을 호출하면 안 됩니다.")

    monkeypatch.setattr(nodes_retrieval, "search_documents", fail_search)
    state = _out_of_scope_state()

    assert nodes_retrieval.build_retrieval_query(state) == {
        "search_query": "",
        "question_scope": QUESTION_SCOPE_OUT_OF_SCOPE,
    }
    assert nodes_retrieval.retrieve_docs(state) == {
        "retrieved_docs": [],
        "pesticide_docs_filtered": 0,
    }


def test_out_of_scope_skips_image_model_even_when_photo_exists(monkeypatch):
    monkeypatch.setattr(
        nodes_context,
        "analyze_plant_image",
        lambda *args, **kwargs: pytest.fail("범위 밖 질문은 이미지 모델을 호출하면 안 됩니다."),
    )
    result = nodes_context.extract_image_signals(
        _out_of_scope_state(photo_data={"storage_path": "private/photo.jpg"})
    )

    assert result == {
        "image_signals": [],
        "image_description": "",
        "vision_error": None,
    }


def test_out_of_scope_generation_refuses_without_llm_or_citations(monkeypatch):
    monkeypatch.setattr(
        nodes_generation,
        "chat_completion",
        lambda **kwargs: pytest.fail("범위 밖 질문은 답변 LLM을 호출하면 안 됩니다."),
    )
    state = _out_of_scope_state(retrieved_docs=[_tomato_doc()])

    result = nodes_generation.generate_answer(state)
    final = nodes_generation.safety_review({**state, **result})["final_answer"]

    assert "상담 범위를 벗어나" in final["summary"]
    assert "0건" in final["summary"]
    assert final["citations"] == []
    assert final["possibleCauses"] == []
    assert "출처는 0건" in final["safetyNotice"]


def test_reranker_is_fail_closed_for_out_of_scope_question(monkeypatch):
    monkeypatch.setattr(
        nodes_retrieval,
        "chat_completion",
        lambda **kwargs: pytest.fail("범위 밖 질문은 리랭커를 호출하면 안 됩니다."),
    )
    result = nodes_retrieval.grade_or_rerank(
        _out_of_scope_state(retrieved_docs=[_tomato_doc()])
    )

    assert result["retrieved_docs"] == []


def test_safety_review_clears_any_leaked_citation_for_out_of_scope_question():
    leaked_citation = {
        "sourceId": "tomato-official",
        "title": "토마토 재배 관리 자료",
        "url": "https://example.com/tomato",
        "publisher": "공공기관",
    }
    draft = {
        "summary": "범위 밖 질문입니다.",
        "possibleCauses": [],
        "todayActions": [],
        "observationChecklist": [],
        "citations": [leaked_citation],
    }

    final = nodes_generation.safety_review(
        _out_of_scope_state(
            draft_answer=draft,
            retrieved_docs=[_tomato_doc()],
        )
    )["final_answer"]

    assert final["citations"] == []
    assert "출처는 0건" in final["safetyNotice"]


def test_explicit_model_refusal_clears_sources_even_for_unknown_topic():
    """키워드 분류가 놓친 새 주제도 모델이 범위 밖이라고 거절하면 출처를 숨긴다."""
    draft = {
        "summary": "해당 내용은 식물 관리와 관련이 없어 답변하기 어렵습니다.",
        "possibleCauses": [],
        "todayActions": [],
        "observationChecklist": [],
        "citations": [{
            "sourceId": "tomato-official",
            "title": "토마토 재배 관리 자료",
            "url": "https://example.com/tomato",
            "publisher": "공공기관",
        }],
    }
    state = _out_of_scope_state(
        question="프랑스 수도는 어디인가요?",
        question_scope=QUESTION_SCOPE_PLANT_CARE,
        draft_answer=draft,
        retrieved_docs=[_tomato_doc()],
    )

    final = nodes_generation.safety_review(state)["final_answer"]

    assert final["citations"] == []
    assert "출처는 0건" in final["safetyNotice"]
