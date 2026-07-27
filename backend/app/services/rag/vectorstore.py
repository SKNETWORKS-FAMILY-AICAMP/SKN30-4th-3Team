import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.db import session
from app.core.config import settings

# 프로젝트 루트 경로 (Fallback 파일 조회용)
backend_dir = Path(__file__).resolve().parents[4]
docs_json_path = backend_dir / "data" / "processed" / "gardening_docs.json"

# Two-Stage Retrieval: 1단계(벡터 검색)는 임계값을 완화해 후보를 폭넓게 수집(Recall)하고,
# 2단계(grade_or_rerank)에서 LLM이 엄격하게 정제(Precision)한다.
VECTOR_MATCH_THRESHOLD = 0.25
VECTOR_CANDIDATE_COUNT = 80

class SearchResult:
    def __init__(self, content: str, metadata: Dict[str, Any], score: float):
        self.content = content
        self.metadata = metadata
        self.score = score


STOPWORDS = {
    "식물", "작물", "사진", "분석", "상태", "관리", "질문", "현재", "공식", "문서",
    "어떻게", "해주세요", "알려줘", "알려주세요", "가능성", "상담", "진단",
    "얼마나", "자주", "주어야", "하나요", "할까요", "되나요", "있나요", "인가요",
    "무엇", "어떤", "언제", "해야", "합니다", "주세요",
    "plant", "care", "photo", "image", "document", "official",
}

CARE_TERMS = {
    "물주기", "물관리", "키우기", "키우는", "방법", "관리법", "가이드", "재배", "재배법",
    "햇빛", "광량", "온도", "습도", "흙", "분갈이", "비료", "병해충", "잎", "줄기",
    "수분공급", "관수", "생육", "건조", "과습", "일반", "황화", "갈변", "시듦",
}

# 식물명/별칭 사전은 plant_catalog 테이블에서 TTL 캐시로 로드한다 (plant_terms.py).
# DB 조회 실패 시 하드코딩된 기본 사전으로 자동 fallback 된다.
from app.services.rag.plant_terms import get_plant_aliases, get_plant_terms


def expand_query_aliases(query: str) -> str:
    lower_query = query.lower()
    aliases = [alias for key, alias in get_plant_aliases().items() if key in lower_query]
    if not aliases:
        return query
    return f"{query} {' '.join(dict.fromkeys(aliases))}"


def tokenize_query(query: str) -> List[str]:
    query = expand_query_aliases(query)
    clean_query = query.replace(",", " ").replace("?", " ").replace(".", " ").replace("/", " ")
    tokens = []
    for token in clean_query.split():
        token = token.strip().lower()
        if len(token) <= 1 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def specific_query_terms(query: str) -> List[str]:
    particles = ["는", "은", "를", "을", "가", "이", "의", "에", "와", "과", "로", "으로", "에서"]
    specific = []
    for token in tokenize_query(query):
        cleaned = token
        for particle in particles:
            if token.endswith(particle) and len(token) > len(particle):
                cleaned = token[: -len(particle)]
                break
        if cleaned not in CARE_TERMS and len(cleaned) >= 2:
            specific.append(cleaned)
    return specific


