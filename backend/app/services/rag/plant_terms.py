"""plant_catalog 테이블 기반 식물명 사전 로더.

기존에는 식물명/별칭이 vectorstore.py에 하드코딩되어 있어 데이터를 확장해도
검색 필터가 따라가지 못했다. 이 모듈은 DB의 plant_catalog(65종+)를 TTL 캐시로
로드해 검색 용어 사전을 데이터와 동기화한다. DB 조회가 실패하면 하드코딩된
기본 사전으로 동작해 기존 대비 성능이 저하되지 않는다.
"""
import logging
import re
import threading
import time
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600.0

# 부분문자열 매칭에 사용할 수 있는 최소 용어 길이.
# '무'·'배'·'감' 같은 1글자 작물명을 부분문자열로 쓰면 '겹무늬병'·'재배'·'감염'처럼
# 무관한 제목에 광범위하게 매칭된다(실측: '무' 516건, '배' 377건, '감' 375건).
# 정확일치 판정에는 필요하므로 사전에서 빼지 않고 부분문자열 용도에서만 제외한다.
MIN_SUBSTRING_TERM_LENGTH = 2

# 농약 청크 테이블 작물명 조회 페이지 크기 (Supabase 기본 max-rows 1000 대응)
PESTICIDE_CROP_PAGE_SIZE = 1000
PESTICIDE_CROP_MAX_PAGES = 10

# DB 접근 불가 환경(로컬 fallback 등)을 위한 기본 사전 — 기존 하드코딩 값 유지
DEFAULT_PLANT_TERMS: Set[str] = {
    "몬스테라", "스투키", "산세베리아", "선인장", "금전수", "테이블야자", "홍콩야자", "호접란",
    "스파티필럼", "보스턴고사리", "부레옥잠", "올리브나무", "오렌지쟈스민", "관음죽",
    "벵갈고무나무", "디펜바키아", "토마토", "고추", "상추", "배추", "파프리카", "양배추",
    "딸기", "감자", "고구마", "장미", "벚꽃", "개나리", "해바라기", "국화", "라벤더",
    "로즈마리", "바질", "민트", "깻잎", "오이", "호박", "가지", "마늘", "양파", "부추",
}

# 학명 별칭. 여러 작물이 공유하는 속명(genus)은 여기에 두지 않는다 —
# 예: Solanum은 감자/토마토/가지가 공유하므로 "solanum"을 감자로 매핑하면
# 토마토 사용자의 작물 게이트에 감자가 섞여 가지과 오매칭이 되살아난다.
# 종소명(epithet)처럼 작물을 고유하게 지목하는 단어만 등록한다.
# 카탈로그에서 실제로 여러 작물을 가리키게 되는 단어는 로드 시 자동 제외된다.
DEFAULT_PLANT_ALIASES: Dict[str, str] = {
    "monstera": "몬스테라",
    "deliciosa": "몬스테라",
    "sansevieria": "스투키",
    "dracaena": "스투키",
    "zamioculcas": "금전수",
    "spathiphyllum": "스파티필럼",
    "orchid": "호접란",
    "phalaenopsis": "호접란",
    "tomato": "토마토",
    "lycopersicum": "토마토",
    # capsicum/pepper(고추·파프리카), lettuce/lactuca(상추·양상추)는 앱이 다루는
    # 작물 안에서도 두 종을 가리키므로 별칭에서 제외한다 — 국문명으로 매칭된다.
    "strawberry": "딸기",
    "fragaria": "딸기",
    "potato": "감자",
    "tuberosum": "감자",
    "rose": "장미",
    "rosa": "장미",
    "helianthus": "해바라기",
    "forsythia": "개나리",
}

# 학명에서 별칭으로 쓰지 않을 일반 단어
_SCIENTIFIC_STOPWORDS = {"var", "spp", "sp", "subsp", "cv", "hybrid", "x"}

_lock = threading.Lock()
_cache_terms: Set[str] = set()
_cache_aliases: Dict[str, str] = {}
# 태그 표기 변형: 소문자 변형 → 같은 작물을 가리키는 모든 표기 목록
_cache_tag_variants: Dict[str, List[str]] = {}
# 도감 물주기 간격 항목: (국문명 소문자, 국문명 첫 토큰 소문자, 학명 소문자, 간격일)
_cache_watering: list = []
_cache_loaded_at: float = 0.0

_PAREN_ALIAS_PATTERN = re.compile(r"^(?P<head>[^()]+)\((?P<alias>[^()]+)\)$")


