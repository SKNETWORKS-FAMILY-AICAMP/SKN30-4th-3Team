-- 텃밭 단위 AI 상담 세션을 저장하기 위한 비파괴 마이그레이션.
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS garden_id UUID REFERENCES public.gardens(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS chat_sessions_garden_id_idx
    ON public.chat_sessions(garden_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.chat_sessions TO authenticated;
