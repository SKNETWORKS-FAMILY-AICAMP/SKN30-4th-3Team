"""안전 태그 기반 고지 강제 검증.

검색된 근거 문서가 농약 자료이면 답변 문구에 "농약" 같은 단어가 없어도
safetyNotice에 안전 고지가 반드시 포함되어야 한다
(AGENTS.md 절대규칙 6항, data/PIPELINE.md의 pesticide_caution 규정).
"""
from app.services.rag import nodes_generation
from app.services.rag.vectorstore import normalize_metadata


def draft() -> dict:
    return {
        "summary": "잎 뒷면 관찰이 필요합니다.",
        "possibleCauses": ["해충 흔적 가능성"],
        # 의도적으로 농약/살충 등 위험 키워드를 쓰지 않은 답변
        "todayActions": ["잎 뒷면을 확대해 확인합니다.", "다른 식물과 떨어뜨려 둡니다."],
        "observationChecklist": ["반점이 번지는지 관찰합니다."],
        "citations": [],
    }


def doc(safety_tags=None, key="safety_tags") -> dict:
    metadata = {"source_id": "psis", "title": "토마토 담배가루이 등록 농약"}
    if safety_tags is not None:
        metadata[key] = safety_tags
    return {"content": "제품명: ...", "metadata": metadata}


# ---------------------------------------------------------------------------
# normalize_metadata: 최상위 컬럼 / camelCase metadata 양쪽에서 태그 추출
# ---------------------------------------------------------------------------
def test_normalize_metadata_reads_safety_tags_from_camelcase_metadata():
    metadata = normalize_metadata({
        "chunk_id": "c1",
        "text": "본문",
        "metadata": {
            "title": "토마토 역병 등록 농약",
            "safetyTags": ["not_diagnosis", "pesticide_caution"],
            "usageScope": "safety_reference_only",
            "cropOrPlant": ["토마토"],
        },
    })
    assert metadata["safety_tags"] == ["not_diagnosis", "pesticide_caution"]
    assert metadata["usage_scope"] == "safety_reference_only"
    assert metadata["crop_or_plant"] == ["토마토"]


def test_normalize_metadata_reads_safety_tags_from_top_level_column():
    metadata = normalize_metadata({
        "chunk_id": "c2",
        "text": "본문",
        "safety_tags": ["pesticide_caution"],
        "metadata": {"title": "제목"},
    })
    assert metadata["safety_tags"] == ["pesticide_caution"]


def test_normalize_metadata_without_safety_tags_is_empty_list():
    metadata = normalize_metadata({"chunk_id": "c3", "text": "본문", "metadata": {}})
    assert metadata["safety_tags"] == []
    assert metadata["usage_scope"] is None


# ---------------------------------------------------------------------------
# collect_document_safety_tags
# ---------------------------------------------------------------------------
def test_collect_document_safety_tags_dedupes_and_keeps_order():
    tags = nodes_generation.collect_document_safety_tags([
        doc(["not_diagnosis", "pesticide_caution"]),
        doc(["pesticide_caution", "label_check_required"]),
    ])
    assert tags == ["not_diagnosis", "pesticide_caution", "label_check_required"]


def test_collect_document_safety_tags_tolerates_missing_and_scalar():
    assert nodes_generation.collect_document_safety_tags(None) == []
    assert nodes_generation.collect_document_safety_tags([doc()]) == []
    assert nodes_generation.collect_document_safety_tags([doc("pesticide_caution")]) == ["pesticide_caution"]


def test_collect_document_safety_tags_reads_camelcase_key():
    assert nodes_generation.collect_document_safety_tags(
        [doc(["pesticide_caution"], key="safetyTags")]
    ) == ["pesticide_caution"]


# ---------------------------------------------------------------------------
# safety_review: 태그 기반 고지 강제
# ---------------------------------------------------------------------------
def test_pesticide_notice_forced_even_when_answer_text_has_no_keyword():
    final = nodes_generation.safety_review({
        "draft_answer": draft(),
        "retrieved_docs": [doc(["not_diagnosis", "pesticide_caution", "label_check_required"])],
        "response_mode": "expert",
    })["final_answer"]

    notice = final["safetyNotice"]
    assert "농약" in notice
    assert "안전사용기준" in notice
    assert "라벨" in notice
    assert "전문가" in notice
    # 답변 본문은 건드리지 않는다
    assert final["todayActions"] == draft()["todayActions"]


def test_label_check_notice_when_pesticide_caution_absent():
    final = nodes_generation.safety_review({
        "draft_answer": draft(),
        "retrieved_docs": [doc(["label_check_required"])],
        "response_mode": "expert",
    })["final_answer"]
    assert "라벨" in final["safetyNotice"]
    assert nodes_generation.PESTICIDE_CAUTION_NOTICE not in final["safetyNotice"]


def test_no_pesticide_notice_for_ordinary_documents():
    final = nodes_generation.safety_review({
        "draft_answer": draft(),
        "retrieved_docs": [doc(["not_diagnosis"])],
        "response_mode": "expert",
    })["final_answer"]
    assert nodes_generation.PESTICIDE_CAUTION_NOTICE not in final["safetyNotice"]
    assert nodes_generation.LABEL_CHECK_NOTICE not in final["safetyNotice"]
    assert final["safetyNotice"]


def test_pesticide_notice_applies_in_companion_mode():
    final = nodes_generation.safety_review({
        "draft_answer": draft(),
        "retrieved_docs": [doc(["pesticide_caution"])],
        "response_mode": "companion",
    })["final_answer"]
    assert "안전사용기준" in final["safetyNotice"]


def test_safety_review_without_retrieved_docs_key():
    """기존 호출부(문서 없이 draft만 전달)와의 하위 호환."""
    final = nodes_generation.safety_review({"draft_answer": draft()})["final_answer"]
    assert final["safetyNotice"]


def test_generation_notice_and_pesticide_notice_coexist():
    final = nodes_generation.safety_review({
        "draft_answer": draft(),
        "retrieved_docs": [doc(["pesticide_caution"])],
        "generation_notice": "현재 AI 생성 연결을 확인할 수 없어",
        "response_mode": "expert",
    })["final_answer"]
    assert "현재 AI 생성 연결을 확인할 수 없어" in final["safetyNotice"]
    assert "안전사용기준" in final["safetyNotice"]


def test_action_keyword_now_covers_fungicide_terms():
    final = nodes_generation.safety_review({
        "draft_answer": {**draft(), "todayActions": ["살균제 사용을 검토합니다", "물받이를 비웁니다"]},
        "retrieved_docs": [],
        "response_mode": "expert",
    })["final_answer"]
    fungicide_action = next(a for a in final["todayActions"] if "살균제" in a)
    assert "전문가" in fungicide_action
    assert final["todayActions"][1] == "물받이를 비웁니다"
