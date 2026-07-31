import json
import types

from app.services.rag import nodes_generation


def _doc():
    return {
        "content": "몬스테라는 흙 수분과 일조 시간을 함께 확인하고, 흙이 마른 뒤 물을 줍니다.",
        "metadata": {"source_id": "care-1", "title": "몬스테라 물관리 자료"},
    }


def _state(mode: str):
    return {
        "retrieved_docs": [_doc()],
        "question": "오늘 내 상태는 어때?",
        "user_context": "식물: 몬스테라, 마지막 물주기: 4일 전",
        "image_description": "사진 분석 결과 없음",
        "image_signals": [],
        "vision_error": None,
        "response_mode": mode,
        "plant_data": {"name": "초록이", "species": "몬스테라"},
        "care_logs": [],
        "chat_history": [],
        "llm_provider": "openai",
        "llm_model": "gpt-5.4",
    }


def _fake_completion(captured):
    def complete(**kwargs):
        captured.update(kwargs)
        content = {
            "evidenceNotes": "몬스테라 물관리 자료에서 흙 수분과 일조 시간 확인을 근거로 사용했습니다.",
            "summary": "나 초록이야! 오늘은 흙이 얼마나 말랐는지 먼저 살펴봐 줘.",
            "possibleCauses": ["흙 수분이 아직 충분할 수 있어."],
            "todayActions": ["흙이 마른 뒤에 물을 부탁해!"],
            "observationChecklist": ["오늘 받은 일조 시간을 같이 기록해 줘."],
        }
        message = types.SimpleNamespace(content=json.dumps(content, ensure_ascii=False))
        response = types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])
        return types.SimpleNamespace(response=response, provider="openai", model="gpt-5.4")
    return complete


def test_companion_prompt_requests_lively_but_safe_tone(monkeypatch):
    captured = {}
    monkeypatch.setattr(nodes_generation, "chat_completion", _fake_completion(captured))

    nodes_generation.generate_answer(_state("companion"))

    system_prompt = captured["messages"][0]["content"]
    assert "발랄하고 생기 있게" in system_prompt
    assert "지나친 아기 말투" in system_prompt
    assert "질병, 농약, 안전 관련 내용에는 이모티콘" in system_prompt


def test_companion_answer_ends_with_one_plant_emoji(monkeypatch):
    captured = {}
    monkeypatch.setattr(nodes_generation, "chat_completion", _fake_completion(captured))

    draft = nodes_generation.generate_answer(_state("companion"))["draft_answer"]
    ending = draft["observationChecklist"][-1]

    assert ending.endswith(nodes_generation.COMPANION_ENDING_EMOJIS)
    assert sum(ending.count(emoji) for emoji in nodes_generation.COMPANION_ENDING_EMOJIS) == 1


def test_companion_answer_deduplicates_model_supplied_plant_emojis():
    styled = nodes_generation._with_companion_ending({
        "summary": "오늘도 힘내자! 🌿",
        "possibleCauses": ["흙이 조금 말랐을 수 있어. 💚"],
        "todayActions": ["흙을 살펴봐 줘. 🌱"],
        "observationChecklist": ["새잎을 같이 확인해 줘. 🍃"],
        "citations": [],
    }, "초록이")
    visible_text = " ".join([
        styled["summary"],
        *styled["possibleCauses"],
        *styled["todayActions"],
        *styled["observationChecklist"],
    ])

    assert sum(visible_text.count(emoji) for emoji in nodes_generation.COMPANION_ENDING_EMOJIS) == 1
    assert styled["observationChecklist"][-1].endswith(nodes_generation.COMPANION_ENDING_EMOJIS)


def test_expert_answer_does_not_force_companion_emoji(monkeypatch):
    captured = {}
    monkeypatch.setattr(nodes_generation, "chat_completion", _fake_completion(captured))

    draft = nodes_generation.generate_answer(_state("expert"))["draft_answer"]
    visible_text = " ".join([draft["summary"], *draft["possibleCauses"], *draft["todayActions"], *draft["observationChecklist"]])

    assert not any(emoji in visible_text for emoji in nodes_generation.COMPANION_ENDING_EMOJIS)
