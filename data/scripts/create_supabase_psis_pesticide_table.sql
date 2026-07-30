-- PSIS 농약 그룹 적재용 Supabase 테이블 생성 SQL
--
-- 포함:
--   1. psis_pesticide_chunks 테이블
--   2. 정확 검색 및 벡터 검색을 위한 인덱스
--   3. 읽기 전용 RLS 정책과 역할별 권한
--
-- 제외:
--   - 자연어 벡터 검색 RPC(match_psis_pesticide_chunks)
--   - 백엔드 검색 및 LangGraph 로직
--
-- 적용 방법:
--   Supabase Dashboard > SQL Editor에서 팀 검토 후 실행합니다.
--   실행 전에 public.rag_sources와 vector 확장이 존재해야 합니다.

BEGIN;

CREATE TABLE IF NOT EXISTS public.psis_pesticide_chunks
(
    -- 임베딩 청크의 결정적 UUID
    chunk_id UUID PRIMARY KEY,

    -- 기존 rag_sources에 등록할 PSIS 출처 UUID
    source_id TEXT NOT NULL
        REFERENCES public.rag_sources(source_id)
        ON DELETE CASCADE,

    -- normalized 문서 및 PSIS 그룹 추적용 고유키
    doc_id TEXT NOT NULL UNIQUE,
    group_key TEXT NOT NULL UNIQUE,

    -- 검색 결과 표시용 제목
    title TEXT NOT NULL,

    -- 작물 및 대상 병해충 정확 검색용 필드
    crop_code TEXT,
    crop_name TEXT NOT NULL,
    target_disease_pest TEXT NOT NULL,
    use_type TEXT,

    -- 자연어 검색용 본문과 1,536차원 OpenAI 임베딩
    text TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL,

    -- 제품명 및 유효성분 정확 검색용 배열
    pesticide_names TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    active_ingredients TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],

    -- 제품별 사용법·안전정보 원본
    registration_count INTEGER NOT NULL DEFAULT 0
        CHECK (registration_count >= 0),
    registrations JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(registrations) = 'array'),

    -- 기존 RAG 검색 결과 형식과의 호환용 metadata
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    collected_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE
        NOT NULL DEFAULT timezone('utc'::TEXT, now())
);

-- 자연어 벡터 검색 성능을 위한 코사인 HNSW 인덱스입니다.
-- 실제 RPC와 백엔드 검색 로직은 백엔드 담당자가 별도로 구현합니다.
CREATE INDEX IF NOT EXISTS psis_pesticide_chunks_embedding_idx
    ON public.psis_pesticide_chunks
    USING hnsw (embedding vector_cosine_ops);

-- 작물 전체 등록 농약 조회:
-- WHERE crop_name = '딸기'
CREATE INDEX IF NOT EXISTS psis_pesticide_chunks_crop_name_idx
    ON public.psis_pesticide_chunks (crop_name);

-- 작물 + 병해충 등록 농약 조회:
-- WHERE crop_name = '딸기' AND target_disease_pest = '흰가루병'
CREATE INDEX IF NOT EXISTS psis_pesticide_chunks_crop_target_idx
    ON public.psis_pesticide_chunks (crop_name, target_disease_pest);

-- 제품명 배열 포함 검색:
-- WHERE pesticide_names @> ARRAY['제품명']
CREATE INDEX IF NOT EXISTS psis_pesticide_chunks_pesticide_names_idx
    ON public.psis_pesticide_chunks
    USING gin (pesticide_names);

-- 유효성분 배열 포함 검색:
-- WHERE active_ingredients @> ARRAY['성분명']
CREATE INDEX IF NOT EXISTS psis_pesticide_chunks_active_ingredients_idx
    ON public.psis_pesticide_chunks
    USING gin (active_ingredients);

ALTER TABLE public.psis_pesticide_chunks ENABLE ROW LEVEL SECURITY;

-- 이 migration을 재적용할 때 정책 이름 충돌을 방지합니다.
DROP POLICY IF EXISTS "Allow public read access to psis_pesticide_chunks"
    ON public.psis_pesticide_chunks;

-- 기존 rag_chunks처럼 읽기는 허용하고, 쓰기는 service_role 적재 코드로 제한합니다.
CREATE POLICY "Allow public read access to psis_pesticide_chunks"
    ON public.psis_pesticide_chunks
    FOR SELECT
    USING (true);

GRANT SELECT ON public.psis_pesticide_chunks TO anon, authenticated;
GRANT ALL ON public.psis_pesticide_chunks TO postgres, service_role;

COMMIT;