def filter_by_specific_terms(
    query: str,
    results: List[SearchResult],
    target_crop_terms: Optional[List[str]] = None,
) -> List[SearchResult]:
    terms = specific_query_terms(query)
    known_plant_terms = get_plant_terms()
    query_plant_terms = [term for term in terms if term in known_plant_terms]

    # plant_data(사용자가 등록한 식물의 name/species)에서 정규화된 작물명이 있으면
    # 자유 텍스트 파싱 결과보다 우선 신뢰한다 — 별명/표현 흔들림에 영향받지 않는다.
    plant_terms = list(dict.fromkeys([*(target_crop_terms or []), *query_plant_terms]))

    if not terms and not plant_terms:
        return results

    plant_terms_lower = {term.lower() for term in plant_terms}

    filtered = []
    for result in results:
        metadata = result.metadata or {}
        crop_tags = [str(c).strip() for c in (metadata.get("crop_or_plant") or []) if str(c).strip()]

        if plant_terms and crop_tags:
            # 구조화 작물 태그(crop_or_plant)가 있으면 제목/본문 문자열 휴리스틱 대신
            # 결정적 교집합 판정을 사용한다. 제목이 일반형이라 문자열 규칙이 놓치던
            # "본문에 여러 작물이 나열된 문서"도 여기서 정확히 걸러진다.
            crop_tags_lower = {tag.lower() for tag in crop_tags}
            if crop_tags_lower & plant_terms_lower:
                filtered.append(result)
            continue

        haystack = " ".join(
            str(part or "")
            for part in [
                result.content,
                metadata.get("title"),
                metadata.get("section"),
                metadata.get("excerpt"),
            ]
        ).lower()
        if plant_terms:
            # crop_or_plant 태그가 없는 문서(일반 원칙 문서 등)만 기존 문자열 휴리스틱을 적용한다.
            title_haystack = " ".join(
                str(part or "")
                for part in [
                    metadata.get("title"),
                    metadata.get("section"),
                    metadata.get("excerpt"),
                ]
            ).lower()
            title_plant_terms = [term for term in known_plant_terms if term.lower() in title_haystack]
            if title_plant_terms and not any(term in plant_terms for term in title_plant_terms):
                continue
            if any(term.lower() in haystack for term in plant_terms):
                filtered.append(result)
            continue
        if any(term.lower() in haystack for term in terms):
            filtered.append(result)
    return filtered

def _extract_crop_or_plant(item: Dict[str, Any], metadata: Dict[str, Any]) -> List[str]:
    """행/메타데이터에서 작물·식물 태그를 뽑는다.

    적재 스크립트(data/scripts/load_supabase_pgvector.py)는 최상위 crop_or_plant
    컬럼이 아니라 metadata.cropOrPlant(camelCase)에 값을 채운다 — 실측 결과 청크의
    약 98%가 metadata.cropOrPlant를 갖고 있다. 두 경로 모두 확인해 향후 최상위
    컬럼이 채워지더라도 그대로 동작하게 한다.
    """
    raw = (
        item.get("crop_or_plant")
        or metadata.get("cropOrPlant")
        or metadata.get("crop_or_plant")
        or []
    )
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(v).strip() for v in raw if str(v).strip()]


def normalize_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    nested_source = item.get("rag_sources") if isinstance(item.get("rag_sources"), dict) else {}
    return {
        "chunk_id": item.get("chunk_id") or item.get("id") or metadata.get("chunkId") or metadata.get("chunk_id"),
        "source_id": item.get("source_id") or metadata.get("sourceId") or metadata.get("source_id") or "",
        "title": item.get("title") or metadata.get("title") or nested_source.get("title") or "출처 미상",
        "url": item.get("url") or metadata.get("url") or nested_source.get("url"),
        "publisher": item.get("publisher") or metadata.get("publisher") or nested_source.get("publisher"),
        "section": item.get("section") or metadata.get("section") or metadata.get("category"),
        "excerpt": item.get("excerpt") or metadata.get("excerpt") or metadata.get("contentPreview"),
        "crop_or_plant": _extract_crop_or_plant(item, metadata),
    }

def score_text(query_tokens: List[str], text: str, keywords: List[str]) -> float:
    score = 0.0
    lower_text = text.lower()
    lower_keywords = [str(keyword).lower() for keyword in keywords]
    stopwords = {"식물", "작물", "사진", "분석", "상태", "관리", "질문", "알려줘", "현재", "기준", "공식", "문서"}
    for token in query_tokens:
        token_lower = token.lower()
        if not token_lower or token_lower in stopwords:
            continue
        if token_lower in lower_text:
            score += 2.0 if len(token_lower) >= 3 else 1.0
        for keyword in lower_keywords:
            if token_lower in keyword or keyword in token_lower:
                score += 2.0
    return score


