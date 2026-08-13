-- ═══════════════════════════════════════════════════════════════
--  عرض للقراءة فقط — ربط راصد بلس مع منصّة موحّدة
--  ينظّف الصيغ (رمز بلا لاحقة · وقت ISO مع منطقة زمنية · سعر خام)
--  شغّله مرة واحدة في Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW public.rasid_feed AS
SELECT
    -- رمز رقمي مجرّد بلا لاحقة (1120.0 → 1120)
    split_part(symbol, '.', 1)                         AS symbol,
    name                                               AS company_name,
    -- الوقت ISO 8601 كامل بمنطقة الرياض (+03:00)
    to_char(
        (created_at AT TIME ZONE 'Asia/Riyadh'),
        'YYYY-MM-DD"T"HH24:MI:SS"+03:00"'
    )                                                  AS first_seen,
    -- السعر الخام وقت الظهور (بدون هدف/مهلة محسوبين)
    entry_price                                        AS raw_entry_price,
    -- محرّك التوصية
    'algo+dual_ai'                                     AS engine,
    -- الثقة (المقياس موضّح بالحقل التالي)
    confidence                                         AS confidence,
    '0-10'                                             AS confidence_scale,
    -- مؤشرات إضافية مفيدة
    score                                              AS signal_score,
    category                                           AS horizon,
    weights_version                                    AS engine_version,
    -- حالة التحقق (اختيارية — المنصّة تتابع بنفسها)
    status                                             AS verify_status,
    outcome                                            AS verify_outcome,
    peak_pct                                           AS verify_peak_pct
FROM public.idx_recommendations
ORDER BY created_at DESC;

-- منح صلاحية القراءة فقط للدور المجهول (anon) — RLS
GRANT SELECT ON public.rasid_feed TO anon;
