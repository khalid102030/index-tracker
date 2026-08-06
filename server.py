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

from fastapi import FastAPI, HTTPException, Response, BackgroundTasks, UploadFile, File, Request
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
def analyze_full(url: str = None, skip_duplicate: bool = False, force: bool = False):
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

        # ── كشف التكرار: نفس التبويب أو نفس الأسعار ──
        if skip_duplicate and not force:
            try:
                import scheduler as _sch
                fp = _sch._data_fingerprint(snap["df"])
                same_tab = _sch._last_sync.get("tab") == snap["tab_name"]
                same_data = fp and _sch._last_sync.get("data_fp") == fp
                if same_tab or same_data:
                    reason = "نفس التبويب" if same_tab else "نفس الأسعار (مطابقة للسابق)"
                    return {
                        "ok": True, "skipped": True, "no_update": True,
                        "message": f"⚠️ البيانات لم تتغير — {reason}. لم يُجرَ تحليل جديد.",
                        "tab": snap["tab_name"],
                    }
            except Exception:
                pass

        analysis = analyze_dataframe(snap["df"])
        analysis["source"] = {"tab": snap["tab_name"],
                              "market_status": classify_snapshot_time()}
        _last_analysis["data"] = analysis
        # سجّل وقت آخر تحليل (يدوي) في حالة المجدوِل
        try:
            import scheduler as _sch
            from market_clock import now_riyadh
            _fp = _sch._data_fingerprint(snap["df"])
            _sch._last_sync.update(time=now_riyadh().isoformat(), tab=snap["tab_name"],
                                   status="success", stocks=analysis["summary"]["total_stocks"],
                                   error=None, data_fp=_fp)
        except Exception:
            pass

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
        new_picks = []
        if sb and eval_result.get("picks"):
            for pick in eval_result["picks"]:
                orig = next((s for s in analysis["stocks"] if s["symbol"] == pick["symbol"]), {})
                merged = {**orig, **pick, "reason": pick.get("reasoning", "")}
                # الاستراتيجية الأساسية دائماً قصيرة المدى (هدف 1.5% بأيام)
                cat = "short_term"
                rec = create_recommendation(merged, cat, sb)
                saved += 1
                # توصية جديدة فقط (ليست تحديثاً لموجودة)
                if not (rec or {}).get("_updated"):
                    new_picks.append({**merged, "target_price": (rec or {}).get("target_price"),
                                      "horizon": pick.get("horizon", ""), "category": cat})

        # إشعار تيليجرام للتوصيات الجديدة فقط (إذا كانت الخدمة مفعّلة)
        tg_result = None
        if new_picks:
            try:
                from telegram_bot import notify_batch
                tg_result = notify_batch(new_picks)
            except Exception as _e:
                tg_result = {"error": str(_e)[:100]}

        # حفظ أسهم المدى البعيد النوعية للتتبع (إذا مفعّلة)
        if sb:
            try:
                # هل التوصيات البعيدة مفعّلة؟
                lt_on = True
                try:
                    srow = sb.table("idx_settings").select("*").eq("key", "longterm").limit(1).execute().data
                    if srow:
                        lt_on = (srow[0].get("value") or {}).get("enabled", True)
                except Exception:
                    pass
                if lt_on:
                    from tracker import create_recommendation
                    lt_stocks = [s for s in analysis["stocks"]
                                 if s.get("long_term_quality")]
                    lt_stocks.sort(key=lambda x: x.get("bet_score", 0), reverse=True)
                    for s in lt_stocks[:3]:
                        merged = {**s, "reason": "مدى بعيد نوعي",
                                  "confidence": min(10, round(s.get("bet_score", 0)/14, 1))}
                        create_recommendation(merged, "long_term", sb)
            except Exception:
                pass

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
            "telegram": tg_result,
            "new_count": len(new_picks),
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
                "long_term_quality": (r.get("indicators") or {}).get("long_term_quality", False) if isinstance(r.get("indicators"), dict) else False,
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


