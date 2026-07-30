"""용어사전 확장(농약 작물명) 및 1글자 부분문자열 분리 검증."""
import pytest

from app.services.rag import plant_terms
from app.services.rag.vectorstore import SearchResult, filter_by_specific_terms


@pytest.fixture
def stub_dictionary(monkeypatch):
    """DB 접근 없이 캐시를 직접 주입한다."""
    def _apply(crop_names, aliases=None):
        terms = set()
        variant_map = {}
        for name in crop_names:
            plant_terms._register_variants(name, terms, variant_map)
        monkeypatch.setattr(plant_terms, "_cache_terms", terms)
        monkeypatch.setattr(plant_terms, "_cache_tag_variants", variant_map)
        monkeypatch.setattr(plant_terms, "_cache_aliases", dict(aliases or {}))
        monkeypatch.setattr(plant_terms, "_cache_loaded_at", float("inf"))
        monkeypatch.setattr(plant_terms, "_refresh_if_stale", lambda: None)
        return terms
    return _apply


# ---------------------------------------------------------------------------
# 괄호 별칭 표기 변형
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("마(산약)", ["마(산약)", "마", "산약"]),
    ("양앵두(체리)", ["양앵두(체리)", "양앵두", "체리"]),
    ("고려엉겅퀴(곤드레나물)", ["고려엉겅퀴(곤드레나물)", "고려엉겅퀴", "곤드레나물"]),
    ("토마토", ["토마토"]),
    ("", []),
])
def test_crop_name_variants(name, expected):
    assert plant_terms.crop_name_variants(name) == expected


def test_variant_lookup_matches_tag_spelling(stub_dictionary):
    """사용자가 '체리'로 등록해도 '양앵두(체리)' 태그와 교집합이 생겨야 한다."""
    stub_dictionary(["양앵두(체리)"])
    resolved = plant_terms.resolve_target_crop_terms("체리", None)
    assert "양앵두(체리)" in resolved
    assert "체리" in resolved


def test_variant_lookup_from_full_spelling(stub_dictionary):
    stub_dictionary(["마(산약)"])
    resolved = plant_terms.resolve_target_crop_terms("마(산약)", None)
    assert set(resolved) == {"마(산약)", "마", "산약"}


# ---------------------------------------------------------------------------
# 1글자 작물명: 정확일치에는 포함, 부분문자열에서는 제외
# ---------------------------------------------------------------------------
def test_single_char_crop_is_in_exact_set_but_not_substring_set(stub_dictionary):
    stub_dictionary(["무", "밀", "토마토", "블루베리"])
    assert "무" in plant_terms.get_plant_terms()
    assert "밀" in plant_terms.get_plant_terms()
    assert "무" not in plant_terms.get_substring_match_plant_terms()
    assert "밀" not in plant_terms.get_substring_match_plant_terms()
    assert "토마토" in plant_terms.get_substring_match_plant_terms()


def test_single_char_crop_still_resolves_for_its_own_plant(stub_dictionary):
    """무를 재배하는 사용자의 작물 게이트는 정상 작동해야 한다."""
    stub_dictionary(["무"])
    assert plant_terms.resolve_target_crop_terms("무", None) == ["무"]


def test_untagged_doc_not_dropped_by_single_char_substring(stub_dictionary):
    """'무'가 '겹무늬병' 제목에 걸려 딸기 문서를 떨어뜨리면 안 된다.

    crop_or_plant 태그가 없는 문서만 제목 휴리스틱을 타므로 태그 없이 구성한다.
    """
    stub_dictionary(["무", "딸기"])
    results = [SearchResult(
        content="딸기 겹무늬병은 잎에 겹무늬가 생기는 병입니다.",
        metadata={"title": "딸기 겹무늬병", "crop_or_plant": []},
        score=1.0,
    )]
    kept = filter_by_specific_terms("딸기 잎에 무늬가 생겼어요", results, target_crop_terms=["딸기"])
    assert len(kept) == 1


