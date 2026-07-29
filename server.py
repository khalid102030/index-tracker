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
    _config.setdefault("sheet_url", os.getenv("SHEET_URL", "https://docs.google.com/spreadsheets/d/1Gqhre_LvpzF0vUpIQSzONDXFeMD-R5zql84zctv9wR8/edit"))
    _config.setdefault("claude_key", os.getenv("ANTHROPIC_API_KEY", ""))
    _config.setdefault("supabase_url", os.getenv("SUPABASE_URL", ""))
    _config.setdefault("supabase_key", os.getenv("SUPABASE_KEY", ""))
    _config.setdefault("sahmk_api_key", os.getenv("SAHMK_API_KEY", ""))
    _config.setdefault("gemini_key", os.getenv("GEMINI_API_KEY", ""))
    # مزامنة المفاتيح مع متغيرات البيئة لتستخدمها الوحدات الأخرى
    if _config.get("claude_key"): os.environ["ANTHROPIC_API_KEY"] = _config["claude_key"]
    if _config.get("gemini_key"): os.environ["GEMINI_API_KEY"] = _config["gemini_key"]
    if _config.get("sahmk_api_key"): os.environ["SAHMK_API_KEY"] = _config["sahmk_api_key"]
    if _config.get("sheet_url"): os.environ["SHEET_URL"] = _config["sheet_url"]
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
    gemini_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""
    sahmk_api_key: str = ""


@app.get("/api/config")
def get_config():
    """يرجّع الإعدادات (مع إخفاء المفاتيح)."""
    return {
        "sheet_url": _config.get("sheet_url", ""),
        "claude_key": "•••" + _config.get("claude_key", "")[-8:] if _config.get("claude_key") else "",
        "gemini_key": "•••" + _config.get("gemini_key", "")[-6:] if _config.get("gemini_key") else "",
        "supabase_url": _config.get("supabase_url", ""),
        "supabase_key": "•••" + _config.get("supabase_key", "")[-8:] if _config.get("supabase_key") else "",
        "sahmk_api_key": "•••" + _config.get("sahmk_api_key", "")[-6:] if _config.get("sahmk_api_key") else "",
    }


@app.post("/api/config")
def save_config(req: ConfigReq):
    """يحفظ الإعدادات + يزامنها مع متغيرات البيئة."""
    global _config
    updates = req.dict()
    for k, v in updates.items():
        if v and not v.startswith("•••"):
            _config[k] = v
    # مزامنة مع البيئة فوراً
    env_map = {"claude_key": "ANTHROPIC_API_KEY", "gemini_key": "GEMINI_API_KEY",
               "sahmk_api_key": "SAHMK_API_KEY", "sheet_url": "SHEET_URL",
               "supabase_url": "SUPABASE_URL", "supabase_key": "SUPABASE_KEY"}
    for ck, ek in env_map.items():
        if _config.get(ck):
            os.environ[ek] = _config[ck]
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

def _analyze_movers(analysis: dict, sb) -> dict:
    """
    يشرّح حركة السوق: أي الأسهم ارتفعت، وهل رشّحناها أم فاتتنا؟
    ويقيّم قرارات الرفض السابقة: هل كان الرفض سليماً؟
    """
    lesson = {"caught": [], "missed": [], "note": "", "rejection_review": []}
    try:
        stocks = analysis.get("stocks", [])
        if not stocks:
            return lesson
        stock_map = {s["symbol"]: s for s in stocks}

        # أعلى المرتفعين اليوم
        risers = sorted([s for s in stocks if s.get("change_pct", 0) >= 2.5],
                        key=lambda x: x.get("change_pct", 0), reverse=True)[:10]

        recommended_syms = set()
        if sb:
            try:
                recent = sb.table("idx_recommendations").select("symbol") \
                    .order("created_at", desc=True).limit(50).execute().data or []
                recommended_syms = {r["symbol"] for r in recent}
            except Exception:
                pass

        for s in risers:
            sym = s["symbol"]
            info = {
                "symbol": sym, "name": s.get("name", ""),
                "change_pct": s.get("change_pct", 0), "weekly": s.get("weekly_change", 0),
                "rsi": s.get("rsi", 0), "rsi_state": s.get("rsi_state", ""),
                "bet_score": s.get("bet_score", 0), "total_active": s.get("total_active", 0),
                "signals": s.get("top3_signals", []), "trend": s.get("trend", ""),
            }
            if sym in recommended_syms:
                lesson["caught"].append(info)
            else:
                lesson["missed"].append(info)

        n_caught, n_missed = len(lesson["caught"]), len(lesson["missed"])
        if risers:
            lesson["note"] = f"من {len(risers)} مرتفع اليوم: رصدنا {n_caught}، فاتنا {n_missed}"

        # تقييم قرارات الرفض السابقة (المخزّنة)
        if sb:
            try:
                evals = sb.table("idx_evaluations").select("rejected,eval_time") \
                    .order("eval_time", desc=True).limit(3).execute().data or []
                for ev in evals:
                    for rej in (ev.get("rejected") or []):
                        rsym = rej.get("symbol")
                        cur = stock_map.get(rsym)
                        if cur:
                            chg = cur.get("change_pct", 0)
                            # قرار الرفض: سليم لو السهم ما ارتفع، خاطئ لو ارتفع قوي
                            verdict = "خاطئ ✗" if chg >= 2.5 else "سليم ✓" if chg <= 0.5 else "محايد"
                            lesson["rejection_review"].append({
                                "symbol": rsym, "reason": rej.get("reason", ""),
                                "change_now": chg, "verdict": verdict,
                            })
            except Exception:
                pass
    except Exception:
        pass
    return lesson


