-- ═══════════════════════════════════════════════════════════════
--  ترقية: إضافة عمود السعر الحالي
--  شغّل هذا في SQL Editor بـ Supabase (مرة واحدة)
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE idx_recommendations
    ADD COLUMN IF NOT EXISTS current_price FLOAT,
    ADD COLUMN IF NOT EXISTS current_pct FLOAT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS post_target_high FLOAT,
    ADD COLUMN IF NOT EXISTS post_target_pct FLOAT DEFAULT 0;
