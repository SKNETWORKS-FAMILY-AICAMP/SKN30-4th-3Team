-- 텃밭의 단일/여러 작물 구분과 대표 작물 정보를 추가한다.
ALTER TABLE public.gardens
    ADD COLUMN IF NOT EXISTS cultivation_type TEXT NOT NULL DEFAULT 'mixed',
    ADD COLUMN IF NOT EXISTS representative_crop TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'gardens_cultivation_type_check'
          AND conrelid = 'public.gardens'::regclass
    ) THEN
        ALTER TABLE public.gardens
            ADD CONSTRAINT gardens_cultivation_type_check
            CHECK (cultivation_type IN ('single', 'mixed'));
    END IF;
END $$;