def crop_name_variants(name: str) -> List[str]:
    """작물명의 태그 표기 변형을 만든다. 예: '마(산약)' → ['마(산약)', '마', '산약'].

    청크의 crop_or_plant 태그는 원문 표기를 그대로 담기 때문에, 사용자가 식물을
    '산약'으로 등록해도 '마(산약)' 태그와 교집합이 생겨야 한다. 실데이터에
    '마(산약)', '양앵두(체리)', '고려엉겅퀴(곤드레나물)' 형태가 존재한다.
    """
    name = (name or "").strip()
    if not name:
        return []
    variants = [name]
    match = _PAREN_ALIAS_PATTERN.match(name)
    if match:
        for part in (match.group("head"), match.group("alias")):
            part = part.strip()
            if part and part not in variants:
                variants.append(part)
    return variants


def _register_variants(name: str, terms: Set[str], variant_map: Dict[str, List[str]]) -> None:
    """작물명과 그 표기 변형을 용어 집합·변형 맵에 함께 등록한다."""
    variants = crop_name_variants(name)
    for variant in variants:
        terms.add(variant)
        bucket = variant_map.setdefault(variant.lower(), [])
        for value in variants:
            if value not in bucket:
                bucket.append(value)


def _load_pesticide_crop_names() -> Set[str]:
    """농약 청크 테이블(psis_pesticide_chunks)의 작물명을 수집한다.

    이 테이블은 plant_catalog에 없는 노지·시설 작물 130여 종을 담고 있다. 사전에
    없으면 resolve_target_crop_terms가 빈 리스트를 반환해 작물 교집합 게이트가
    아예 걸리지 않고, 그 상태에서 농약 문서가 검색되면 타 작물 약제가 통과할 수
    있다. 테이블이 없는 환경(마이그레이션 전)에서는 예외를 그대로 올려 호출부가
    기본 사전으로 폴백하게 한다.
    """
    from app.db import session

    names: Set[str] = set()
    for page in range(PESTICIDE_CROP_MAX_PAGES):
        start = page * PESTICIDE_CROP_PAGE_SIZE
        response = (
            session.supabase.table("psis_pesticide_chunks")
            .select("crop_name")
            .range(start, start + PESTICIDE_CROP_PAGE_SIZE - 1)
            .execute()
        )
        rows = response.data or []
        for row in rows:
            name = str(row.get("crop_name") or "").strip()
            if name:
                names.add(name)
        if len(rows) < PESTICIDE_CROP_PAGE_SIZE:
            break
    return names


def _load_from_catalog() -> tuple[Set[str], Dict[str, str], List[str], list]:
    from app.db import session

    try:
        response = session.supabase.table("plant_catalog").select(
            "name,species,watering_interval_days"
        ).execute()
    except Exception:
        # watering_interval_days 컬럼 미적용(마이그레이션 전) 환경
        response = session.supabase.table("plant_catalog").select("name,species").execute()

    terms: Set[str] = set()
    variant_map: Dict[str, List[str]] = {}
    watering: list = []
    # 별칭 후보: 단어 → 그 단어가 가리키는 작물 집합.
    # 두 개 이상을 가리키면(주로 속명) 모호하므로 최종 별칭에서 제외한다.
    # DEFAULT_PLANT_ALIASES는 이 판정 대상이 아니다 — 손으로 큐레이션한 항목이고
    # 마지막에 덮어써서 우선한다(예: lycopersicum은 토마토/방울토마토에 걸리지만
    # 두 표기 모두 토마토를 뜻하므로 토마토로 확정하는 것이 맞다).
    alias_candidates: Dict[str, Set[str]] = {}

    for default_term in DEFAULT_PLANT_TERMS:
        _register_variants(default_term, terms, variant_map)

    for row in response.data or []:
        name = str(row.get("name") or "").strip()
        species = str(row.get("species") or "").strip()
        if not name:
            continue
        # 국문명 전체 + 첫 토큰 (예: "몬스테라 델리시오사" → "몬스테라"도 등록)
        _register_variants(name, terms, variant_map)
        first_token = name.split()[0]
        if len(first_token) >= 2:
            _register_variants(first_token, terms, variant_map)
        # 학명의 종소명만 국문명으로 매핑한다 (예: "Monstera deliciosa" → 몬스테라).
        # - 첫 토큰(속명)은 제외: 속은 근연종이 정의상 공유한다.
        #   Prunus는 벚나무·매실·살구·자두·복숭아가, Solanum은 감자·토마토·가지가 공유.
        # - 종소명 뒤(var. 이하 변종명)도 제외: 변종명은 속을 넘어 재사용된다.
        #   'capitata'는 Brassica oleracea와 Lactuca sativa에 모두 쓰여 양배추/양상추가 섞인다.
        species_words = species.replace("'", " ").replace(".", " ").split()
        for word in species_words[1:2]:
            word_lower = word.strip().lower()
            if len(word_lower) >= 3 and word_lower not in _SCIENTIFIC_STOPWORDS:
                alias_candidates.setdefault(word_lower, set()).add(first_token)
        interval = row.get("watering_interval_days")
        if isinstance(interval, int) and interval > 0:
            watering.append((name.lower(), first_token.lower(), species.lower(), interval))

    aliases: Dict[str, str] = {}
    ambiguous: List[str] = []
    for word, targets in alias_candidates.items():
        if len(targets) == 1:
            aliases[word] = next(iter(targets))
        else:
            ambiguous.append(word)
    if ambiguous:
        logger.info(
            "모호한 학명 별칭 %d개 제외(여러 작물 공유): %s",
            len(ambiguous), ", ".join(sorted(ambiguous)[:12])
        )
    # 큐레이션 별칭이 카탈로그 추론값을 덮어쓴다
    aliases.update(DEFAULT_PLANT_ALIASES)

    # 농약 데이터 작물명 병합 — 실패해도 도감 사전만으로 계속 동작한다
    try:
        for crop_name in _load_pesticide_crop_names():
            _register_variants(crop_name, terms, variant_map)
    except Exception as exc:
        logger.warning("농약 청크 작물명 로드 실패, 도감 사전만 사용: %s", exc)

    return terms, aliases, variant_map, watering