def merge_results(*groups: List[SearchResult], top_k: int, rrf_k: int = 60) -> List[SearchResult]:
    # RRF (Reciprocal Rank Fusion) 알고리즘 적용
    rrf_scores: dict[str, float] = {}
    merged_results: dict[str, SearchResult] = {}
    
    for group in groups:
        # group은 이미 자신의 점수로 정렬되어 있다고 가정
        for rank, result in enumerate(group):
            metadata = result.metadata or {}
            key = str(metadata.get("chunk_id") or metadata.get("id") or result.content[:120])
            
            # RRF 점수 누적
            if key not in rrf_scores:
                rrf_scores[key] = 0.0
                merged_results[key] = result
            
            rrf_scores[key] += 1.0 / (rank + 1 + rrf_k)
            
    # 누적된 RRF 점수로 갱신
    for key, result in merged_results.items():
        result.score = rrf_scores[key]
        
    return sorted(merged_results.values(), key=lambda item: item.score, reverse=True)[:top_k]

KEYWORD_SELECT = "chunk_id,source_id,text,symptom_keywords,crop_or_plant,metadata,rag_sources(title,url,publisher)"
KEYWORD_FILTER_ROW_LIMIT = 120
LEGACY_SCAN_ROW_LIMIT = 1000


def _sanitize_filter_term(term: str) -> str:
    """PostgREST or_ 필터 구문을 깨뜨릴 수 있는 문자를 제거한다."""
    for ch in (",", "(", ")", "{", "}", "%", "*", '"'):
        term = term.replace(ch, "")
    return term.strip()


def _score_rows(rows: list, query_tokens: List[str], top_k: int) -> List[SearchResult]:
    results = []
    for item in rows:
        content = item.get("text") or item.get("content") or ""
        if not content:
            continue
        keywords = item.get("symptom_keywords") or []
        if not isinstance(keywords, list):
            keywords = [str(keywords)]
        score = score_text(query_tokens, content, keywords)
        if score >= 2.0:
            results.append(SearchResult(content=content, metadata=normalize_metadata(item), score=score))
    results.sort(key=lambda result: result.score, reverse=True)
    return results[:top_k]


def supabase_keyword_search(query: str, top_k: int = 3) -> List[SearchResult]:
    """
    벡터 검색을 보완하는 키워드 검색기입니다.
    검색어를 서버측 필터(crop_or_plant contains / text ilike)로 전달해
    필요한 행만 받아온다 — 과거의 테이블 풀스캔(최대 3,000행) 방식을 대체한다.
    """
    query_tokens = tokenize_query(query)
    if not query_tokens:
        return []

    terms = [_sanitize_filter_term(t) for t in specific_query_terms(query)]
    terms = [t for t in terms if len(t) >= 2]
    known_plant_terms = get_plant_terms()
    plant_terms = [t for t in terms if t in known_plant_terms]
    other_terms = [t for t in terms if t not in known_plant_terms][:4]

    conditions: list[str] = []
    for term in plant_terms:
        # crop_or_plant 배열 정확 매칭 + 본문 부분 매칭
        conditions.append(f"crop_or_plant.cs.{{{term}}}")
        conditions.append(f"text.ilike.%{term}%")
    for term in other_terms:
        conditions.append(f"text.ilike.%{term}%")
    if not conditions:
        # 특정 용어가 없으면 상위 토큰으로라도 서버측 필터를 건다
        for token in [_sanitize_filter_term(t) for t in query_tokens[:4]]:
            if len(token) >= 2:
                conditions.append(f"text.ilike.%{token}%")
    if not conditions:
        return []

    try:
        response = (
            session.supabase.table("rag_chunks")
            .select(KEYWORD_SELECT)
            .or_(",".join(conditions))
            .limit(KEYWORD_FILTER_ROW_LIMIT)
            .execute()
        )
        return _score_rows(response.data or [], query_tokens, top_k)
    except Exception as exc:
        print(f"[RAG SEARCH WARNING] 서버측 키워드 필터 실패, 제한 스캔으로 전환: {exc}")

    # 서버측 필터가 실패하는 스키마 환경을 위한 제한된 스캔 fallback
    try:
        response = (
            session.supabase.table("rag_chunks")
            .select(KEYWORD_SELECT)
            .limit(LEGACY_SCAN_ROW_LIMIT)
            .execute()
        )
        return _score_rows(response.data or [], query_tokens, top_k)
    except Exception as exc:
        print(f"[RAG SEARCH WARNING] Supabase keyword fallback 조회 실패: {exc}")
        return []

