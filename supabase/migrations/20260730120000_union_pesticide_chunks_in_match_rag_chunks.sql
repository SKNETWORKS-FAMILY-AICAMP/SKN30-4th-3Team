-- match_rag_chunks가 농약 청크 테이블(psis_pesticide_chunks)까지 함께 검색하도록 확장한다.
--
-- 배경
--   psis_pesticide_chunks(1,168행 / 작물 136종)는 rag_chunks와 동일한 임베딩 모델과
--   차원(text-embedding-3-small, vector(1536)) 및 metadata 규격으로 적재되어 있으나,
--   기존 RPC가 rag_chunks 단일 테이블만 조회해 RAG가 이 데이터를 한 행도 읽지 못했다.
--   ("토마토 응애 농약" 질의 시 토마토 문서 0건 → 근거 부족 폴백으로 응답)
--
-- 설계 결정
--   데이터를 rag_chunks로 복사하지 않고 RPC에서 UNION한다. 농약 등록정보는 갱신과
--   등록취소가 발생하는 데이터여서, 사본을 두면 낡은 사본이 "등록취소된 약제 추천"으로
--   이어질 수 있다. 단일 진실원천을 psis_pesticide_chunks로 유지한다.
--
-- 성능
--   테이블별로 먼저 (embedding <=> query_embedding) 순 정렬 + LIMIT match_count를 적용한
--   뒤 병합한다. 각 테이블의 벡터 인덱스를 그대로 활용할 수 있고, "각 테이블 상위 N개의
--   합집합에서 다시 상위 N개"는 전역 상위 N개와 동일한 결과를 준다.
--
-- 반환 컬럼(id/source_id/title/url/publisher/content/metadata/similarity)은 백엔드
--   vectorstore.search_documents가 의존하므로 이름과 순서를 바꾸지 않는다.
--
-- 롤백
--   이전 정의는 supabase/migrations/20260702100000_fix_match_rag_chunks_id_type.sql 에
--   그대로 있다. 해당 파일의 CREATE OR REPLACE FUNCTION 블록을 다시 실행하면 복구된다.

-- 농약 청크도 벡터 검색 대상이 되므로 코사인 거리 인덱스를 보장한다 (멱등).
CREATE INDEX IF NOT EXISTS psis_pesticide_chunks_embedding_hnsw
  ON public.psis_pesticide_chunks USING hnsw (embedding vector_cosine_ops);

DROP FUNCTION IF EXISTS public.match_rag_chunks(vector, float, int);

CREATE OR REPLACE FUNCTION public.match_rag_chunks (
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
RETURNS TABLE (
  id TEXT,
  source_id TEXT,
  title TEXT,
  url TEXT,
  publisher TEXT,
  content TEXT,
  metadata JSONB,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  WITH rag_top AS (
    SELECT
      rag_chunks.chunk_id::text                                              AS id,
      rag_chunks.source_id::text                                             AS source_id,
      COALESCE(rag_chunks.metadata->>'title', rag_sources.title)              AS title,
      COALESCE(rag_chunks.metadata->>'url', rag_sources.url)                  AS url,
      COALESCE(rag_chunks.metadata->>'publisher', rag_sources.publisher)      AS publisher,
      rag_chunks.text                                                        AS content,
      rag_chunks.metadata                                                    AS metadata,
      1 - (rag_chunks.embedding <=> query_embedding)                         AS similarity
    FROM public.rag_chunks
    JOIN public.rag_sources ON rag_sources.source_id = rag_chunks.source_id
    WHERE rag_chunks.embedding IS NOT NULL
    ORDER BY rag_chunks.embedding <=> query_embedding
    LIMIT match_count
  ),
  pesticide_top AS (
    -- rag_sources는 LEFT JOIN 한다. 출처 행이 없어도 농약 청크가 조용히 사라지지 않게
    -- 하고, 제목/URL/발행기관은 청크 자체의 metadata로 폴백한다.
    SELECT
      psis_pesticide_chunks.chunk_id::text                                   AS id,
      psis_pesticide_chunks.source_id::text                                  AS source_id,
      COALESCE(
        psis_pesticide_chunks.metadata->>'title',
        psis_pesticide_chunks.title,
        rag_sources.title
      )                                                                      AS title,
      COALESCE(psis_pesticide_chunks.metadata->>'url', rag_sources.url)       AS url,
      COALESCE(
        psis_pesticide_chunks.metadata->>'publisher',
        rag_sources.publisher
      )                                                                      AS publisher,
      psis_pesticide_chunks.text                                             AS content,
      psis_pesticide_chunks.metadata                                         AS metadata,
      1 - (psis_pesticide_chunks.embedding <=> query_embedding)              AS similarity
    FROM public.psis_pesticide_chunks
    LEFT JOIN public.rag_sources
      ON rag_sources.source_id = psis_pesticide_chunks.source_id
    WHERE psis_pesticide_chunks.embedding IS NOT NULL
    ORDER BY psis_pesticide_chunks.embedding <=> query_embedding
    LIMIT match_count
  ),
  merged AS (
    SELECT * FROM rag_top
    UNION ALL
    SELECT * FROM pesticide_top
  )
  SELECT
    merged.id,
    merged.source_id,
    merged.title,
    merged.url,
    merged.publisher,
    merged.content,
    merged.metadata,
    merged.similarity
  FROM merged
  WHERE merged.similarity > match_threshold
  ORDER BY merged.similarity DESC
  LIMIT match_count;
END;
$$;