@app.post("/api/analyze-full")
def analyze_full(url: str = None):
    """
    الزر الواحد: يسحب البيانات → يحدّث النتائج السابقة →
    Claude + Gemini يقيّمون مع مراعاة الأداء → يحفظ التوصيات.
    كل شي في خطوة واحدة بدون مراحل.
    """
    from sheets_reader import fetch_latest_snapshot
    from indicator_analyzer import analyze_dataframe
    from market_clock import classify_snapshot_time
    from dual_evaluator import dual_evaluate
    from tracker import create_recommendation, update_active, update_post_watch, performance_report
    from price_feed import fetch_prices_bulk

    sheet_url = url or _config.get("sheet_url")
    if not sheet_url:
        raise HTTPException(status_code=400, detail="حدد رابط الشيت في الإعدادات")

    sb = _get_supabase()

    try:
        # ① سحب وتحليل
        snap = fetch_latest_snapshot(sheet_url)
        analysis = analyze_dataframe(snap["df"])
        analysis["source"] = {"tab": snap["tab_name"],
                              "market_status": classify_snapshot_time()}
        _last_analysis["data"] = analysis

        # ② تحديث النتائج السابقة (قبل التقييم الجديد)
        prev_picks = []
        performance = None
        if sb:
            try:
                # أسعار حالية لتحديث التوصيات القديمة (مع أعلى سعر اليوم)
                active = sb.table("idx_recommendations").select("*").eq("status", "active").execute().data or []
                prev_picks = [{"symbol": r["symbol"], "confidence": r.get("score", 0)} for r in active]
                symbols = list(set(r["symbol"] for r in active))
                prices = {}
                if symbols and os.getenv("SAHMK_API_KEY"):
                    from price_feed import fetch_prices_full
                    prices = fetch_prices_full(symbols)
                if len(prices) < len(symbols):
                    sheet_full = _get_current_prices_full()
                    for s in symbols:
                        if s not in prices and s in sheet_full:
                            prices[s] = sheet_full[s]
                update_active(prices, sb)
                update_post_watch(prices, sb)
                performance = performance_report(sb)
            except Exception:
                pass

        # ③ تشريح حركة أمس: ماذا ارتفع؟ هل رشّحناه أم فاتنا؟
        movers_lesson = _analyze_movers(analysis, sb)

        # ④ التقييم المزدوج (مع الأداء + السابق + دروس الحركة)
        eval_result = dual_evaluate(analysis, performance, prev_picks, movers_lesson)

        # ④ حفظ التوصيات الجديدة
        saved = 0
        if sb and eval_result.get("picks"):
            for pick in eval_result["picks"]:
                orig = next((s for s in analysis["stocks"] if s["symbol"] == pick["symbol"]), {})
                merged = {**orig, **pick, "reason": pick.get("reasoning", "")}
                # الاستراتيجية الأساسية دائماً قصيرة المدى (هدف 1.5% بأيام)
                # المدى البعيد قسم منفصل تماماً لا يُحفظ هنا
                cat = "short_term"
                create_recommendation(merged, cat, sb)
                saved += 1

        # حفظ التقييم (مع المرفوضين) لمراجعة القرارات لاحقاً
        if sb:
            try:
                sb.table("idx_evaluations").insert({
                    "candidates_count": eval_result.get("candidates_count", 0),
                    "picks_count": len(eval_result.get("picks", [])),
                    "picks": eval_result.get("picks", []),
                    "rejected": eval_result.get("rejected", []),
                    "market_note": eval_result.get("market_note", ""),
                    "model": ",".join(eval_result.get("models", [])),
                }).execute()
            except Exception:
                pass

        # ── التعلّم التلقائي: كل 10 نتائج محسومة، يعيد تقييم الاستراتيجية ──
        learn_note = None
        if sb and performance and performance.get("closed", 0) >= 10:
            try:
                from dual_evaluator import evaluate_and_learn, save_strategy
                # يتعلّم مرة كل 10 نتائج جديدة (نتحقق من عدّاد بسيط)
                closed_n = performance.get("closed", 0)
                last_learn = _last_analysis.get("last_learn_at", 0)
                if closed_n - last_learn >= 10:
                    lr = evaluate_and_learn(performance)
                    if lr.get("new_weights"):
                        save_strategy(lr["new_weights"], lr.get("target_recommendation", {}), sb)
                        _last_analysis["last_learn_at"] = closed_n
                        learn_note = lr.get("claude_assessment", "تم تحديث الاستراتيجية")
            except Exception:
                pass

        return {
            "ok": True,
            "tab": snap["tab_name"],
            "total_stocks": analysis["summary"]["total_stocks"],
            "market_mood": analysis["summary"]["market_mood"],
            "picks": eval_result.get("picks", []),
            "market_note": eval_result.get("market_note", ""),
            "consensus_note": eval_result.get("consensus_note", ""),
            "rejected": eval_result.get("rejected", []),
            "saved": saved,
            "models": eval_result.get("models", []),
            "performance": performance,
            "learn_note": learn_note,
            "missed_pattern": eval_result.get("missed_pattern", ""),
            "movers_note": eval_result.get("movers_note", ""),
            "rejection_review": movers_lesson.get("rejection_review", []),
        }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evaluate")
