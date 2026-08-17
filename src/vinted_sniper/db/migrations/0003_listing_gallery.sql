-- Everything the dashboard's listing cards show beyond the cover photo. The catalog
-- response already carries the full photo set and the counters, so keeping them costs
-- no extra requests. Rows written before this migration keep NULLs and render with the
-- cover photo alone.
ALTER TABLE items ADD COLUMN photo_urls_json TEXT;
ALTER TABLE items ADD COLUMN favourite_count INTEGER;
ALTER TABLE items ADD COLUMN view_count INTEGER;
