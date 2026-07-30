from app.services.rag import nodes_generation


def fallback_state(question: str) -> dict:
    return {
        "question": question,
        "retrieved_docs": [
            {
                "content": "폭넓은 식물 관리 문서: 과습, 물주기, 건조, 해충을 모두 다룹니다.",
                "metadata": {
                    "source_id": "test-source",
                    "title": "식물 관리 안내",
                    "publisher": "테스트 기관",
                    "url": "https://example.com/plant-care",
                },
            }
        ],
        "user_context": "기본 식물 기록",
        "image_description": "",
        "image_signals": [],
        "response_mode": "expert",
        "plant_data": {"name": "테스트 식물", "species": "Test species"},
        "care_logs": [],
        "chat_history": [],
    }


def test_fallback_uses_current_question_not_all_document_keywords(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(nodes_generation.settings, "OPENAI_API_KEY", "")

    dry_result = nodes_generation.generate_answer(fallback_state("잎 끝이 마르고 갈색으로 변했어요"))
    pest_result = nodes_generation.generate_answer(fallback_state("잎 뒷면에 벌레와 끈적임이 보여요"))

    assert "건조" in dry_result["draft_answer"]["summary"]
    assert "해충" in pest_result["draft_answer"]["summary"]
    assert dry_result["draft_answer"]["summary"] != pest_result["draft_answer"]["summary"]
    assert dry_result["generation_notice"]


def test_generation_notice_is_included_in_safety_notice(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(nodes_generation.settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(nodes_generation.settings, "LLM_FALLBACK_ENABLED", False)
    result = nodes_generation.generate_answer(fallback_state("현재 상태를 전반적으로 확인해 주세요"))

    final = nodes_generation.safety_review({
        "draft_answer": result["draft_answer"],
        "generation_notice": result["generation_notice"],
        "response_mode": "expert",
    })["final_answer"]

    assert "AI 생성 설정이 없어" in final["safetyNotice"]
