-- 1. 확장 기능 활성화 (RAG용 벡터 검색 지원)
CREATE EXTENSION
IF NOT EXISTS vector;
    -- 2. profiles (사용자 프로필 테이블)
    -- Supabase Auth의 auth.users 테이블과 1:1 관계를 가집니다.
    CREATE TABLE IF NOT EXISTS public.profiles
        (
            id UUID PRIMARY KEY REFERENCES auth.users(id) ON
            DELETE
                CASCADE                            ,
                updated_at TIMESTAMP WITH TIME ZONE,
                username TEXT UNIQUE               ,
                full_name TEXT                     ,
                avatar_url TEXT );
    -- 3. plants (사용자별 식물 프로필 테이블)
    CREATE TABLE IF NOT EXISTS public.gardens
        (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            location    TEXT,
            description TEXT,
            sunlight    TEXT,
            soil_type   TEXT,
            image_url   TEXT,
            cultivation_type TEXT NOT NULL DEFAULT 'mixed' CHECK (cultivation_type IN ('single', 'mixed')),
            representative_crop TEXT,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
        );
    CREATE TABLE IF NOT EXISTS public.plants
        (
            id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES public.profiles(id) ON
            DELETE
                CASCADE                         ,
                garden_id UUID REFERENCES public.gardens(id) ON DELETE SET NULL,
                name TEXT NOT NULL              ,
                species TEXT                    ,
                location TEXT                   ,
                sunlight TEXT                   ,
                health_score INTEGER,
                moisture TEXT                   ,
                next_task TEXT                  ,
                image_url TEXT                  ,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL );
    -- 4. care_logs (식물 재배/물주기 로그 테이블)
    CREATE TABLE IF NOT EXISTS public.care_logs
        (
            id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plant_id UUID NOT NULL REFERENCES public.plants(id) ON
            DELETE
                CASCADE            ,
                watered_at DATE    ,
                leaf_condition TEXT,
                soil_condition TEXT,
                memo TEXT          ,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL );
    -- 5. plant_photos (식물 사진 메타데이터 테이블)
    CREATE TABLE IF NOT EXISTS public.plant_photos
        (
            id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plant_id UUID REFERENCES public.plants(id) ON
            DELETE
                CASCADE,
                garden_id UUID REFERENCES public.gardens(id) ON
            DELETE
                CASCADE,
                storage_path TEXT NOT NULL                                                        ,
                note TEXT                                                                         ,
                captured_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
                CONSTRAINT plant_photos_exactly_one_owner CHECK (num_nonnulls(plant_id, garden_id) = 1) );
    -- 6. chat_sessions (식물별 AI 상담 세션 테이블)
    CREATE TABLE IF NOT EXISTS public.chat_sessions
        (
            id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES public.profiles(id) ON
            DELETE
                CASCADE,
                plant_id UUID REFERENCES public.plants(id) ON
            DELETE
            SET
                NULL,
                garden_id UUID REFERENCES public.gardens(id) ON
            DELETE
            SET
                NULL,
                title TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL );
    -- 7. chat_messages (상담 대화 이력 테이블)
    CREATE TABLE IF NOT EXISTS public.chat_messages
        (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id UUID NOT NULL REFERENCES public.chat_sessions(id) ON
            DELETE
                CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user',
                                                   'assistant')),
                content JSONB NOT NULL                              ,
                citations JSONB DEFAULT '[]'::jsonb                 , -- 출처 메타데이터 리스트
                created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL );
    -- 7.5. plant_catalog (식물 품종 도감 테이블)
    CREATE TABLE IF NOT EXISTS public.plant_catalog
    (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        species     TEXT NOT NULL,
        family_name TEXT,
        description TEXT,
        created_at  TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
    );
    -- 8. rag_sources (RAG용 공식 문서 출처 테이블)
    CREATE TABLE IF NOT EXISTS public.rag_sources
        (
            source_id    UUID PRIMARY KEY,
            title        TEXT NOT NULL   ,
            url          TEXT            ,
            publisher    TEXT            ,
            collected_at TIMESTAMP WITH TIME ZONE,
            created_at   TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
        )
    ;
    -- 9. rag_chunks (검색 가능한 문서 chunk와 embedding 테이블)
    CREATE TABLE IF NOT EXISTS public.rag_chunks
        (
            chunk_id UUID PRIMARY KEY,
            source_id UUID NOT NULL REFERENCES public.rag_sources(source_id) ON
            DELETE
                CASCADE                           ,
                text TEXT NOT NULL                ,
                embedding VECTOR(1536)            , -- OpenAI Embedding 차원(1536) 기준
                symptom_keywords TEXT[] DEFAULT ARRAY[]::TEXT[],
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL );
    -- 10. 벡터 인덱스 생성 (HNSW 인덱스를 사용하여 유사도 검색 속도 향상)
    -- OpenAI 임베딩 기본 유사도 지표인 코사인 거리를 기준으로 설정합니다.
    CREATE INDEX IF
    NOT EXISTS rag_chunks_embedding_idx ON public.rag_chunks USING hnsw
        (
            embedding vector_cosine_ops
        )
    ;

    -- 11. RAG 유사도 검색 RPC 함수
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
      SELECT
        rag_chunks.chunk_id::text AS id,
        rag_chunks.source_id::text AS source_id,
        rag_sources.title,
        rag_sources.url,
        rag_sources.publisher,
        rag_chunks.text AS content,
        rag_chunks.metadata,
        1 - (rag_chunks.embedding <=> query_embedding) AS similarity
      FROM public.rag_chunks
      JOIN public.rag_sources ON rag_sources.source_id = rag_chunks.source_id
      WHERE 1 - (rag_chunks.embedding <=> query_embedding) > match_threshold
      ORDER BY rag_chunks.embedding <=> query_embedding
      LIMIT match_count;
    END;
    $$;
    -- =========================================================================
    -- [유틸리티] Supabase Auth 회원가입 시 public.profiles 테이블 자동 생성 트리거
    -- =========================================================================
    CREATE
    OR
    REPLACE FUNCTION public.handle_new_user() RETURNS trigger AS $$
    BEGIN
        INSERT INTO public.profiles
            (
                id       ,
                username ,
                full_name,
                avatar_url
            )
        VALUES
            (
                new.id                              ,
                new.raw_user_meta_data->>'username' ,
                new.raw_user_meta_data->>'full_name',
                new.raw_user_meta_data->>'avatar_url'
            )
        ;
        RETURN new;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER;
    CREATE OR REPLACE TRIGGER on_auth_user_created
    AFTER INSERT
    ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
    -- =========================================================================
    -- [보안] Row Level Security (RLS) 설정 활성화
    -- =========================================================================
    ALTER TABLE public.profiles
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.gardens
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.plants
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.care_logs
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.plant_photos
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.chat_sessions
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.chat_messages
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.rag_sources
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.rag_chunks
        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.plant_catalog
        ENABLE ROW LEVEL SECURITY;
    -- 예시 보안 정책 (자신의 데이터만 보고 쓸 수 있도록 허용)
    -- 1) 프로필: 누구나 읽을 수 있고 자기 프로필만 수정 가능
    CREATE POLICY "Allow public read access to profiles" ON public.profiles FOR
    SELECT
    USING
        (true);
    CREATE POLICY "Allow individual update to own profile" ON public.profiles FOR
    UPDATE
    USING
        (auth.uid() = id);
    -- 2) 식물 정보: 본인 식물 데이터만 CRUD 가능
    CREATE POLICY "Allow individual CRUD on own gardens" ON public.gardens
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
    CREATE POLICY "Allow individual CRUD on own plants" ON public.plants USING (auth.uid() = user_id);
    -- 3) 재배 로그: 본인 식물의 로그만 CRUD 가능
    CREATE POLICY "Allow individual CRUD on care logs through plant owner" ON public.care_logs USING (EXISTS
    (
        SELECT
            1
        FROM
            public.plants
        WHERE
            public.plants.id      = public.care_logs.plant_id
        AND public.plants.user_id = auth.uid() ));
    -- 4) 사진: 본인 식물의 사진만 CRUD 가능
    CREATE POLICY "Allow individual CRUD on plant photos through plant owner" ON public.plant_photos USING (
        (plant_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.plants
            WHERE public.plants.id = public.plant_photos.plant_id
              AND public.plants.user_id = auth.uid()
        )) OR (garden_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.gardens
            WHERE public.gardens.id = public.plant_photos.garden_id
              AND public.gardens.user_id = auth.uid()
        ))
    ) WITH CHECK (
        (plant_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.plants
            WHERE public.plants.id = public.plant_photos.plant_id
              AND public.plants.user_id = auth.uid()
        )) OR (garden_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.gardens
            WHERE public.gardens.id = public.plant_photos.garden_id
              AND public.gardens.user_id = auth.uid()
        ))
    );
    -- 5) 채팅 세션: 본인 세션만 CRUD 가능
    CREATE POLICY "Allow individual CRUD on own chat sessions" ON public.chat_sessions USING (auth.uid() = user_id);
    -- 6) 채팅 메시지: 본인 세션의 메시지만 CRUD 가능
    CREATE POLICY "Allow individual CRUD on own chat messages through session" ON public.chat_messages USING (EXISTS
    (
        SELECT
            1
        FROM
            public.chat_sessions
        WHERE
            public.chat_sessions.id      = public.chat_messages.session_id
        AND public.chat_sessions.user_id = auth.uid() ));
    -- 7) RAG 출처 및 청크: 누구나 읽을 수 있지만 수정은 금지 (Select Only)
    CREATE POLICY "Allow public read access to rag_sources" ON public.rag_sources FOR
    SELECT
    USING
        (true);
    CREATE POLICY "Allow public read access to rag_chunks" ON public.rag_chunks FOR
    SELECT
    USING
        (true);
    CREATE POLICY "Allow public read access to plant_catalog" ON public.plant_catalog FOR
    SELECT
    USING
        (true);

    -- =========================================================================
    -- [권한] Supabase API 역할별 명시적 권한 (GRANT) 부여
    -- =========================================================================
    -- postgres, service_role 역할은 스키마 내 모든 권한 소유
    GRANT ALL ON ALL TABLES IN SCHEMA public TO postgres, service_role;
    GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO postgres, service_role;
    GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO postgres, service_role;

    -- 인증된 사용자(authenticated)는 테이블 CRUD 가능 (실제 데이터 접근 범위는 RLS가 제한)
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO authenticated;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO authenticated;

    -- 비인증 사용자(anon)는 조회만 가능
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO anon;