def test_untagged_other_crop_doc_still_dropped(stub_dictionary):
    """2글자 이상 타 작물명은 여전히 제목 휴리스틱으로 걸러져야 한다."""
    stub_dictionary(["딸기", "토마토"])
    results = [SearchResult(
        content="토마토 역병 방제 요령입니다.",
        metadata={"title": "토마토 역병", "crop_or_plant": []},
        score=1.0,
    )]
    kept = filter_by_specific_terms("딸기가 시들어요", results, target_crop_terms=["딸기"])
    assert kept == []


# ---------------------------------------------------------------------------
# 태그 교집합 경로 (기존 1차 개선 동작 회귀)
# ---------------------------------------------------------------------------
def test_tagged_doc_intersection_still_blocks_other_crop(stub_dictionary):
    stub_dictionary(["토마토", "감자"])
    results = [
        SearchResult("감자 역병 문서", {"title": "역병 발생생태", "crop_or_plant": ["감자"]}, 1.0),
        SearchResult("토마토 역병 문서", {"title": "역병 발생생태", "crop_or_plant": ["토마토"]}, 0.9),
    ]
    kept = filter_by_specific_terms("토마토 역병", results, target_crop_terms=["토마토"])
    assert [r.content for r in kept] == ["토마토 역병 문서"]


def test_variant_spelling_intersects_tag(stub_dictionary):
    """'체리'로 등록한 사용자가 '양앵두(체리)' 태그 문서를 통과시켜야 한다."""
    stub_dictionary(["양앵두(체리)", "토마토"])
    results = [SearchResult(
        "양앵두 병해충 문서",
        {"title": "양앵두(체리) 등록 농약", "crop_or_plant": ["양앵두(체리)"]},
        1.0,
    )]
    resolved = plant_terms.resolve_target_crop_terms("체리", None)
    kept = filter_by_specific_terms("체리 병해충", results, target_crop_terms=resolved)
    assert len(kept) == 1


# ---------------------------------------------------------------------------
# 학명 별칭: 속명/변종명이 근연종을 섞지 않아야 한다 (가지과 오매칭 회귀 방지)
# ---------------------------------------------------------------------------
class FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Res", (), {"data": self._rows})()


def _load_with_catalog(monkeypatch, catalog_rows):
    """plant_catalog만 있는(농약 테이블 없는) 환경으로 사전을 로드한다."""
    class FakeSupabase:
        def table(self, name):
            if name == "plant_catalog":
                return FakeTable(catalog_rows)
            raise RuntimeError("psis_pesticide_chunks 없음")

    import app.db.session as db_session
    monkeypatch.setattr(db_session, "supabase", FakeSupabase())
    return plant_terms._load_from_catalog()


def test_genus_word_is_not_registered_as_alias(monkeypatch):
    """Solanum은 감자·토마토·가지가 공유하므로 별칭이 되어서는 안 된다."""
    _, aliases, _, _ = _load_with_catalog(monkeypatch, [
        {"name": "감자", "species": "Solanum tuberosum"},
        {"name": "토마토", "species": "Solanum lycopersicum"},
        {"name": "가지", "species": "Solanum melongena"},
    ])
    assert "solanum" not in aliases
    assert aliases.get("melongena") == "가지"


def test_varietal_epithet_is_not_registered_as_alias(monkeypatch):
    """'capitata'는 Brassica와 Lactuca에 모두 쓰여 양배추/양상추를 섞는다."""
    _, aliases, _, _ = _load_with_catalog(monkeypatch, [
        {"name": "양배추", "species": "Brassica oleracea var. capitata"},
    ])
    assert "capitata" not in aliases
    assert aliases.get("oleracea") == "양배추"


def test_ambiguous_catalog_alias_is_dropped(monkeypatch):
    """같은 종소명이 서로 다른 작물을 가리키면 별칭에서 제외한다."""
    _, aliases, _, _ = _load_with_catalog(monkeypatch, [
        {"name": "상추", "species": "Lactuca sativa"},
        {"name": "무", "species": "Raphanus sativa"},
    ])
    assert "sativa" not in aliases