def evaluate_picks():
    """التقييم المزدوج فقط — Claude + Gemini (بدون سحب جديد)."""
    from dual_evaluator import dual_evaluate
    from tracker import performance_report
    analysis = _last_analysis.get("data")
    if not analysis:
        raise HTTPException(status_code=400, detail="شغّل التحليل أولاً")
    try:
        sb = _get_supabase()
        perf = performance_report(sb) if sb else None
        result = dual_evaluate(analysis, perf)
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
    """يحدّث التوصيات — أسعار سهمك (مع أعلى سعر اليوم) أولاً، ثم الشيت."""
    from tracker import update_active, update_post_watch
    from price_feed import fetch_prices_full
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        active = sb.table("idx_recommendations").select("symbol").eq("status", "active").execute().data or []
        symbols = list(set(r["symbol"] for r in active))
        prices = {}
        # محاولة 1: سهمك (يشمل أعلى سعر اليوم High)
        if symbols and os.getenv("SAHMK_API_KEY"):
            prices = fetch_prices_full(symbols)
        # محاولة 2: من الشيت (السعر + عمود "أعلى")
        if len(prices) < len(symbols):
            sheet_full = _get_current_prices_full()
            for s in symbols:
                if s not in prices and s in sheet_full:
                    prices[s] = sheet_full[s]
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


