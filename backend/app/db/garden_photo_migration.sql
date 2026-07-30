-- 텃밭 상담 사진을 기존 사진 메타데이터 테이블에 안전하게 함께 저장한다.
ALTER TABLE public.plant_photos
    ALTER COLUMN plant_id DROP NOT NULL;

ALTER TABLE public.plant_photos
    ADD COLUMN IF NOT EXISTS garden_id UUID REFERENCES public.gardens(id) ON DELETE CASCADE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'plant_photos_exactly_one_owner'
          AND conrelid = 'public.plant_photos'::regclass
    ) THEN
        ALTER TABLE public.plant_photos
            ADD CONSTRAINT plant_photos_exactly_one_owner
            CHECK (num_nonnulls(plant_id, garden_id) = 1);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS plant_photos_garden_id_idx ON public.plant_photos(garden_id);

DROP POLICY IF EXISTS "Allow individual CRUD on plant photos through plant owner" ON public.plant_photos;
CREATE POLICY "Allow individual CRUD on plant photos through plant owner"
    ON public.plant_photos
    FOR ALL
    USING (
        (plant_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.plants
            WHERE public.plants.id = public.plant_photos.plant_id
              AND public.plants.user_id = auth.uid()
        ))
        OR
        (garden_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.gardens
            WHERE public.gardens.id = public.plant_photos.garden_id
              AND public.gardens.user_id = auth.uid()
        ))
    )
    WITH CHECK (
        (plant_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.plants
            WHERE public.plants.id = public.plant_photos.plant_id
              AND public.plants.user_id = auth.uid()
        ))
        OR
        (garden_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.gardens
            WHERE public.gardens.id = public.plant_photos.garden_id
              AND public.gardens.user_id = auth.uid()
        ))
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON public.plant_photos TO authenticated;
