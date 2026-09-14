# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════
  طبقة مصدر البيانات — قابلة للتبديل (Google Sheet ⇄ Supabase)
═══════════════════════════════════════════════════════════════
انتقال تدريجي آمن: الخياران متاحان، تختار المصدر من الإعدادات.
- "sheet"    : يقرأ من Google Sheet (الحالي)
- "supabase" : يقرأ من جدول Supabase (الجديد)

كلاهما يُرجع نفس البنية: {df, tab_name, display_name, snapshot_time, ...}
═══════════════════════════════════════════════════════════════
"""
import json
import pandas as pd


def _get_sb():
    try:
        import server
        return server._get_supabase()
    except Exception:
        return None


def get_source_mode() -> str:
    """يقرأ مصدر البيانات المختار (sheet افتراضياً)."""
    sb = _get_sb()
    if sb:
        try:
            rows = sb.table("idx_settings").select("*").eq("key", "data_source").limit(1).execute().data
            if rows:
                val = rows[0].get("value") or {}
                if isinstance(val, str):
                    val = json.loads(val)
                return val.get("mode", "sheet")
        except Exception:
            pass
    return "sheet"


def set_source_mode(mode: str) -> dict:
    """يبدّل مصدر البيانات (sheet / supabase)."""
    if mode not in ("sheet", "supabase"):
        return {"ok": False, "error": "المصدر يجب أن يكون sheet أو supabase"}
    sb = _get_sb()
    if sb:
        try:
            sb.table("idx_settings").upsert(
                {"key": "data_source", "value": {"mode": mode}}, on_conflict="key").execute()
            return {"ok": True, "mode": mode}
        except Exception as e:
            return {"ok": False, "error": str(e)[:150]}
    return {"ok": False, "error": "Supabase غير متصل"}


# ═══════ القراءة الموحّدة ═══════

def fetch_latest_snapshot(sheet_url: str = None) -> dict:
    """
    يقرأ أحدث بيانات المؤشرات من المصدر المختار.
    يُرجع نفس البنية بغض النظر عن المصدر.
    """
    mode = get_source_mode()
    if mode == "supabase":
        return _fetch_from_supabase()
    else:
        # الافتراضي: Google Sheet (الدالة الأصلية)
        from sheets_reader import fetch_latest_snapshot as _sheet_fetch
        return _sheet_fetch(sheet_url)


def _fetch_from_supabase() -> dict:
    """
    يقرأ بيانات المؤشرات من جدول plus_sessions.
    - أحدث جلسة إغلاق (is_closing=true)
    - يفرد extra JSONB (كل الـ140 مؤشر بأسماء الشيت العربية)
    """
    from market_clock import now_riyadh
    sb = _get_sb()
    if not sb:
        raise RuntimeError("Supabase غير متصل")

    # ① أحدث جلسة إغلاق
    latest = sb.table("plus_sessions").select("session_name,session_ts") \
        .eq("is_closing", True) \
        .order("session_ts", desc=True).limit(1).execute().data
    if not latest:
        # احتياطي: أحدث جلسة بغض النظر عن الإغلاق
        latest = sb.table("plus_sessions").select("session_name,session_ts") \
            .order("session_ts", desc=True).limit(1).execute().data
    if not latest:
        raise RuntimeError("جدول plus_sessions فارغ")
    session = latest[0]["session_name"]
    session_ts = latest[0].get("session_ts")

    # ② كل صفوف هذه الجلسة
    rows = sb.table("plus_sessions").select("*") \
        .eq("session_name", session).limit(1000).execute().data or []
    if not rows:
        raise RuntimeError("الجلسة فارغة")

    # ③ أعِد بناء كل صف: الأعمدة المفتاحية + فرد extra بالكامل
    records = []
    for r in rows:
        extra = r.get("extra") or {}
        if isinstance(extra, str):
            try: extra = json.loads(extra)
            except: extra = {}
        # ابدأ بـ extra (كل المؤشرات) ثم أضف/اطغَ بالأعمدة المفتاحية بأسماء الشيت
        row = dict(extra)  # كل الـ140 مؤشر بأسماء الشيت العربية
        # الأعمدة المفتاحية بأسماء الشيت التي يتوقعها المحلّل
        row["الرمز"] = r.get("symbol", "")
        row["الاسم"] = r.get("name", "")
        if r.get("close") is not None:
            row.setdefault("آخر", r.get("close"))
        if r.get("change_pct") is not None:
            row.setdefault("التغير %", r.get("change_pct"))
        if r.get("net_liq") is not None:
            row.setdefault("صافي السيولة", r.get("net_liq"))
        if r.get("high") is not None:
            row.setdefault("أعلى", r.get("high"))
        if r.get("low") is not None:
            row.setdefault("أدنى", r.get("low"))
        if r.get("weekly_pct") is not None:
            row.setdefault("التغير الاسبوعي", r.get("weekly_pct"))
        if r.get("monthly_pct") is not None:
            row.setdefault("التغير الشهري", r.get("monthly_pct"))
        if r.get("yearly_pct") is not None:
            row.setdefault("التغير السنوي", r.get("yearly_pct"))
        records.append(row)

    df = pd.DataFrame(records)

    # ④ وقت الجلسة (من session_name أو session_ts)
    snap_time = None
    try:
        from datetime import datetime
        import re
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})[_ ](\d{2})[-:](\d{2})", str(session))
        if m:
            snap_time = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]))
        elif session_ts:
            snap_time = datetime.fromisoformat(str(session_ts).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        snap_time = now_riyadh().replace(tzinfo=None)

    display_name = str(session)
    return {"df": df, "tab_name": str(session), "display_name": display_name,
            "snapshot_time": snap_time, "all_tabs": [str(session)],
            "source": "supabase"}
