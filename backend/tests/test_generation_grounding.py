import json
import types

import pytest

from app.services.rag import nodes_generation


def _completion(content: dict):
    message = types.SimpleNamespace(content=json.dumps(content, ensure_ascii=False))
    return types.SimpleNamespace(
        response=types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)]),
        provider="openai",
        model="gpt-5.4",
    )


def _state(docs):
    return {
        "retrieved_docs": docs,
        "question": "토마토 아래 잎이 노랗게 변했습니다. 무엇부터 확인해야 하나요?",
        "user_context": "식물: 토마토, 마지막 물주기: 5일 전",
        "image_description": "사진 분석 결과 없음",
        "image_signals": [],
        "vision_error": None,
        "response_mode": "expert",
        "plant_data": {"name": "토마토", "species": "토마토"},
        "care_logs": [],
        "chat_history": [],
        "llm_provider": "openai",
        "llm_model": "gpt-5.4",
    }


def _doc():
    return {
        "content": "토마토 잎 황화는 토양 수분과 뿌리 활력, 일조 시간을 함께 점검해야 합니다.",
        "metadata": {
            "source_id": "official-1",
            "title": "토마토 잎 황화 관리 자료",
            "publisher": "공공기관",
            "url": "https://example.com/official-1",
        },
    }


def test_no_documents_skips_llm_and_explicitly_reports_zero_evidence(monkeypatch):
    monkeypatch.setattr(
        nodes_generation,
        "chat_completion",
        lambda **kwargs: pytest.fail("검색 문서가 없으면 LLM을 호출하면 안 됩니다."),
    )

    result = nodes_generation.generate_answer(_state([]))
    final = nodes_generation.safety_review({**_state([]), **result})["final_answer"]

    assert result["draft_answer"]["citations"] == []
    assert "0건" in final["summary"]
    assert "0건" in final["safetyNotice"]
    assert "추가" in final["todayActions"][0]


def test_grounded_model_answer_is_kept(monkeypatch):
    monkeypatch.setattr(
        nodes_generation,
        "chat_completion",
        lambda **kwargs: _completion({
            "evidenceNotes": "토마토 잎 황화 관리 자료에서 토양 수분과 뿌리 활력 점검을 확인했습니다.",
            "summary": "토마토 잎 황화는 토양 수분과 뿌리 활력을 먼저 확인해야 합니다.",
            "possibleCauses": ["토양 수분 불균형 가능성"],
            "todayActions": ["흙 수분과 뿌리 상태를 확인합니다."],
            "observationChecklist": ["일조 시간과 황화 범위를 기록합니다."],
        }),
    )

    result = nodes_generation.generate_answer(_state([_doc()]))

    assert result["llm_provider_used"] == "openai"
    assert result["llm_model_used"] == "gpt-5.4"
    assert result["draft_answer"]["summary"].startswith("토마토 잎 황화는")
    assert len(result["draft_answer"]["citations"]) == 1


def test_ungrounded_model_answer_is_replaced_with_document_evidence(monkeypatch):
    monkeypatch.setattr(
        nodes_generation,
        "chat_completion",
        lambda **kwargs: _completion({
            "evidenceNotes": "정보가 부족합니다.",
            "summary": "현재 정보가 부족하므로 추가 정보를 알려주세요.",
            "possibleCauses": ["정보 부족"],
            "todayActions": ["사진을 추가합니다."],
            "observationChecklist": ["변화를 관찰합니다."],
        }),
    )

    state = _state([_doc()])
    result = nodes_generation.generate_answer(state)
    final = nodes_generation.safety_review({**state, **result})["final_answer"]

    assert "토마토 잎 황화 관리 자료" in final["summary"]
    assert "토양 수분" in final["summary"]
    assert "검색 문서의 구체적인 근거" in final["safetyNotice"]
    assert "llm_provider_used" not in result
    assert len(final["citations"]) == 1