@app.get("/api/strategy/criteria")
def strategy_criteria():
    """المعايير والأوزان المرجعية — للمراجعة مقابل الأداء."""
    return {
        "core": {
            "target_pct": 1.5, "validity_days": 3, "max_days": 5,
            "flat_band": 1.0, "post_watch_days": 14,
        },
        "rsi_layer": [
            {"state": "زخم مؤكد", "condition": "RSI≥60 + سيولة", "weight": 22},
            {"state": "مرتفع بلا دعم", "condition": "RSI≥60 بلا سيولة", "weight": -10},
            {"state": "صاعد صحّي", "condition": "RSI 50-60 + سيولة", "weight": 12},
            {"state": "حيادي", "condition": "RSI 45-50", "weight": -3},
            {"state": "تجميع مبكر", "condition": "RSI<45 + سيولة + هدوء", "weight": 10},
            {"state": "خمول ميّت", "condition": "RSI<45 بلا سيولة", "weight": -22},
        ],
        "bonuses": [
            {"signal": "زخم مؤكد (RSI+سيولة)", "weight": 32},
            {"signal": "تتابع صحّي", "weight": 30},
            {"signal": "سيولة متراكمة", "weight": 28},
            {"signal": "تجميع صامت", "weight": 25},
            {"signal": "بوادر مبكرة", "weight": 22},
            {"signal": "تجميع مبكر", "weight": 20},
            {"signal": "قرب من المتوسط", "weight": 15},
            {"signal": "قصيرة نشطة", "weight": 14},
        ],
        "penalties": [
            {"penalty": "فخ كلاسيكي", "weight": -45},
            {"penalty": "اشتعال كامل", "weight": -30},
            {"penalty": "صعود متأخر", "weight": -25},
            {"penalty": "خمول ميّت", "weight": -22},
            {"penalty": "اشتعال مفرط", "weight": -20},
            {"penalty": "RSI مرتفع بلا دعم", "weight": -10},
        ],
        "timeframe": [
            {"range": "الربع الذهبي (0.7-1.6%)", "weight": 30, "measured_rate": "80%"},
            {"range": "صاعد قوي (≥1.61%)", "weight": 20},
            {"range": "المنطقة الميتة (-0.53-0.7%)", "weight": -25, "measured_rate": "51%"},
        ],
        "gates": {
            "max_candidates": 12, "max_final": 3,
            "min_confidence": 8, "min_bet_score": 30,
        },
    }