def fallback_keyword_search(query: str, top_k: int = 3) -> List[SearchResult]:
    """
    OpenAI API Key 또는 pgvector RPC가 없는 환경에서 동작하는 텍스트 기반 키워드 매칭 검색기입니다.
    """
    if not docs_json_path.exists():
        print(f"[Fallback Search] 가이드 문서 파일이 존재하지 않습니다: {docs_json_path}")
        return []
        
    with open(docs_json_path, "r", encoding="utf-8") as f:
        docs = json.load(f)
        
    query_tokens = tokenize_query(query)
    
    results = []
    for doc in docs:
        score = 0.0
        for token in query_tokens:
            for keyword in doc.get("keywords", []):
                if token in keyword or keyword in token:
                    score += 2.0
            if token in doc["content"]:
                score += 1.0
                
        if score >= 2.0:
            metadata = {
                "source_id": doc["id"],
                "title": doc["title"],
                "url": doc["url"],
                "publisher": doc["publisher"]
            }
            results.append(SearchResult(
                content=doc["content"],
                metadata=metadata,
                score=score
            ))
            
    results.sort(key=lambda x: x.score, reverse=True)
    return results[:top_k]

def search_documents(
    query: str,
    top_k: int = 8,
    target_crop_terms: Optional[List[str]] = None,
) -> List[SearchResult]:
    """
    최적의 수단을 사용하여 지침 문서를 검색합니다.
    1순위: OpenAI Embedding + Supabase pgvector RPC 호출
    2순위: 텍스트 기반 로컬 키워드 매칭 Fallback 엔진

    target_crop_terms: plant_data(name/species)를 정규화해 얻은 작물명 후보.
    주어지면 filter_by_specific_terms가 crop_or_plant 구조화 태그와의 교집합
    판정에 최우선으로 사용한다 (plant_terms.resolve_target_crop_terms 참고).
    """
    openai_key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    
    if openai_key:
        try:
            from openai import OpenAI
            openai_client = OpenAI(api_key=openai_key, timeout=10.0, max_retries=0)
            
            # 1. OpenAI 임베딩 생성 (1536차원)
            res = openai_client.embeddings.create(
                input=[query],
                model="text-embedding-3-small"
            )
            query_vector = res.data[0].embedding
            
            # 2. Supabase RPC match_rag_chunks 호출
            response = session.supabase.rpc(
                "match_rag_chunks",
                {
                    "query_embedding": query_vector,
                    "match_threshold": VECTOR_MATCH_THRESHOLD,
                    "match_count": VECTOR_CANDIDATE_COUNT
                }
            ).execute()
            
            vector_results = []
            for item in response.data or []:
                content = item.get("content") or item.get("text") or ""
                if not content:
                    continue
                metadata = normalize_metadata(item)
                vector_results.append(SearchResult(
                    content=content,
                    metadata=metadata,
                    score=float(item.get("similarity") or item.get("score") or 0.0)
                ))
            keyword_results = supabase_keyword_search(query, top_k)
            if vector_results or keyword_results:
                merged_results = merge_results(keyword_results, vector_results, top_k=top_k)
                return filter_by_specific_terms(query, merged_results, target_crop_terms)[:top_k]
        except Exception as e:
            print(f"[RAG SEARCH WARNING] Supabase pgvector RPC 검색 중 오류 발생, Supabase keyword fallback 전환: {e}")

    supabase_results = supabase_keyword_search(query, top_k)
    if supabase_results:
        return filter_by_specific_terms(query, supabase_results, target_crop_terms)[:top_k]
    return filter_by_specific_terms(query, fallback_keyword_search(query, top_k), target_crop_terms)[:top_k]