def _apply_default_cache() -> None:
    """DB 접근 불가 환경용 기본 사전을 캐시에 적재한다."""
    global _cache_terms, _cache_aliases, _cache_tag_variants
    terms: Set[str] = set()
    variant_map: Dict[str, List[str]] = {}
    for default_term in DEFAULT_PLANT_TERMS:
        _register_variants(default_term, terms, variant_map)
    _cache_terms = terms
    _cache_aliases = dict(DEFAULT_PLANT_ALIASES)
    _cache_tag_variants = variant_map


def _refresh_if_stale() -> None:
    global _cache_terms, _cache_aliases, _cache_tag_variants, _cache_watering, _cache_loaded_at
    now = time.monotonic()
    if _cache_terms and now - _cache_loaded_at < CACHE_TTL_SECONDS:
        return
    with _lock:
        if _cache_terms and time.monotonic() - _cache_loaded_at < CACHE_TTL_SECONDS:
            return
        try:
            terms, aliases, variant_map, watering = _load_from_catalog()
            _cache_terms = terms
            _cache_aliases = aliases
            _cache_tag_variants = variant_map
            _cache_watering = watering
            _cache_loaded_at = time.monotonic()
            logger.info(
                "작물 용어 사전 로드 완료: 용어 %d개, 별칭 %d개, 표기변형 %d개, 물주기 항목 %d개",
                len(terms), len(aliases), len(variant_map), len(watering)
            )
        except Exception as exc:
            logger.warning("작물 용어 사전 로드 실패, 기본 사전 사용: %s", exc)
            if not _cache_terms:
                _apply_default_cache()
            # 실패 시에도 TTL을 갱신해 매 요청마다 재시도로 지연이 생기지 않게 한다
            _cache_loaded_at = time.monotonic()


def get_plant_terms() -> Set[str]:
    """정확일치 판정용 전체 용어 집합 (1글자 작물명 포함)."""
    _refresh_if_stale()
    return _cache_terms


def get_substring_match_plant_terms() -> Set[str]:
    """부분문자열 매칭에 사용할 수 있는 용어만 반환한다.

    1글자 작물명은 제외한다 — MIN_SUBSTRING_TERM_LENGTH 주석 참고.
    """
    return {term for term in get_plant_terms() if len(term) >= MIN_SUBSTRING_TERM_LENGTH}


def get_plant_aliases() -> Dict[str, str]:
    _refresh_if_stale()
    return _cache_aliases


def get_tag_variants(term: str) -> List[str]:
    """주어진 용어와 같은 작물을 가리키는 모든 태그 표기를 반환한다."""
    _refresh_if_stale()
    return list(_cache_tag_variants.get((term or "").strip().lower()) or ([term] if term else []))