def test_curated_alias_survives_catalog_ambiguity(monkeypatch):
    """큐레이션 별칭은 카탈로그 모호성에 관계없이 유지된다."""
    _, aliases, _, _ = _load_with_catalog(monkeypatch, [
        {"name": "토마토", "species": "Solanum lycopersicum"},
        {"name": "방울토마토", "species": "Solanum lycopersicum var. cerasiforme"},
    ])
    assert aliases.get("lycopersicum") == "토마토"


def test_species_alias_skipped_when_name_matches(stub_dictionary):
    """이름이 사전에 있으면 학명 별칭은 보지 않는다 (양배추/케일 동일 종 문제)."""
    stub_dictionary(["케일", "양배추"], aliases={"oleracea": "양배추"})
    assert plant_terms.resolve_target_crop_terms("케일", "Brassica oleracea") == ["케일"]


def test_species_alias_used_when_name_unknown(stub_dictionary):
    """별명으로 등록한 식물은 학명 별칭으로 구제한다."""
    stub_dictionary(["토마토"], aliases={"lycopersicum": "토마토"})
    assert plant_terms.resolve_target_crop_terms("토토", "Solanum lycopersicum") == ["토마토"]


# ---------------------------------------------------------------------------
# 기본 사전 폴백
# ---------------------------------------------------------------------------
def test_default_cache_registers_variants(monkeypatch):
    monkeypatch.setattr(plant_terms, "_cache_terms", set())
    monkeypatch.setattr(plant_terms, "_cache_tag_variants", {})
    plant_terms._apply_default_cache()
    assert "토마토" in plant_terms._cache_terms
    assert plant_terms._cache_tag_variants["토마토"] == ["토마토"]


# ---------------------------------------------------------------------------
# 단계 순서: 작물 필터가 top_k 절단보다 먼저 적용되어야 한다
# ---------------------------------------------------------------------------
def test_crop_filter_runs_before_top_k_truncation(monkeypatch, stub_dictionary):
    """해당 작물 문서가 상위 top_k 밖에 있어도 결과에 남아야 한다.

    회귀 시나리오: 병합 단계에서 top_k(8)로 먼저 자르면 벡터 순위 10위의
    토마토 문서가 탈락하고, 남은 타 작물 문서는 필터가 전부 버려 0건이 된다.
    """
    stub_dictionary(["토마토", "고추"])

    # 앞선 8건은 고추, 9번째에 토마토 — top_k=8 선절단이면 토마토가 사라진다
    rows = [
        {"chunk_id": f"c{i}", "content": f"고추 문서 {i}", "similarity": 0.9 - i * 0.01,
         "metadata": {"title": f"고추 문서 {i}", "cropOrPlant": ["고추"]}}
        for i in range(8)
    ]
    rows.append({"chunk_id": "tomato", "content": "토마토 역병 등록 농약",
                 "similarity": 0.80,
                 "metadata": {"title": "토마토 역병 등록 농약", "cropOrPlant": ["토마토"],
                              "safetyTags": ["pesticide_caution"]}})

    class FakeRpc:
        def __init__(self, rows):
            self._rows = rows

        def execute(self):
            return type("Res", (), {"data": self._rows})()

    class FakeSupabase:
        def rpc(self, _name, _params):
            return FakeRpc(rows)

        def table(self, _name):
            raise RuntimeError("키워드 검색 비활성")

    import app.db.session as db_session
    from app.services.rag import vectorstore as vs

    monkeypatch.setattr(db_session, "supabase", FakeSupabase())
    monkeypatch.setattr(vs.session, "supabase", FakeSupabase(), raising=False)
    monkeypatch.setattr(vs.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeEmbeddings:
        def create(self, **_kwargs):
            return type("R", (), {"data": [type("D", (), {"embedding": [0.0] * 1536})()]})()

    class FakeOpenAI:
        def __init__(self, *_a, **_k):
            self.embeddings = FakeEmbeddings()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    results = vs.search_documents("토마토 역병 농약", top_k=8, target_crop_terms=["토마토"])
    titles = [r.metadata.get("title") for r in results]
    assert "토마토 역병 등록 농약" in titles, f"토마토 문서가 절단으로 유실됨: {titles}"
