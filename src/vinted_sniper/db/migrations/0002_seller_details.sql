-- The seller's id and review count. The id is what a notification needs to link the
-- seller's profile; the review count is what makes a rating readable ("4.8 from 3
-- reviews" and "4.8 from 300" are different offers). Rows written before this migration
-- keep NULLs and simply render without a seller link or count.
ALTER TABLE items ADD COLUMN seller_id INTEGER;
ALTER TABLE items ADD COLUMN seller_feedback_count INTEGER;
