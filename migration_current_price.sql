-- ═══════════════════════════════════════════════════════════════
--  ترقية: إضافة عمود السعر الحالي
--  شغّل هذا في SQL Editor بـ Supabase (مرة واحدة)
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE idx_recommendations
    ADD COLUMN IF NOT EXISTS current_price FLOAT,
    ADD COLUMN IF NOT EXISTS current_pct FLOAT DEFAULT 0;
