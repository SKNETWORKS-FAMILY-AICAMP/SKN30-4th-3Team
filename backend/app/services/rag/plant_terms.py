"""plant_catalog 테이블 기반 식물명 사전 로더.

기존에는 식물명/별칭이 vectorstore.py에 하드코딩되어 있어 데이터를 확장해도
검색 필터가 따라가지 못했다. 이 모듈은 DB의 plant_catalog(65종+)를 TTL 캐시로
로드해 검색 용어 사전을 데이터와 동기화한다. DB 조회가 실패하면 하드코딩된
기본 사전으로 동작해 기존 대비 성능이 저하되지 않는다.
"""
import logging
import threading
import time
from typing import Dict, Set

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600.0

# DB 접근 불가 환경(로컬 fallback 등)을 위한 기본 사전 — 기존 하드코딩 값 유지
DEFAULT_PLANT_TERMS: Set[str] = {
    "몬스테라", "스투키", "산세베리아", "선인장", "금전수", "테이블야자", "홍콩야자", "호접란",
    "스파티필럼", "보스턴고사리", "부레옥잠", "올리브나무", "오렌지쟈스민", "관음죽",
    "벵갈고무나무", "디펜바키아", "토마토", "고추", "상추", "배추", "파프리카", "양배추",
    "딸기", "감자", "고구마", "장미", "벚꽃", "개나리", "해바라기", "국화", "라벤더",
    "로즈마리", "바질", "민트", "깻잎", "오이", "호박", "가지", "마늘", "양파", "부추",
}

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
    "capsicum": "고추",
    "pepper": "고추",
    "lettuce": "상추",
    "lactuca": "상추",
    "strawberry": "딸기",
    "fragaria": "딸기",
    "potato": "감자",
    "solanum": "감자",
    "sweet": "고구마",
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
# 도감 물주기 간격 항목: (국문명 소문자, 국문명 첫 토큰 소문자, 학명 소문자, 간격일)
_cache_watering: list = []
_cache_loaded_at: float = 0.0


def _load_from_catalog() -> tuple[Set[str], Dict[str, str], list]:
    from app.db import session

    # aliases/watering_interval_days 컬럼 미적용(마이그레이션 전) 환경까지 단계적 폴백
    response = None
    for columns in (
        "name,species,watering_interval_days,aliases",
        "name,species,watering_interval_days",
        "name,species",
    ):
        try:
            response = session.supabase.table("plant_catalog").select(columns).execute()
            break
        except Exception:
            response = None
    if response is None:
        raise RuntimeError("plant_catalog select 실패")

    terms: Set[str] = set(DEFAULT_PLANT_TERMS)
    aliases: Dict[str, str] = dict(DEFAULT_PLANT_ALIASES)
    watering: list = []

    for row in response.data or []:
        name = str(row.get("name") or "").strip()
        species = str(row.get("species") or "").strip()
        if not name:
            continue
        # 국문명 전체 + 첫 토큰 (예: "몬스테라 델리시오사" → "몬스테라"도 등록)
        terms.add(name)
        first_token = name.split()[0]
        if len(first_token) >= 2:
            terms.add(first_token)
        # 이명(별칭) → 표준국명 첫 토큰으로 매핑 (예: 방울토마토→토마토, 길경→도라지)
        for alias_name in (row.get("aliases") or []):
            alias_name = str(alias_name).strip()
            if len(alias_name) >= 2:
                terms.add(alias_name)
                aliases.setdefault(alias_name, first_token)
        # 학명/영문명의 각 단어를 국문명으로 매핑 (예: "Monstera deliciosa" → 몬스테라)
        for word in species.replace("'", " ").replace(".", " ").split():
            word_lower = word.strip().lower()
            if len(word_lower) >= 3 and word_lower not in _SCIENTIFIC_STOPWORDS:
                aliases.setdefault(word_lower, first_token)
        interval = row.get("watering_interval_days")
        if isinstance(interval, int) and interval > 0:
            watering.append((name.lower(), first_token.lower(), species.lower(), interval))
    return terms, aliases, watering


def _refresh_if_stale() -> None:
    global _cache_terms, _cache_aliases, _cache_watering, _cache_loaded_at
    now = time.monotonic()
    if _cache_terms and now - _cache_loaded_at < CACHE_TTL_SECONDS:
        return
    with _lock:
        if _cache_terms and time.monotonic() - _cache_loaded_at < CACHE_TTL_SECONDS:
            return
        try:
            terms, aliases, watering = _load_from_catalog()
            _cache_terms = terms
            _cache_aliases = aliases
            _cache_watering = watering
            _cache_loaded_at = time.monotonic()
            logger.info(
                "plant_catalog 사전 로드 완료: 용어 %d개, 별칭 %d개, 물주기 항목 %d개",
                len(terms), len(aliases), len(watering)
            )
        except Exception as exc:
            logger.warning("plant_catalog 사전 로드 실패, 기본 사전 사용: %s", exc)
            if not _cache_terms:
                _cache_terms = set(DEFAULT_PLANT_TERMS)
                _cache_aliases = dict(DEFAULT_PLANT_ALIASES)
            # 실패 시에도 TTL을 갱신해 매 요청마다 재시도로 지연이 생기지 않게 한다
            _cache_loaded_at = time.monotonic()


def get_plant_terms() -> Set[str]:
    _refresh_if_stale()
    return _cache_terms


def get_plant_aliases() -> Dict[str, str]:
    _refresh_if_stale()
    return _cache_aliases


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