def resolve_target_crop_terms(name: str | None, species: str | None) -> list[str]:
    """plant_data의 name/species를 도감 용어 사전으로 정규화해, rag_chunks의
    crop_or_plant 구조화 태그와 직접 비교 가능한 한글 작물명 후보를 만든다.

    자유 텍스트 질문 문자열을 파싱해서 작물명을 추정하는 방식(specific_query_terms)보다
    신뢰도가 높다 — 사용자가 지은 별명이나 질문 표현에 흔들리지 않고, 사용자가
    등록한 식물 레코드(name/species) 자체를 근거로 삼기 때문이다.

    매칭된 작물은 표기 변형까지 함께 반환한다. 사용자가 '산약'으로 등록했어도
    청크 태그가 '마(산약)'이면 교집합이 생겨야 하기 때문이다(crop_name_variants 참고).
    """
    _refresh_if_stale()
    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: str | None) -> None:
        if not term:
            return
        term = term.strip()
        if term and term not in seen:
            seen.add(term)
            terms.append(term)

    def _add_with_variants(term: str | None) -> None:
        if not term:
            return
        for variant in _cache_tag_variants.get(term.strip().lower()) or [term]:
            _add(variant)

    clean_name = (name or "").strip()
    if clean_name in _cache_terms:
        _add_with_variants(clean_name)
    elif clean_name:
        first_token = clean_name.split()[0]
        if first_token in _cache_terms:
            _add_with_variants(first_token)

    # 국문명이 사전에 매칭되면 학명 별칭은 보지 않는다.
    # 학명 별칭은 이름을 알아보지 못한 경우를 구제하는 보조 수단이고, 같은 종 안의
    # 재배형(예: 양배추와 케일은 둘 다 Brassica oleracea)은 학명으로 구분할 수 없어
    # 그대로 두면 타 작물 용어가 게이트에 섞인다. 이름이 더 신뢰도 높은 신호다.
    if terms:
        return terms

    clean_species = (species or "").strip()
    for word in clean_species.replace("'", " ").replace(".", " ").split():
        alias = _cache_aliases.get(word.strip().lower())
        if alias:
            _add_with_variants(alias)

    return terms


def find_catalog_watering_interval(name: str | None, species: str | None) -> int | None:
    """
    도감(plant_catalog)에서 이 식물에 해당하는 권장 물주기 간격을 찾는다.
    이름/품종 문자열과 도감 국문명·학명의 포함 관계로 매칭하며, 없으면 None.
    """
    _refresh_if_stale()
    haystack = f"{name or ''} {species or ''}".strip().lower()
    if not haystack:
        return None
    for catalog_name, first_token, catalog_species, interval in _cache_watering:
        if catalog_name and catalog_name in haystack:
            return interval
        if first_token and len(first_token) >= 2 and first_token in haystack:
            return interval
        if catalog_species and catalog_species in haystack:
            return interval
    return None


# 종 그룹별 권장 물주기 간격 (일). 이름/품종 문자열 키워드 매칭 — 먼저 매칭되는 규칙 우선.
DEFAULT_WATERING_INTERVAL_DAYS = 7
WATERING_INTERVAL_RULES: list = [
    # 다육·선인장류: 건조에 강함
    (14, ("선인장", "다육", "스투키", "산세베리아", "산세비에리아", "금전수", "알로에",
          "틸란드시아", "리톱스", "세덤", "에케베리아", "cactus", "sansevieria",
          "dracaena", "zamioculcas", "aloe", "succulent")),
    # 허브·채소류: 물 소모가 빠름
    (3, ("바질", "민트", "고수", "루꼴라", "상추", "깻잎", "시금치", "부추",
         "토마토", "방울토마토", "오이", "고추", "파프리카", "딸기", "가지",
         "basil", "mint", "lettuce", "tomato", "cucumber", "strawberry")),
]


def watering_interval_days(name: str | None, species: str | None) -> int:
    """도감 매칭 → 키워드 규칙 → 기본값 순으로 권장 물주기 간격(일)을 결정한다."""
    try:
        catalog_interval = find_catalog_watering_interval(name, species)
        if catalog_interval:
            return catalog_interval
    except Exception:
        logger.warning("도감 물주기 간격 조회 실패, 키워드 규칙 사용", exc_info=True)
    haystack = f"{name or ''} {species or ''}".lower()
    for days, keywords in WATERING_INTERVAL_RULES:
        if any(keyword in haystack for keyword in keywords):
            return days
    return DEFAULT_WATERING_INTERVAL_DAYS
