"""평가 스크립트의 작물 검사 로직 검증.

핵심 요구: 이번에 실제로 놓쳤던 두 결함을 이 검사가 반드시 잡아야 한다.
  1) 게이트에 타 작물이 섞임 (resolve_target_crop_terms → ['토마토','감자'])
  2) 검색 0건이 지표에서 조용히 사라짐 (N/A로 분모 제외)
"""
import importlib.util
from pathlib import Path

import pytest

from app.services.rag import plant_terms

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_rag_v2.py"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("evaluate_rag_v2", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stub_dictionary(monkeypatch):
    def _apply(crop_names, aliases=None):
        terms, variant_map = set(), {}
        for name in crop_names:
            plant_terms._register_variants(name, terms, variant_map)
        monkeypatch.setattr(plant_terms, "_cache_terms", terms)
        monkeypatch.setattr(plant_terms, "_cache_tag_variants", variant_map)
        monkeypatch.setattr(plant_terms, "_cache_aliases", dict(aliases or {}))
        monkeypatch.setattr(plant_terms, "_refresh_if_stale", lambda: None)
    return _apply


def doc(tags, title="문서", safety_tags=None):
    metadata = {"title": title, "crop_or_plant": list(tags)}
    if safety_tags:
        metadata["safety_tags"] = safety_tags
    return {"content": "본문", "metadata": metadata}


# ---------------------------------------------------------------------------
# 게이트 입력 검사 — 놓쳤던 결함 1
# ---------------------------------------------------------------------------
def test_gate_check_catches_congeneric_leak(ev, stub_dictionary):
    """게이트가 감자를 함께 반환하면 검색 결과와 무관하게 FAIL이어야 한다.

    원래 결함 그대로를 재현한다: 속명 'solanum'이 별칭으로 등록되어 있으면
    학명이 Solanum인 식물의 게이트에 감자가 섞인다. (별명으로 등록해 학명
    별칭 경로를 타게 만든다 — 이름이 사전에 있으면 별칭을 보지 않으므로)
    """
    stub_dictionary(["토마토", "감자"], aliases={"solanum": "감자", "lycopersicum": "토마토"})
    item = {"plant_name": "토토", "plant_species": "Solanum lycopersicum",
            "expected_crops": ["토마토"], "forbidden_crops": ["감자"]}
    result = ev.check_crop_gate(item)
    assert result["status"] == "FAIL"
    assert result["leaked"] == ["감자"]
    assert result["leaked_forbidden"] == ["감자"]


def test_gate_check_passes_when_genus_alias_removed(ev, stub_dictionary):
    """속명 별칭을 제거한 현재 구현에서는 같은 케이스가 통과한다 (수정 확인)."""
    stub_dictionary(["토마토", "감자"], aliases={"lycopersicum": "토마토"})
    item = {"plant_name": "토토", "plant_species": "Solanum lycopersicum",
            "expected_crops": ["토마토"], "forbidden_crops": ["감자"]}
    assert ev.check_crop_gate(item)["status"] == "PASS"


def test_gate_check_passes_when_gate_is_clean(ev, stub_dictionary):
    stub_dictionary(["토마토"], aliases={"lycopersicum": "토마토"})
    item = {"plant_name": "토마토", "plant_species": "Solanum lycopersicum",
            "expected_crops": ["토마토"]}
    assert ev.check_crop_gate(item)["status"] == "PASS"


def test_gate_check_allows_spelling_variants(ev, stub_dictionary):
    """'체리' 기대 작물에 '양앵두(체리)' 변형이 들어오는 것은 위반이 아니다."""
    stub_dictionary(["양앵두(체리)"])
    item = {"plant_name": "체리", "plant_species": None, "expected_crops": ["체리"]}
    assert ev.check_crop_gate(item)["status"] == "PASS"


def test_gate_check_flags_empty_gate(ev, stub_dictionary):
    """게이트가 비면 작물 제약이 없으므로 통과로 집계하면 안 된다."""
    stub_dictionary(["토마토"])
    item = {"plant_name": "이름없는화분", "plant_species": None, "expected_crops": ["토마토"]}
    assert ev.check_crop_gate(item)["status"] == "EMPTY"


