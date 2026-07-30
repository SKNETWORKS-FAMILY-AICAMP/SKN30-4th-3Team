-- =============================================================================
-- plant_catalog.aliases 컬럼 추가
--   국명 외 이명(별칭)을 배열로 보관한다. 국립수목원 국가표준식물목록(KPNI) 기반
--   정규화에서 표준국명↔이명(예: 도라지↔길경, 토마토↔방울토마토)을 확보하며,
--   백엔드 plant_terms 사전이 이 값을 읽어 RAG 크롭 스코핑 용어/별칭으로 확장한다.
--   (rag_chunks.crop_or_plant 와 동일하게 text[] 사용)
-- =============================================================================
ALTER TABLE public.plant_catalog
  ADD COLUMN IF NOT EXISTS aliases text[] NOT NULL DEFAULT '{}';
