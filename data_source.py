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
    يقرأ بيانات المؤشرات من جدول Supabase.
    الجدول المتوقّع: idx_market_data (صف لكل سهم، أحدث لقطة).
    العمود snapshot_batch يحدّد اللقطة (التاريخ_الوقت).
    """
    from market_clock import now_riyadh
    sb = _get_sb()
    if not sb:
        raise RuntimeError("Supabase غير متصل")

    # أحدث لقطة (batch)
    latest = sb.table("idx_market_data").select("snapshot_batch") \
        .order("snapshot_batch", desc=True).limit(1).execute().data
    if not latest:
        raise RuntimeError("لا توجد بيانات مؤشرات في Supabase (جدول idx_market_data فارغ)")
    batch = latest[0]["snapshot_batch"]

    # كل صفوف هذه اللقطة
    rows = sb.table("idx_market_data").select("*") \
        .eq("snapshot_batch", batch).limit(1000).execute().data or []
    if not rows:
        raise RuntimeError("اللقطة فارغة")

    # حوّل لـ DataFrame (نفس شكل الشيت)
    # عمود data JSONB يحمل كل أعمدة المؤشرات كما هي
    records = []
    for r in rows:
        d = r.get("data") or {}
        if isinstance(d, str):
            try: d = json.loads(d)
            except: d = {}
        records.append(d)
    df = pd.DataFrame(records)

    # وقت اللقطة
    snap_time = None
    try:
        from datetime import datetime
        snap_time = datetime.fromisoformat(str(batch).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        snap_time = now_riyadh().replace(tzinfo=None)

    display_name = snap_time.strftime("%Y-%m-%d_%H-%M") if snap_time else str(batch)
    return {"df": df, "tab_name": str(batch), "display_name": display_name,
            "snapshot_time": snap_time, "all_tabs": [str(batch)],
            "source": "supabase"}