@app.get("/api/recommendations/tracking")
def recommendations_tracking():
    """يعرض التوصيات مع حالتها اللحظية وسجل التتبع."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        rows = sb.table("idx_recommendations").select("*") \
            .order("appeared_date", desc=True).limit(200).execute().data or []

        active = [r for r in rows if r["status"] == "active"]
        success = [r for r in rows if r.get("outcome") == "success"]
        failed = [r for r in rows if r.get("outcome") == "failed"]
        flat = [r for r in rows if r.get("outcome") == "flat"]
        late = [r for r in rows if r.get("post_watch_hit")]

        def _slim_rec(r):
            conf = r.get("confidence", 0) or 0
            if not conf:
                raw = r.get("score", 0)
                conf = round(min(10, raw / 14), 1) if raw > 10 else round(raw, 1)
            return {
                "symbol": r["symbol"], "name": r.get("name", ""),
                "entry_price": r.get("entry_price"), "target_price": r.get("target_price"),
                "current_price": r.get("current_price"), "current_pct": r.get("current_pct", 0),
                "peak_pct": r.get("peak_pct", 0), "highest_price": r.get("highest_price"),
                "status": r["status"], "outcome": r.get("outcome"),
                "appeared_date": r.get("appeared_date"), "expiry_date": r.get("expiry_date"),
                "closed_date": r.get("closed_date"), "score": r.get("score", 0),
                "confidence": round(min(10, conf), 1),
                "created_at": r.get("created_at", ""),
                "category": r.get("category", ""), "reason": r.get("reason", ""),
                "post_watch": r.get("post_watch"), "post_watch_hit": r.get("post_watch_hit"),
                "post_watch_peak": r.get("post_watch_peak", 0),
                "post_target_high": r.get("post_target_high"),
                "post_target_pct": r.get("post_target_pct", 0),
            }

        all_recs = [_slim_rec(r) for r in rows]
        # ترتيب: الجارية أولاً ثم الأحدث
        all_recs.sort(key=lambda x: (x["status"] != "active", x.get("created_at") or x.get("appeared_date") or ""), reverse=False)
        active_first = [r for r in all_recs if r["status"] == "active"]
        closed_recs = sorted([r for r in all_recs if r["status"] != "active"],
                             key=lambda x: x.get("created_at") or x.get("appeared_date") or "", reverse=True)

        return {
            "all": active_first + closed_recs,
            "active": [_slim_rec(r) for r in active],
            "success": [_slim_rec(r) for r in success],
            "failed": [_slim_rec(r) for r in failed],
            "flat": [_slim_rec(r) for r in flat],
            "late_success": [_slim_rec(r) for r in late],
            "counts": {"active": len(active), "success": len(success),
                       "failed": len(failed), "flat": len(flat), "late": len(late)},
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sync/history")
def sync_history():
    """سجل عمليات السحب والتحديث."""
    from scheduler import get_sync_status
    status = get_sync_status()
    return {"log": status.get("log", []), "last_sync": status.get("last_sync", {})}


@app.get("/api/test/keys")
def test_keys():
    """يختبر كل المفاتيح ويرجّع حالة كل واحد."""
    import requests as _rq
    results = {}

    # ① سهمك — جرّب جلب سعر الراجحي
    sahmk = os.getenv("SAHMK_API_KEY", "")
    if not sahmk:
        results["sahmk"] = {"ok": False, "msg": "المفتاح غير مضاف"}
    else:
        try:
            from price_feed import fetch_price
            d = fetch_price("1120")
            if d.get("price"):
                results["sahmk"] = {"ok": True, "msg": f"الراجحي: {d['price']} ريال"}
            else:
                results["sahmk"] = {"ok": False, "msg": d.get("error", "لا سعر")}
        except Exception as e:
            results["sahmk"] = {"ok": False, "msg": str(e)[:100]}

    # ② Claude
    claude = os.getenv("ANTHROPIC_API_KEY", "")
    if not claude:
        results["claude"] = {"ok": False, "msg": "المفتاح غير مضاف"}
    else:
        try:
            r = _rq.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": claude, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 10, "messages": [{"role": "user", "content": "قل تمام"}]},
                timeout=20)
            results["claude"] = {"ok": r.status_code == 200,
                                 "msg": "يعمل ✓" if r.status_code == 200 else f"HTTP {r.status_code}"}
        except Exception as e:
            results["claude"] = {"ok": False, "msg": str(e)[:100]}

    # ③ Gemini
    gemini = os.getenv("GEMINI_API_KEY", "")
    if not gemini:
        results["gemini"] = {"ok": False, "msg": "المفتاح غير مضاف"}
    else:
        try:
            r = _rq.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": "قل تمام"}]}]}, timeout=20)
            results["gemini"] = {"ok": r.status_code == 200,
                                 "msg": "يعمل ✓" if r.status_code == 200 else f"HTTP {r.status_code}"}
        except Exception as e:
            results["gemini"] = {"ok": False, "msg": str(e)[:100]}

    # ④ Supabase
    sb = _get_supabase()
    if not sb:
        results["supabase"] = {"ok": False, "msg": "غير متصل — تحقق من URL/Key"}
    else:
        try:
            sb.table("idx_recommendations").select("id").limit(1).execute()
            results["supabase"] = {"ok": True, "msg": "متصل ✓"}
        except Exception as e:
            results["supabase"] = {"ok": False, "msg": str(e)[:100]}

    # ⑤ Google Sheets
    sheet = os.getenv("SHEET_URL", "") or _config.get("sheet_url", "")
    if not sheet:
        results["sheet"] = {"ok": False, "msg": "الرابط غير مضاف"}
    else:
        try:
            from sheets_reader import fetch_latest_snapshot
            snap = fetch_latest_snapshot(sheet)
            results["sheet"] = {"ok": True, "msg": f"آخر تبويب: {snap['tab_name']}"}
        except Exception as e:
            results["sheet"] = {"ok": False, "msg": str(e)[:100]}

    return results


@app.post("/api/recommendations/dedupe")
def dedupe_recommendations():
    """ينظّف التوصيات المكررة — يبقي الأقدم لكل سهم (جارية) ويحذف التكرار."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        active = sb.table("idx_recommendations").select("*").eq("status", "active") \
            .order("appeared_date", desc=False).order("created_at", desc=False).execute().data or []
        seen = {}
        to_delete = []
        for r in active:
            sym = r["symbol"]
            if sym in seen:
                # نبقي الأقدم، نحذف الأحدث المكرر
                to_delete.append(r["id"])
            else:
                seen[sym] = r["id"]
        for rid in to_delete:
            sb.table("idx_recommendations").delete().eq("id", rid).execute()

        # كذلك نظّف المكررات في المحسومة (نفس السهم بنفس النتيجة)
        closed = sb.table("idx_recommendations").select("*").neq("status", "active") \
            .order("appeared_date", desc=False).execute().data or []
        seen_closed = {}
        closed_del = []
        for r in closed:
            key = f"{r['symbol']}_{r.get('outcome','')}_{r.get('closed_date','')}"
            if key in seen_closed:
                closed_del.append(r["id"])
            else:
                seen_closed[key] = r["id"]
        for rid in closed_del:
            sb.table("idx_recommendations").delete().eq("id", rid).execute()

        total = len(to_delete) + len(closed_del)
        return {"ok": True, "removed": total,
                "message": f"حُذف {total} تكرار ({len(to_delete)} جارية، {len(closed_del)} محسومة)"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/recommendations/dedup")
def dedup_recommendations():
    """ينظّف التوصيات المكررة — يبقي أقدم توصية جارية لكل سهم ويحدّثها."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        active = sb.table("idx_recommendations").select("*") \
            .eq("status", "active").order("appeared_date").execute().data or []
        seen = {}
        removed = 0
        for r in active:
            sym = r["symbol"]
            if sym not in seen:
                seen[sym] = r  # أول ظهور (الأقدم) نبقيه
            else:
                # مكرر — احتفظ بأعلى ذروة ثم احذف
                keep = seen[sym]
                # لو المكرر ذروته أعلى، انقل القيم المهمة للأصل
                if (r.get("peak_pct", 0) or 0) > (keep.get("peak_pct", 0) or 0):
                    sb.table("idx_recommendations").update({
                        "highest_price": r.get("highest_price"),
                        "peak_pct": r.get("peak_pct"),
                        "current_price": r.get("current_price"),
                        "current_pct": r.get("current_pct"),
                    }).eq("id", keep["id"]).execute()
                sb.table("idx_recommendations").delete().eq("id", r["id"]).execute()
                removed += 1
        return {"ok": True, "removed": removed, "kept": len(seen),
                "message": f"حُذف {removed} مكرر · بقي {len(seen)} سهم فريد"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/recommendations/fix-categories")
def fix_categories():
    """يصحّح تصنيف التوصيات القديمة — كلها short_term (الاستراتيجية الأساسية)."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        # كل التوصيات المحفوظة في idx_recommendations تخص الاستراتيجية القصيرة
        rows = sb.table("idx_recommendations").select("id,category") \
            .neq("category", "short_term").execute().data or []
        fixed = 0
        for r in rows:
            sb.table("idx_recommendations").update({"category": "short_term"}).eq("id", r["id"]).execute()
            fixed += 1
        return {"ok": True, "fixed": fixed, "message": f"تم تصحيح {fixed} توصية إلى قصيرة المدى"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recommendations/longterm")
def recommendations_longterm(url: str = None):
    """
    أسهم المدى البعيد النوعية — أسبوعي/شهري.
    معايير صارمة: إشارات طويلة قوية، لم ترتفع كثيراً، جودة عالية.
    قليلة ونادرة التغيّر (صعبة التشكّل).
    """
    from sheets_reader import fetch_latest_snapshot
    from indicator_analyzer import analyze_dataframe
    sheet_url = url or _config.get("sheet_url")
    if not sheet_url:
        raise HTTPException(status_code=400, detail="حدد رابط الشيت")
    try:
        # استخدم آخر تحليل مخزّن إن وُجد
        analysis = _last_analysis.get("data")
        if not analysis:
            snap = fetch_latest_snapshot(sheet_url)
            analysis = analyze_dataframe(snap["df"])

        stocks = analysis.get("stocks", [])
        picks = []
        for s in stocks:
            fs = s.get("frame_scores", {})
            long_pct = fs.get("long", {}).get("pct", 0)
            wkly = s.get("weekly_change", 0)
            mnth = s.get("monthly_change", 0)
            rsi_state = s.get("rsi_state", "")

            # معايير المدى البعيد النوعي:
            # ① إشارات أسبوعية/شهرية قوية (long_pct عالي)
            # ② لم يرتفع كثيراً مؤخراً (أسبوعي معتدل، شهري ليس مرتفعاً)
            # ③ ليس فخاً وليس خمولاً ميّتاً
            # ④ جودة: تجميع أو زخم مبكر
            if (long_pct >= 40                        # إشارات طويلة قوية جداً
                    and -3 <= wkly <= 3               # لم يرتفع كثيراً أسبوعياً
                    and mnth <= 12                     # لم يرتفع كثيراً شهرياً
                    and not s.get("is_trap")
                    and rsi_state not in ("خمول ميّت", "مرتفع بلا دعم")
                    and s.get("bet_score", 0) >= 35):
                picks.append({
                    "symbol": s["symbol"], "name": s.get("name", ""),
                    "price": s.get("price"), "weekly_change": wkly,
                    "monthly_change": mnth, "long_pct": long_pct,
                    "bet_score": s.get("bet_score", 0),
                    "rsi": s.get("rsi", 0), "rsi_state": rsi_state,
                    "trend": s.get("trend", ""),
                    "signals": s.get("top3_signals", []),
                })

        # ترتيب حسب الأفضلية: أقوى إشارات طويلة + جودة عالية
        # مرتّبة حسب الأفضلية (bet_score ثم long_pct)، الأقل ارتفاعاً أفضل
        picks.sort(key=lambda x: (x["bet_score"], x["long_pct"], -abs(x["weekly_change"])), reverse=True)
        # محدودة جداً: أعلى 3 فقط (تشكّلها صعب وبطيء)
        picks = picks[:3]
        return {"picks": picks, "count": len(picks)}
    except Exception as e:
        traceback.print_exc()
        return {"picks": [], "error": str(e)[:150]}


@app.get("/api/recommendations/latest")
def recommendations_latest():
    """التوصيات الجارية (كلها) مرتّبة بالقوة — الأقوى دائماً ظاهر."""
    sb = _get_supabase()
    if not sb:
        return {"picks": [], "note": "Supabase غير متصل"}
    try:
        # الجارية فقط — تبقى ظاهرة حتى تُحسم
        rows = sb.table("idx_recommendations").select("*") \
            .eq("status", "active") \
            .order("created_at", desc=True).limit(30).execute().data or []
        if not rows:
            return {"picks": [], "date": None}

        latest_date = max((r.get("appeared_date") or "") for r in rows)

        picks = []
        for r in rows:
            conf = r.get("confidence", 0)
            if not conf or conf == 0:
                raw = r.get("score", 0)
                conf = round(min(10, raw / 14), 1) if raw > 10 else round(raw, 1)
            conf = round(min(10, conf), 1)
            picks.append({
                "symbol": r["symbol"], "name": r.get("name", ""),
                "price": r.get("entry_price"), "entry_price": r.get("entry_price"),
                "target_price": r.get("target_price"),
                "current_price": r.get("current_price"),
                "current_pct": r.get("current_pct", 0),
                "confidence": conf,
                "reasoning": r.get("reason", ""),
                "category": r.get("category", ""),
                "status": r.get("status"), "outcome": r.get("outcome"),
                "peak_pct": r.get("peak_pct", 0),
                "appeared_date": r.get("appeared_date"),
                "expiry_date": r.get("expiry_date"),
                "max_expiry_date": r.get("max_expiry_date"),
                "created_at": r.get("created_at", ""),
            })
        picks.sort(key=lambda x: x["confidence"], reverse=True)
        # كل الجارية (الجدول يستوعبها) — مرتّبة بالقوة
        return {"picks": picks, "total_active": len(picks), "date": latest_date, "count": len(picks)}
    except Exception as e:
        return {"picks": [], "error": str(e)[:150]}


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


def _get_current_prices_full() -> dict:
    """يجلب {symbol: {price, high, low}} من الشيت — يشمل عمود 'أعلى'."""
    result = {}
    try:
        from sheets_reader import fetch_latest_snapshot
        sheet_url = _config.get("sheet_url")
        if not sheet_url:
            return result
        snap = fetch_latest_snapshot(sheet_url)
        df = snap["df"]
        sym_col = next((c for c in df.columns if str(c).strip() == "الرمز"), None) or next((c for c in df.columns if "الرمز" in str(c)), None)
        price_col = next((c for c in df.columns if str(c).strip() == "آخر"), None) or next((c for c in df.columns if "آخر" in str(c) or str(c).strip() == "السعر"), None)
        high_col = next((c for c in df.columns if str(c).strip() == "أعلى"), None)
        low_col = next((c for c in df.columns if str(c).strip() == "أدنى"), None)
        if not (sym_col and price_col):
            return result
        for _, row in df.iterrows():
            try:
                sym = str(row[sym_col])
                price = float(row[price_col])
                high = float(row[high_col]) if high_col and str(row[high_col]) not in ("nan", "") else price
                low = float(row[low_col]) if low_col and str(row[low_col]) not in ("nan", "") else price
                result[sym] = {"price": price, "high": max(high, price), "low": min(low, price)}
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    return result


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
#  المزامنة التلقائية
# ══════════════════════════════════════════════════════════════
@app.get("/api/sync/status")
def sync_status():
    from scheduler import get_sync_status
    return get_sync_status()


@app.post("/api/sync/now")
def sync_now(force: bool = False, full: bool = True):
    """مزامنة فورية — يسحب ويحلّل كامل (Claude+Gemini)."""
    from scheduler import run_sync
    return run_sync(force=force, full=full)


@app.post("/api/sync/start")
def sync_start():
    """تشغيل المجدوِل التلقائي."""
    from scheduler import start_scheduler
    return start_scheduler()


@app.post("/api/sync/stop")
def sync_stop():
    """إيقاف المجدوِل."""
    from scheduler import stop_scheduler
    return stop_scheduler()


@app.post("/api/sync/pause")
def sync_pause():
    """إيقاف مؤقت حتى إشعار آخر."""
    from scheduler import pause_scheduler
    return pause_scheduler()


@app.post("/api/sync/resume")
def sync_resume():
    """استئناف بعد الإيقاف المؤقت."""
    from scheduler import resume_scheduler
    return resume_scheduler()


@app.post("/api/sync/times")
def sync_set_times(times: list[str]):
    """ضبط أوقات المزامنة من الموقع."""
    from scheduler import set_sync_times
    return set_sync_times(times)


@app.get("/api/wake")
def wake():
    """نقطة إيقاظ خفيفة لخدمات cron (تمنع نوم Render Free)."""
    from market_clock import now_riyadh, classify_snapshot_time
    return {"awake": True, "time": now_riyadh().strftime("%H:%M"),
            "market": classify_snapshot_time()}


@app.get("/api/cron/sync")
def cron_sync():
    """نقطة وصول لخدمات cron خارجية — يسحب ويحلّل كامل."""
    from scheduler import run_sync
    from market_clock import is_trading_day
    if not is_trading_day():
        return {"skipped": True, "reason": "عطلة"}
    return run_sync(full=True)


# تشغيل المجدوِل تلقائياً عند بدء السيرفر
@app.on_event("startup")
def _auto_start_scheduler():
    from scheduler import start_scheduler
    start_scheduler()
    print("📡 المجدوِل التلقائي يعمل: " + ", ".join(["10:30","12:00","14:00","16:30"]))


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
