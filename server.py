# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════
  متابعة المؤشرات — Index Tracker (خادم مستقل)
═══════════════════════════════════════════════════════════════════════
"""
import os, sys, traceback, json
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, HTTPException, Response, BackgroundTasks, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import tempfile, shutil

app = FastAPI(title="متابعة المؤشرات API")
WEB_DIR = _ROOT / "web"
_bg_status = {}
_config = {}  # runtime config cache


# ══════════════════════════════════════════════════════════════
#  إعدادات النظام
# ══════════════════════════════════════════════════════════════
def _load_config():
    """يحمّل الإعدادات من ملف config.json أو متغيرات البيئة."""
    global _config
    cfg_path = _ROOT / "config.json"
    if cfg_path.exists():
        try:
            _config = json.loads(cfg_path.read_text("utf-8"))
        except Exception:
            _config = {}
    # متغيرات البيئة تتفوق
    _config.setdefault("sheet_url", os.getenv("SHEET_URL", ""))
    _config.setdefault("claude_key", os.getenv("ANTHROPIC_API_KEY", ""))
    _config.setdefault("supabase_url", os.getenv("SUPABASE_URL", ""))
    _config.setdefault("supabase_key", os.getenv("SUPABASE_KEY", ""))
    _config.setdefault("sahmk_api_key", os.getenv("SAHMK_API_KEY", ""))
    return _config

_load_config()


def _get_supabase():
    """اتصال Supabase — يعمل فقط إذا الإعدادات موجودة."""
    url = _config.get("supabase_url")
    key = _config.get("supabase_key")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


@app.get("/api/health")
def health():
    from market_clock import classify_snapshot_time, now_riyadh
    try:
        sb = _get_supabase()
        return {
            "ok": True,
            "market_status": classify_snapshot_time(),
            "time_riyadh": now_riyadh().strftime("%H:%M"),
            "supabase": "✅" if sb else "❌ غير متصل",
            "sheet_url": "✅" if _config.get("sheet_url") else "❌",
            "claude_key": "✅" if _config.get("claude_key") else "❌",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════
#  الإعدادات — حفظ/قراءة
# ══════════════════════════════════════════════════════════════
class ConfigReq(BaseModel):
    sheet_url: str = ""
    claude_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""
    sahmk_api_key: str = ""


@app.get("/api/config")
def get_config():
    """يرجّع الإعدادات (مع إخفاء المفاتيح)."""
    return {
        "sheet_url": _config.get("sheet_url", ""),
        "claude_key": "•••" + _config.get("claude_key", "")[-8:] if _config.get("claude_key") else "",
        "supabase_url": _config.get("supabase_url", ""),
        "supabase_key": "•••" + _config.get("supabase_key", "")[-8:] if _config.get("supabase_key") else "",
        "sahmk_api_key": "•••" + _config.get("sahmk_api_key", "")[-6:] if _config.get("sahmk_api_key") else "",
    }


@app.post("/api/config")
def save_config(req: ConfigReq):
    """يحفظ الإعدادات في config.json."""
    global _config
    updates = req.dict()
    for k, v in updates.items():
        if v and not v.startswith("•••"):
            _config[k] = v
    cfg_path = _ROOT / "config.json"
    cfg_path.write_text(json.dumps(_config, ensure_ascii=False, indent=2), "utf-8")
    return {"ok": True, "message": "تم حفظ الإعدادات"}


# ══════════════════════════════════════════════════════════════
#  حالة المهام الخلفية
# ══════════════════════════════════════════════════════════════
@app.get("/api/bg-status/{task_name}")
def bg_status(task_name: str):
    return _bg_status.get(task_name, {"status": "unknown"})


# ══════════════════════════════════════════════════════════════
#  تحليل ملف إكسل مرفوع
# ══════════════════════════════════════════════════════════════
@app.post("/api/indicators/analyze")
async def indicators_analyze(file: UploadFile = File(...)):
    from indicator_analyzer import analyze_file
    try:
        suffix = Path(file.filename).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        result = analyze_file(tmp_path)
        for s in result.get("stocks", []):
            s.pop("frame_signals", None)
        for cat in result.get("recommendations", {}).values():
            for r in cat:
                r.pop("frame_signals", None)
        _last_analysis["data"] = result
        os.unlink(tmp_path)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
#  المُقيّم النهائي — حارس البوابة (Claude)
# ══════════════════════════════════════════════════════════════
_last_analysis = {}  # كاش آخر تحليل للتقييم

@app.post("/api/evaluate")
def evaluate_picks():
    """التقييم المزدوج — Claude + Gemini يناقشون ويختارون."""
    from dual_evaluator import dual_evaluate
    analysis = _last_analysis.get("data")
    if not analysis:
        raise HTTPException(status_code=400, detail="شغّل التحليل أولاً")
    try:
        result = dual_evaluate(analysis)
        # حفظ في Supabase
        sb = _get_supabase()
        if sb:
            try:
                sb.table("idx_evaluations").insert({
                    "candidates_count": result.get("candidates_count"),
                    "picks_count": len(result.get("picks", [])),
                    "picks": result.get("picks", []),
                    "rejected": result.get("rejected", []),
                    "market_note": result.get("market_note", ""),
                    "model": ",".join(result.get("models", [])),
                }).execute()
            except Exception:
                pass
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evaluate/learn")
def evaluate_learn():
    """Claude + Gemini يقيّمون النتائج ويعدّلون الاستراتيجية."""
    from dual_evaluator import evaluate_and_learn, save_strategy
    from tracker import performance_report
    sb = _get_supabase()
    try:
        perf = performance_report(sb)
        if perf.get("closed", 0) < 5:
            return {"skip": True, "reason": f"العينة صغيرة ({perf.get('closed',0)}/5) — انتظر المزيد"}
        result = evaluate_and_learn(perf)
        if result.get("save_strategy") and sb:
            save_result = save_strategy(result.get("new_weights", {}), result.get("target_recommendation", {}), sb)
            result["save_result"] = save_result
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
#  الأسعار اللحظية من سهمك
# ══════════════════════════════════════════════════════════════
@app.get("/api/prices/{symbol}")
def get_price(symbol: str):
    from price_feed import fetch_price
    return fetch_price(symbol)


@app.post("/api/prices/bulk")
def get_prices_bulk(symbols: list[str] = None):
    """أسعار مجموعة أسهم — يستخدمها تحديث التوصيات."""
    from price_feed import fetch_prices_bulk
    if not symbols:
        # جلب رموز التوصيات النشطة
        sb = _get_supabase()
        if sb:
            try:
                active = sb.table("idx_recommendations").select("symbol").eq("status", "active").execute().data or []
                symbols = list(set(r["symbol"] for r in active))
            except Exception:
                symbols = []
    if not symbols:
        return {"prices": {}, "note": "لا توجد رموز"}
    return {"prices": fetch_prices_bulk(symbols), "count": len(symbols)}


@app.post("/api/recommendations/update")
def recommendations_update():
    """يحدّث التوصيات — يستخدم أسعار سهمك اللحظية أولاً، ثم الشيت."""
    from tracker import update_active, update_post_watch
    from price_feed import fetch_prices_bulk
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        # جلب رموز التوصيات النشطة
        active = sb.table("idx_recommendations").select("symbol").eq("status", "active").execute().data or []
        symbols = list(set(r["symbol"] for r in active))
        # محاولة 1: أسعار سهمك اللحظية
        prices = {}
        if symbols and os.getenv("SAHMK_API_KEY"):
            prices = fetch_prices_bulk(symbols)
        # محاولة 2: من الشيت
        if len(prices) < len(symbols):
            sheet_prices = _get_current_prices()
            for s in symbols:
                if s not in prices and s in sheet_prices:
                    prices[s] = sheet_prices[s]
        active_result = update_active(prices, sb)
        post_result = update_post_watch(prices, sb)
        return {"active": active_result, "post_watch": post_result,
                "price_source": "سهمك" if os.getenv("SAHMK_API_KEY") else "الشيت",
                "prices_found": len(prices)}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
#  قراءة Google Sheets + تحليل
# ══════════════════════════════════════════════════════════════
@app.post("/api/sheets/fetch")
def sheets_fetch(url: str = None):
    """يقرأ آخر لقطة من Google Sheets ويحلّلها."""
    from sheets_reader import fetch_latest_snapshot, extract_sheet_id
    from indicator_analyzer import analyze_dataframe
    from market_clock import classify_snapshot_time

    sheet_url = url or _config.get("sheet_url")
    if not sheet_url:
        raise HTTPException(status_code=400, detail="لم يتم تحديد رابط Google Sheets")

    try:
        snap = fetch_latest_snapshot(sheet_url)
        df = snap["df"]
        result = analyze_dataframe(df)
        result["source"] = {
            "type": "google_sheets",
            "tab": snap["tab_name"],
            "snapshot_time": snap["snapshot_time"].isoformat() if snap["snapshot_time"] else None,
            "all_tabs": snap["all_tabs"],
            "market_status": classify_snapshot_time(),
        }
        _last_analysis["data"] = result
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sheets/tabs")
def sheets_tabs(url: str = None):
    """يرجّع قائمة تبويبات الشيت."""
    from sheets_reader import extract_sheet_id, list_sheet_tabs
    sheet_url = url or _config.get("sheet_url")
    if not sheet_url:
        raise HTTPException(status_code=400, detail="لم يتم تحديد رابط Google Sheets")
    try:
        sid = extract_sheet_id(sheet_url)
        tabs = list_sheet_tabs(sid)
        return {"sheet_id": sid, "tabs": tabs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sheets/analyze-tab")
def sheets_analyze_tab(tab: str, url: str = None):
    """يحلّل تبويب محدد من الشيت."""
    from sheets_reader import extract_sheet_id, read_tab
    from indicator_analyzer import analyze_dataframe
    sheet_url = url or _config.get("sheet_url")
    if not sheet_url:
        raise HTTPException(status_code=400, detail="لم يتم تحديد رابط الشيت")
    try:
        sid = extract_sheet_id(sheet_url)
        df = read_tab(sid, tab)
        return analyze_dataframe(df)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
#  التوصيات — إنشاء + تتبع + أداء
# ══════════════════════════════════════════════════════════════
@app.post("/api/recommendations/generate")
def recommendations_generate(url: str = None):
    """يحلّل آخر لقطة ويولّد توصيات ويحفظها."""
    from sheets_reader import fetch_latest_snapshot
    from indicator_analyzer import analyze_dataframe
    from tracker import create_recommendation

    sheet_url = url or _config.get("sheet_url")
    if not sheet_url:
        raise HTTPException(status_code=400, detail="حدد رابط الشيت أولاً")

    sb = _get_supabase()
    try:
        snap = fetch_latest_snapshot(sheet_url)
        result = analyze_dataframe(snap["df"])
        recs = result.get("recommendations", {})
        saved = {"short_term": 0, "long_term": 0, "speculative": 0}

        for cat in ["short_term", "long_term", "speculative"]:
            for stock in recs.get(cat, []):
                create_recommendation(stock, cat, sb)
                saved[cat] += 1

        return {"ok": True, "saved": saved, "total": sum(saved.values())}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recommendations")
def list_recommendations(status: str = None, category: str = None, limit: int = 200):
    """يعرض التوصيات مع فلترة."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        q = sb.table("idx_recommendations").select("*") \
            .order("appeared_date", desc=True).limit(limit)
        if status:
            q = q.eq("status", status)
        if category:
            q = q.eq("category", category)
        rows = q.execute().data or []
        return {"count": len(rows), "results": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recommendations/performance")
def recommendations_performance():
    """تقرير أداء التوصيات."""
    from tracker import performance_report
    sb = _get_supabase()
    try:
        return performance_report(sb)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_current_prices() -> dict:
    """يجلب أسعار حالية — من الشيت أو من السوق."""
    prices = {}
    # محاولة 1: من Google Sheets
    try:
        from sheets_reader import fetch_latest_snapshot
        sheet_url = _config.get("sheet_url")
        if sheet_url:
            snap = fetch_latest_snapshot(sheet_url)
            df = snap["df"]
            sym_col = None
            price_col = None
            for c in df.columns:
                if "الرمز" in str(c):
                    sym_col = c
                if "آخر" in str(c) or "السعر" in str(c):
                    price_col = c
            if sym_col and price_col:
                for _, row in df.iterrows():
                    try:
                        prices[str(row[sym_col])] = float(row[price_col])
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass
    return prices


# ══════════════════════════════════════════════════════════════
#  Supabase Schema — إنشاء الجداول
# ══════════════════════════════════════════════════════════════
@app.post("/api/setup/tables")
def setup_tables():
    """ينشئ جداول النظام في Supabase (يُشغّل مرة واحدة)."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل — أضف الإعدادات أولاً")

    sql = """
    CREATE TABLE IF NOT EXISTS idx_recommendations (
        id BIGSERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        name TEXT,
        category TEXT,
        entry_price FLOAT,
        target_price FLOAT,
        target_pct FLOAT DEFAULT 3.0,
        score FLOAT DEFAULT 0,
        trend TEXT,
        reason TEXT,
        signals_summary JSONB DEFAULT '{}',
        appeared_date DATE NOT NULL,
        expiry_date DATE,
        max_expiry_date DATE,
        status TEXT DEFAULT 'active',
        outcome TEXT,
        highest_price FLOAT,
        lowest_price FLOAT,
        peak_pct FLOAT DEFAULT 0,
        closed_date DATE,
        post_watch BOOLEAN DEFAULT FALSE,
        post_watch_peak FLOAT DEFAULT 0,
        post_watch_hit BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(symbol, appeared_date, category)
    );

    CREATE TABLE IF NOT EXISTS idx_snapshots (
        id BIGSERIAL PRIMARY KEY,
        snapshot_time TIMESTAMPTZ DEFAULT NOW(),
        source TEXT,
        tab_name TEXT,
        market_status TEXT,
        total_stocks INT,
        market_mood JSONB DEFAULT '{}',
        top_stocks JSONB DEFAULT '[]',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_rec_status ON idx_recommendations(status);
    CREATE INDEX IF NOT EXISTS idx_rec_date ON idx_recommendations(appeared_date DESC);
    """
    try:
        sb.postgrest.rpc("exec_sql", {"sql": sql}).execute()
        return {"ok": True, "message": "تم إنشاء الجداول"}
    except Exception:
        # Supabase قد لا يدعم RPC مباشرة — ننشئ بطريقة بديلة
        # نحاول insert فارغ لاختبار وجود الجدول
        try:
            sb.table("idx_recommendations").select("id").limit(1).execute()
            return {"ok": True, "message": "الجداول موجودة بالفعل"}
        except Exception as e:
            return {
                "ok": False,
                "message": "أنشئ الجداول يدوياً من SQL Editor في Supabase",
                "sql": sql,
                "error": str(e)[:200],
            }


# ══════════════════════════════════════════════════════════════
#  Static Files
# ══════════════════════════════════════════════════════════════
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print("=" * 50)
    print("  📡 متابعة المؤشرات — Index Tracker")
    print(f"  http://localhost:{port}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=port)