@app.get("/api/recommendations/export")
def export_recommendations(fmt: str = "csv"):
    """
    تصدير سجل تحليلي كامل: المؤشرات + الأوزان + الإشارات + النتيجة.
    للدراسة وتقييم أي المؤشرات لها تأثير على النجاح.
    """
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        rows = sb.table("idx_recommendations").select("*") \
            .order("appeared_date", desc=True).limit(5000).execute().data or []

        if fmt == "json":
            return {"count": len(rows), "data": rows}

        # CSV تحليلي
        import io, csv
        buf = io.StringIO()
        buf.write("\ufeff")  # BOM للعربي في Excel
        w = csv.writer(buf)
        # العناوين
        w.writerow([
            "الرمز", "الاسم", "التصنيف", "تاريخ الظهور", "وقت الظهور",
            "سعر الدخول", "الهدف", "الثقة", "النقاط(bet_score)",
            "الاتجاه", "RSI", "حالة_RSI", "MFI", "صافي_السيولة",
            "التغير_الأسبوعي", "التغير_الشهري", "قوة_الانطلاق", "عدد_النشطة",
            "الإشارات_الثلاث", "العقوبات",
            "قصير_%", "متوسط_%", "طويل_%",
            "الحالة", "النتيجة", "الذروة%", "أعلى_سعر",
            "أعلى_بعد_الهدف", "%بعد_الهدف", "تاريخ_الإغلاق", "حقق_لاحقاً",
        ])
        for r in rows:
            ind = r.get("indicators") or {}
            if isinstance(ind, str):
                try: ind = json.loads(ind)
                except: ind = {}
            ss = r.get("signals_summary") or {}
            if isinstance(ss, str):
                try: ss = json.loads(ss)
                except: ss = {}
            created = r.get("created_at", "") or ""
            w.writerow([
                r.get("symbol",""), r.get("name",""), r.get("category",""),
                r.get("appeared_date",""), created[11:16] if len(created) > 16 else "",
                r.get("entry_price",""), r.get("target_price",""),
                r.get("confidence",""), r.get("score",""),
                r.get("trend",""),
                ind.get("rsi",""), ind.get("rsi_state",""), ind.get("mfi",""),
                ind.get("net_liquidity",""), ind.get("weekly_change",""),
                ind.get("monthly_change",""), ind.get("pre_launch",""),
                ind.get("total_active",""),
                " | ".join(r.get("top3_signals") or []),
                " | ".join(ind.get("penalties") or []),
                ss.get("short",{}).get("pct",""), ss.get("mid",{}).get("pct",""),
                ss.get("long",{}).get("pct",""),
                r.get("status",""), r.get("outcome",""),
                r.get("peak_pct",""), r.get("highest_price",""),
                r.get("post_target_high",""), r.get("post_target_pct",""),
                r.get("closed_date",""), "نعم" if r.get("post_watch_hit") else "",
            ])
        csv_data = buf.getvalue()
        from fastapi.responses import Response as FastResponse
        return FastResponse(
            content=csv_data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=rasid_export_{date.today().isoformat()}.csv"}
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recommendations/analysis")
def indicator_analysis():
    """
    تحليل: أي المؤشرات/الإشارات لها أعلى تأثير على النجاح.
    يقارن الناجحة بالفاشلة عبر متوسطات المؤشرات.
    """
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        rows = sb.table("idx_recommendations").select("*") \
            .eq("status", "closed").limit(5000).execute().data or []
        succ = [r for r in rows if r.get("outcome") == "success"]
        fail = [r for r in rows if r.get("outcome") == "failed"]

        def _avg_ind(recs, field):
            vals = []
            for r in recs:
                ind = r.get("indicators") or {}
                if isinstance(ind, str):
                    try: ind = json.loads(ind)
                    except: ind = {}
                v = ind.get(field)
                if isinstance(v, (int, float)):
                    vals.append(v)
            return round(sum(vals)/len(vals), 2) if vals else None

        # تأثير الإشارات: نسبة نجاح كل إشارة
        sig_impact = {}
        for r in rows:
            for sig in (r.get("top3_signals") or []):
                sig_impact.setdefault(sig, {"success": 0, "total": 0})
                sig_impact[sig]["total"] += 1
                if r.get("outcome") == "success":
                    sig_impact[sig]["success"] += 1
        sig_rates = sorted([
            {"signal": s, "rate": round(v["success"]/v["total"]*100, 1), "count": v["total"]}
            for s, v in sig_impact.items() if v["total"] >= 2
        ], key=lambda x: x["rate"], reverse=True)

        # تأثير حالة RSI
        rsi_impact = {}
        for r in rows:
            ind = r.get("indicators") or {}
            if isinstance(ind, str):
                try: ind = json.loads(ind)
                except: ind = {}
            st = ind.get("rsi_state", "")
            if st:
                rsi_impact.setdefault(st, {"success": 0, "total": 0})
                rsi_impact[st]["total"] += 1
                if r.get("outcome") == "success":
                    rsi_impact[st]["success"] += 1
        rsi_rates = sorted([
            {"state": s, "rate": round(v["success"]/v["total"]*100, 1), "count": v["total"]}
            for s, v in rsi_impact.items()
        ], key=lambda x: x["rate"], reverse=True)

        return {
            "closed_total": len(rows),
            "success": len(succ), "failed": len(fail),
            "avg_indicators": {
                "success": {f: _avg_ind(succ, f) for f in ["rsi", "weekly_change", "monthly_change", "net_liquidity", "pre_launch", "total_active"]},
                "failed": {f: _avg_ind(fail, f) for f in ["rsi", "weekly_change", "monthly_change", "net_liquidity", "pre_launch", "total_active"]},
            },
            "signal_success_rates": sig_rates,
            "rsi_state_success_rates": rsi_rates,
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/recommendations/reopen-early-closed")
def reopen_early_closed():
    """
    يعيد فتح التوصيات التي حُسمت (فاشلة/بلا حركة) قبل انتهاء مدتها الفعلية.
    بمفهوم 'نهاية التداول' — لو مدتها لم تنتهِ بعد، ترجع نشطة.
    لا يلمس الناجحة.
    """
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        from tracker import _is_past_expiry
        closed = sb.table("idx_recommendations").select("*") \
            .eq("status", "closed").neq("outcome", "success").execute().data or []
        reopened = 0
        for r in closed:
            # لو مدتها لم تنتهِ فعلياً → ترجع نشطة
            if not _is_past_expiry(r.get("max_expiry_date", "9999")):
                sb.table("idx_recommendations").update({
                    "status": "active", "outcome": None,
                    "closed_date": None, "post_watch": False,
                }).eq("id", r["id"]).execute()
                reopened += 1
        return {"ok": True, "reopened": reopened,
                "message": f"أُعيد فتح {reopened} توصية حُسمت قبل انتهاء مدتها"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/recommendations/dedup")
def dedup_recommendations():
    """
    ينظّف التوصيات المكررة لكل سهم:
    - لو فيه نشطة + منتهية لنفس السهم (تداخل)، يبقي النشطة ويحذف المنتهية المكررة
    - لو عدة نشطة، يبقي الأقدم بأعلى ذروة
    يمنع احتساب نفس التوصية مرتين في النسب.
    """
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        allr = sb.table("idx_recommendations").select("*") \
            .order("appeared_date").execute().data or []

        # تجميع حسب (السهم + تاريخ الدخول) — نفس التوصية بالضبط
        groups = {}
        for r in allr:
            key = (r["symbol"], r.get("entry_price"))
            groups.setdefault(key, []).append(r)

        removed = 0
        for key, rows in groups.items():
            if len(rows) <= 1:
                continue
            # نفس السهم ونفس سعر الدخول = نفس التوصية مكررة
            active = [r for r in rows if r["status"] == "active"]
            closed = [r for r in rows if r["status"] == "closed"]

            # الأولوية: نبقي النشطة إن وُجدت، وإلا الناجحة، وإلا الأحدث
            if active:
                keep = active[0]
                drop = active[1:] + closed
            else:
                success = [r for r in closed if r.get("outcome") == "success"]
                keep = success[0] if success else closed[-1]
                drop = [r for r in rows if r["id"] != keep["id"]]

            # انقل أعلى ذروة للمُبقى
            max_peak = max((r.get("peak_pct", 0) or 0) for r in rows)
            max_high = max((r.get("highest_price", 0) or 0) for r in rows)
            if max_peak > (keep.get("peak_pct", 0) or 0):
                sb.table("idx_recommendations").update({
                    "peak_pct": max_peak, "highest_price": max_high,
                }).eq("id", keep["id"]).execute()

            for r in drop:
                sb.table("idx_recommendations").delete().eq("id", r["id"]).execute()
                removed += 1

        return {"ok": True, "removed": removed,
                "message": f"حُذف {removed} توصية مكررة — النسب الآن دقيقة"}
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


@app.get("/api/recommendations/check/{symbol}")
def check_symbol(symbol: str):
    """تشخيص: كل سجلات سهم معيّن (لمعرفة ليش اعتُبر مكرراً)."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    try:
        rows = sb.table("idx_recommendations").select("*") \
            .eq("symbol", symbol).order("appeared_date", desc=True).execute().data or []
        return {"symbol": symbol, "count": len(rows),
                "records": [{"appeared": r.get("appeared_date"),
                             "status": r.get("status"), "outcome": r.get("outcome"),
                             "category": r.get("category"),
                             "entry": r.get("entry_price"),
                             "created": r.get("created_at", "")[:16]} for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.post("/api/telegram/resend-today")
def telegram_resend_today():
    """يعيد إرسال توصيات اليوم لتيليجرام (لو ما وصلت)."""
    from telegram_bot import notify_batch, is_enabled, get_settings
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    if not is_enabled():
        s = get_settings()
        missing = []
        if not s.get("enabled"): missing.append("الخدمة غير مفعّلة (فعّل 🔔 إرسال التوصيات)")
        if not s.get("bot_token"): missing.append("لا يوجد توكن")
        if not s.get("chat_id"): missing.append("لا يوجد Chat ID")
        return {"ok": False, "message": " · ".join(missing) or "غير مفعّلة"}
    from datetime import date
    today = date.today().isoformat()
    try:
        rows = sb.table("idx_recommendations").select("*") \
            .eq("appeared_date", today).eq("status", "active").execute().data or []
        if not rows:
            return {"ok": True, "sent": 0, "message": "لا توصيات جديدة اليوم"}
        picks = [{"symbol": r["symbol"], "name": r["name"],
                  "entry_price": r["entry_price"], "target_price": r["target_price"],
                  "confidence": r.get("confidence", 0),
                  "category": r.get("category", "short_term")} for r in rows]
        result = notify_batch(picks)
        return {"ok": True, "sent": result.get("sent", 0),
                "recipients": result.get("recipients", 0),
                "message": f"أُرسلت {len(picks)} توصية"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.get("/api/sheet/debug")
def sheet_debug():
    """تشخيص: وش يشوف النظام بالشيت (آخر تبويب + البصمة)."""
    sheet_url = _config.get("sheet_url")
    if not sheet_url:
        raise HTTPException(status_code=400, detail="لا يوجد رابط شيت")
    try:
        from sheets_reader import fetch_latest_snapshot
        import scheduler as _sch
        snap = fetch_latest_snapshot(sheet_url)
        fp = _sch._data_fingerprint(snap["df"])
        last = _sch._last_sync
        return {
            "current_tab": snap["tab_name"],
            "all_tabs": snap.get("all_tabs", [])[-5:],
            "rows": len(snap["df"]),
            "current_fingerprint": fp[:12] if fp else "",
            "last_synced_tab": last.get("tab"),
            "last_synced_fingerprint": (last.get("data_fp") or "")[:12],
            "same_tab": last.get("tab") == snap["tab_name"],
            "same_data": bool(fp) and last.get("data_fp") == fp,
            "verdict": "سيتوقف (مكرر)" if (last.get("tab") == snap["tab_name"] or (fp and last.get("data_fp") == fp)) else "سيحلّل (جديد)",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.get("/api/longterm/status")
def longterm_status():
    """حالة تفعيل التوصيات البعيدة."""
    sb = _get_supabase()
    enabled = True
    if sb:
        try:
            srow = sb.table("idx_settings").select("*").eq("key", "longterm").limit(1).execute().data
            if srow:
                enabled = (srow[0].get("value") or {}).get("enabled", True)
        except Exception:
            pass
    return {"enabled": enabled}


@app.post("/api/longterm/toggle")
def longterm_toggle():
    """تشغيل/إيقاف التوصيات البعيدة."""
    sb = _get_supabase()
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase غير متصل")
    cur = True
    try:
        srow = sb.table("idx_settings").select("*").eq("key", "longterm").limit(1).execute().data
        if srow:
            cur = (srow[0].get("value") or {}).get("enabled", True)
    except Exception:
        pass
    new_state = not cur
    sb.table("idx_settings").upsert(
        {"key": "longterm", "value": {"enabled": new_state}}, on_conflict="key").execute()
    return {"enabled": new_state}


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
        # الجارية فقط — تبقى ظاهرة حتى تُحسم (كلها، بدون حد فعلي)
        rows = sb.table("idx_recommendations").select("*") \
            .eq("status", "active") \
            .order("created_at", desc=True).limit(500).execute().data or []
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
                "highest_price": r.get("highest_price"),
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


@app.post("/api/sync/skip-next")
def sync_skip_next():
    """تخطّي السحب القادم مرة واحدة (لا يلغي الجدول)."""
    from scheduler import skip_next_sync
    return skip_next_sync()


@app.post("/api/sync/cancel-skip")
def sync_cancel_skip():
    """إلغاء تخطّي السحب القادم."""
    from scheduler import cancel_skip
    return cancel_skip()


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
    """
    نقطة cron الذكية — استخدم هذا الرابط في cron job.
    يتحقق تلقائياً:
    ① يوم تداول؟ (يتخطى العطل)
    ② بيانات جديدة؟ (يتوقف لو نفس التبويب)
    ثم يحلّل وينشر فقط عند وجود جديد.
    """
    from scheduler import run_sync
    from market_clock import is_trading_day
    if not is_trading_day():
        return {"skipped": True, "reason": "عطلة — لا تداول اليوم"}
    # run_sync فيه كشف التكرار المدمج
    return run_sync(full=True)


# تشغيل المجدوِل تلقائياً عند بدء السيرفر
@app.on_event("startup")
def _auto_start_scheduler():
    import scheduler as _sch
    # حمّل الإعدادات المحفوظة من Supabase (تبقى بعد إعادة التشغيل)
    _sch._state_loaded[0] = False
    _sch._ensure_state()
    _sch.start_scheduler()
    print("📡 المجدوِل التلقائي يعمل: " + ", ".join(_sch.SYNC_TIMES))


# ══════════════════════════════════════════════════════════════
#  Telegram — تكامل مستقل
# ══════════════════════════════════════════════════════════════
class TelegramConfig(BaseModel):
    enabled: bool = None
    bot_token: str = None
    chat_id: str = None
    access_code: str = None


@app.get("/api/telegram/settings")
def telegram_get_settings():
    """يرجّع إعدادات تيليجرام (التوكن مخفي)."""
    from telegram_bot import get_settings, get_access_code
    s = get_settings()
    return {
        "enabled": s.get("enabled", False),
        "bot_token": "•••" + s.get("bot_token", "")[-6:] if s.get("bot_token") else "",
        "chat_id": s.get("chat_id", ""),
        "access_code": get_access_code(),
        "private_mode": s.get("private_mode", False),
    }


@app.post("/api/telegram/settings")
def telegram_save_settings(cfg: TelegramConfig):
    """يحفظ إعدادات تيليجرام."""
    from telegram_bot import save_settings
    return save_settings(enabled=cfg.enabled, bot_token=cfg.bot_token,
                         chat_id=cfg.chat_id, access_code=cfg.access_code)


@app.post("/api/telegram/toggle")
def telegram_toggle():
    """تشغيل/إيقاف خدمة تيليجرام (مستقلة)."""
    from telegram_bot import get_settings, save_settings
    s = get_settings()
    new_state = not s.get("enabled", False)
    save_settings(enabled=new_state)
    return {"enabled": new_state}


@app.post("/api/telegram/test")
def telegram_test():
    """يختبر الاتصال ويرسل رسالة تجريبية."""
    from telegram_bot import test_connection
    return test_connection()


@app.post("/api/telegram/set-webhook")
def telegram_set_webhook(request: Request):
    """يسجّل webhook (يُستدعى مرة بعد النشر)."""
    from telegram_bot import set_webhook, get_webhook_info
    base = str(request.base_url).rstrip("/")
    # تأكد من https
    if base.startswith("http://"):
        base = base.replace("http://", "https://", 1)
    result = set_webhook(base)
    info = get_webhook_info()
    return {"set_result": result, "webhook_url": f"{base}/api/telegram/webhook",
            "current_webhook": info.get("result", {}).get("url", ""),
            "pending": info.get("result", {}).get("pending_update_count", 0),
            "last_error": info.get("result", {}).get("last_error_message", "")}


@app.get("/api/telegram/enable")
def telegram_enable_get():
    """تفعيل الخدمة مباشرة من المتصفح (حل بديل للزر)."""
    from telegram_bot import save_settings, is_enabled
    save_settings(enabled=True)
    return {"enabled": True, "is_enabled_now": is_enabled(),
            "message": "✅ تم تفعيل خدمة تيليجرام — جرّب /active الآن"}


@app.get("/api/telegram/disable")
def telegram_disable_get():
    """إيقاف الخدمة مباشرة من المتصفح."""
    from telegram_bot import save_settings
    save_settings(enabled=False)
    return {"enabled": False, "message": "🔴 تم إيقاف خدمة تيليجرام"}


@app.post("/api/telegram/private-toggle")
def telegram_private_toggle():
    """تشغيل/إيقاف الوضع الخاص (البوت تحت التطوير — للمالك فقط)."""
    from telegram_bot import get_settings
    s = get_settings()
    new_state = not s.get("private_mode", False)
    cur = get_settings()
    cur["private_mode"] = new_state
    sb = _get_supabase()
    if sb:
        try:
            sb.table("idx_settings").upsert(
                {"key": "telegram", "value": cur}, on_conflict="key").execute()
        except Exception:
            pass
    return {"private_mode": new_state}


@app.get("/api/telegram/subscribers")
def telegram_subscribers():
    """قائمة المشتركين + حالة التفعيل."""
    from telegram_bot import _get_subscribers, _get_authorized, get_settings
    s = get_settings()
    return {"subscribers": _get_subscribers(), "full_access": _get_authorized(),
            "subs_enabled": s.get("subs_enabled", True)}


@app.post("/api/telegram/subs-toggle")
def telegram_subs_toggle():
    """تشغيل/إيقاف إرسال التوصيات للمشتركين."""
    from telegram_bot import get_settings, save_settings
    s = get_settings()
    new_state = not s.get("subs_enabled", True)
    # حفظ عبر save_settings مع تمرير الحقل
    cur = get_settings()
    cur["subs_enabled"] = new_state
    sb = _get_supabase()
    if sb:
        try:
            sb.table("idx_settings").upsert(
                {"key": "telegram", "value": cur}, on_conflict="key").execute()
        except Exception:
            pass
    return {"subs_enabled": new_state}


@app.post("/api/telegram/subs-remove")
def telegram_subs_remove(cfg: dict):
    """يحذف مشترك محدد."""
    from telegram_bot import _remove_subscriber
    cid = cfg.get("chat_id", "")
    remaining = _remove_subscriber(cid)
    return {"ok": True, "remaining": len(remaining)}


@app.get("/api/telegram/webhook-info")
def telegram_webhook_info():
    """يفحص حالة webhook للتشخيص."""
    from telegram_bot import get_webhook_info, is_enabled, get_settings
    info = get_webhook_info()
    s = get_settings()
    return {
        "webhook": info.get("result", info),
        "is_enabled": is_enabled(),
        "enabled_flag": s.get("enabled"),
        "has_token": bool(s.get("bot_token")),
        "has_chat_id": bool(s.get("chat_id")),
        "chat_id": s.get("chat_id"),
    }


@app.get("/api/telegram/test-command")
def telegram_test_command():
    """يحاكي أمر /active للتشخيص (يرسل لك النتيجة)."""
    from telegram_bot import handle_update, get_settings
    s = get_settings()
    fake_update = {"message": {"text": "/active",
                   "chat": {"id": s.get("chat_id")}}}
    result = handle_update(fake_update)
    return {"handled": result}


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """نقطة استقبال تحديثات تيليجرام — ترد فوراً وتعالج بالخلفية."""
    from telegram_bot import handle_update
    try:
        update = await request.json()
        # معالجة بالخلفية عشان الرد يكون فوري (تيليجرام يتوقّع رد سريع)
        background_tasks.add_task(handle_update, update)
        return {"ok": True}
    except Exception:
        return {"ok": True}


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