# ---------------------------------------------------------------------------
# 검색 0건 — 놓쳤던 결함 2
# ---------------------------------------------------------------------------
def test_empty_retrieval_is_not_na(ev, stub_dictionary):
    """검색 0건은 EMPTY로 구분되어야 한다 (N/A로 묶으면 분모에서 사라진다)."""
    stub_dictionary(["토마토"])
    item = {"plant_name": "토마토", "expected_crops": ["토마토"], "forbidden_crops": ["감자"]}
    assert ev.check_crop_consistency(item, [])["status"] == "EMPTY"


def test_untagged_docs_still_na(ev, stub_dictionary):
    """문서는 있으나 작물 태그가 없으면 기존처럼 N/A."""
    stub_dictionary(["토마토"])
    item = {"plant_name": "토마토", "expected_crops": ["토마토"]}
    result = ev.check_crop_consistency(item, [doc([], "일반 관리 원칙")])
    assert result["status"] == "N/A"


# ---------------------------------------------------------------------------
# 엄격 기준: 금지목록에 없는 작물도 잡아야 한다
# ---------------------------------------------------------------------------
def test_strict_mode_catches_crop_absent_from_forbidden_list(ev, stub_dictionary):
    """forbidden_crops에 '오미자'가 없어도 토마토 기대 케이스에서는 오매칭이다."""
    stub_dictionary(["토마토", "오미자"])
    item = {"plant_name": "토마토", "expected_crops": ["토마토"], "forbidden_crops": ["감자"]}
    result = ev.check_crop_consistency(item, [doc(["오미자"], "오미자 역병 등록 농약")])
    assert result["status"] == "FAIL"
    assert result["violations"] == []          # 금지목록 기준으로는 잡히지 않음
    assert len(result["strict_violations"]) == 1  # 엄격 기준으로 잡힘


def test_expected_crop_doc_passes_both_criteria(ev, stub_dictionary):
    stub_dictionary(["토마토"])
    item = {"plant_name": "토마토", "expected_crops": ["토마토"], "forbidden_crops": ["감자"]}
    result = ev.check_crop_consistency(item, [doc(["토마토"], "토마토 역병 등록 농약")])
    assert result["status"] == "PASS"
    assert result["strict_violations"] == []


def test_mixed_tag_doc_is_recorded_not_hidden(ev, stub_dictionary):
    """['몬스테라','무'] 같은 오염 태그 문서는 통과하되 기록으로 남아야 한다."""
    stub_dictionary(["무", "몬스테라"])
    item = {"plant_name": "무", "expected_crops": ["무"], "forbidden_crops": ["몬스테라"]}
    result = ev.check_crop_consistency(item, [doc(["몬스테라", "무"], "몬스테라 응애 농약")])
    assert result["status"] == "PASS"
    assert len(result["mixed"]) == 1
    assert result["mixed"][0]["offending"] == ["몬스테라"]


# ---------------------------------------------------------------------------
# 기대 작물 자동 추론 (30개 중 14개만 손으로 작성된 문제)
# ---------------------------------------------------------------------------
def test_expected_crops_inferred_from_plant_name(ev, stub_dictionary):
    stub_dictionary(["상추"])
    assert ev.effective_expected_crops({"plant_name": "상추"}) == {"상추"}


def test_expected_crops_not_inferred_for_non_crop_name(ev, stub_dictionary):
    """'다육식물'처럼 사전에 없는 이름은 추론하지 않는다."""
    stub_dictionary(["상추"])
    assert ev.effective_expected_crops({"plant_name": "다육식물"}) == set()


def test_explicit_expected_crops_win(ev, stub_dictionary):
    stub_dictionary(["상추", "토마토"])
    assert ev.effective_expected_crops(
        {"plant_name": "상추", "expected_crops": ["토마토"]}
    ) == {"토마토"}


# ---------------------------------------------------------------------------
# 농약 안전 고지 검사 (AC-2)
# ---------------------------------------------------------------------------
def test_safety_check_flags_missing_notice(ev):
    result = ev.check_safety_notice([doc(["토마토"], "토마토 농약", ["pesticide_caution"])], "일반 고지만")
    assert result["status"] == "FAIL"


def test_safety_check_passes_with_notice(ev):
    from app.services.rag import nodes_generation
    notice = f"기본 고지 {nodes_generation.PESTICIDE_CAUTION_NOTICE}"
    result = ev.check_safety_notice([doc(["토마토"], "토마토 농약", ["pesticide_caution"])], notice)
    assert result["status"] == "PASS"


def test_safety_check_skipped_without_pesticide_docs(ev):
    assert ev.check_safety_notice([doc(["토마토"], "토마토 물주기")], "고지") is None
