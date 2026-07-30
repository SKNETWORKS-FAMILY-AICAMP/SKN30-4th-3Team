-- 내 텃밭 기능용 비파괴 마이그레이션.
-- 기존 plants 및 사용자 데이터는 유지하고 텃밭 테이블/연결 컬럼만 추가한다.

CREATE TABLE IF NOT EXISTS public.gardens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    location    TEXT,
    description TEXT,
    sunlight    TEXT,
    soil_type   TEXT,
    image_url   TEXT,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.plants
    ADD COLUMN IF NOT EXISTS garden_id UUID REFERENCES public.gardens(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS gardens_user_id_idx ON public.gardens(user_id);
CREATE INDEX IF NOT EXISTS plants_garden_id_idx ON public.plants(garden_id);

ALTER TABLE public.gardens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow individual CRUD on own gardens" ON public.gardens;
CREATE POLICY "Allow individual CRUD on own gardens"
    ON public.gardens
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.gardens TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.plants TO authenticated;
