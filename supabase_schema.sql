-- ═══════════════════════════════════════════════════════════════
--  متابعة المؤشرات — جداول Supabase
--  شغّل هذا الملف مرة واحدة في SQL Editor بـ Supabase
--  Dashboard → SQL Editor → New query → الصق → Run
-- ═══════════════════════════════════════════════════════════════

-- ① جدول التوصيات
CREATE TABLE IF NOT EXISTS idx_recommendations (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    name            TEXT,
    category        TEXT,                          -- short_term | long_term | speculative
    entry_price     FLOAT,
    target_price    FLOAT,
    target_pct      FLOAT DEFAULT 1.5,
    score           FLOAT DEFAULT 0,
    trend           TEXT,
    reason          TEXT,
    signals_summary JSONB DEFAULT '{}',
    top3_signals    JSONB DEFAULT '[]',
    appeared_date   DATE NOT NULL,
    expiry_date     DATE,                          -- صلاحية 3 أيام تداول
    max_expiry_date DATE,                          -- حد أقصى 5 أيام
    status          TEXT DEFAULT 'active',          -- active | closed
    outcome         TEXT,                          -- success | flat | failed
    highest_price   FLOAT,
    lowest_price    FLOAT,
    current_price   FLOAT,
    current_pct     FLOAT DEFAULT 0,
    peak_pct        FLOAT DEFAULT 0,
    closed_date     DATE,
    post_watch      BOOLEAN DEFAULT FALSE,         -- متابعة ما بعد الحسم
    post_watch_peak FLOAT DEFAULT 0,
    post_watch_hit  BOOLEAN DEFAULT FALSE,         -- حققت الهدف لاحقاً؟
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, appeared_date, category)
);

-- ② جدول اللقطات (سجل تاريخي لكل تحليل)
CREATE TABLE IF NOT EXISTS idx_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_time   TIMESTAMPTZ DEFAULT NOW(),
    source          TEXT,                          -- google_sheets | excel_upload
    tab_name        TEXT,
    market_status   TEXT,                          -- live | post_close | pre_open | weekend
    total_stocks    INT,
    market_mood     JSONB DEFAULT '{}',
    top_stocks      JSONB DEFAULT '[]',
    recommendations JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ③ جدول التقييمات (سجل تقييمات Claude)
CREATE TABLE IF NOT EXISTS idx_evaluations (
    id              BIGSERIAL PRIMARY KEY,
    eval_time       TIMESTAMPTZ DEFAULT NOW(),
    candidates_count INT,
    picks_count     INT,
    picks           JSONB DEFAULT '[]',
    rejected        JSONB DEFAULT '[]',
    market_note     TEXT,
    model           TEXT DEFAULT 'claude-sonnet-4-6',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ④ جدول أوزان التعلّم (يتحدّث مع تراكم البيانات)
CREATE TABLE IF NOT EXISTS idx_learned_weights (
    id              BIGSERIAL PRIMARY KEY,
    signal_name     TEXT UNIQUE NOT NULL,
    total_count     INT DEFAULT 0,
    success_count   INT DEFAULT 0,
    success_rate    FLOAT DEFAULT 0,
    weight          FLOAT DEFAULT 1.0,
    last_updated    TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════
--  الفهارس (لتسريع الاستعلامات)
-- ═══════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_rec_status
    ON idx_recommendations(status);

CREATE INDEX IF NOT EXISTS idx_rec_date
    ON idx_recommendations(appeared_date DESC);

CREATE INDEX IF NOT EXISTS idx_rec_symbol
    ON idx_recommendations(symbol);

CREATE INDEX IF NOT EXISTS idx_rec_category
    ON idx_recommendations(category);

CREATE INDEX IF NOT EXISTS idx_rec_outcome
    ON idx_recommendations(outcome);

CREATE INDEX IF NOT EXISTS idx_snap_time
    ON idx_snapshots(snapshot_time DESC);

CREATE INDEX IF NOT EXISTS idx_eval_time
    ON idx_evaluations(eval_time DESC);

-- ═══════════════════════════════════════════════════════════════
--  تفعيل RLS (اختياري — للأمان)
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE idx_recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE idx_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE idx_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE idx_learned_weights ENABLE ROW LEVEL SECURITY;

-- سياسة تسمح للـ service_role بكل شيء
CREATE POLICY "service_full_access" ON idx_recommendations
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "service_full_access" ON idx_snapshots
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "service_full_access" ON idx_evaluations
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "service_full_access" ON idx_learned_weights
    FOR ALL USING (true) WITH CHECK (true);
