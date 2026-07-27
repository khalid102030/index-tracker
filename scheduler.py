# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════
  المزامنة التلقائية — يسحب من Google Sheets ويحلّل
  أوقات التداول: 10:30 · 12:00 · 14:00 · 16:00
  أيام: أحد–خميس فقط
═══════════════════════════════════════════════════════════════
"""
import threading, time, json, os
from datetime import datetime, timedelta
from market_clock import now_riyadh, is_trading_day, RIYADH_TZ

# أوقات المزامنة — قابلة للتعديل من الموقع
SYNC_TIMES = ["10:30", "12:00", "14:00", "16:30"]
_scheduler_running = False
_scheduler_paused = False  # إيقاف مؤقت حتى إشعار آخر
_scheduler_thread = None
_last_sync = {"time": None, "tab": None, "status": None, "stocks": 0, "error": None}
_sync_log = []  # آخر 20 محاولة

_STATE_FILE = os.path.join(os.path.dirname(__file__), "scheduler_state.json")


def _load_state():
    global SYNC_TIMES, _scheduler_paused
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE) as f:
                st = json.load(f)
                SYNC_TIMES = st.get("sync_times", SYNC_TIMES)
                _scheduler_paused = st.get("paused", False)
    except Exception:
        pass


def _save_state():
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump({"sync_times": SYNC_TIMES, "paused": _scheduler_paused}, f)
    except Exception:
        pass


def set_sync_times(times: list):
    """يضبط أوقات المزامنة من الموقع."""
    global SYNC_TIMES
    SYNC_TIMES = sorted(times)
    _save_state()
    return {"sync_times": SYNC_TIMES}


def pause_scheduler():
    """إيقاف مؤقت حتى إشعار آخر."""
    global _scheduler_paused
    _scheduler_paused = True
    _save_state()
    return {"paused": True}


def resume_scheduler():
    global _scheduler_paused
    _scheduler_paused = False
    _save_state()
    return {"paused": False}


_load_state()


def get_sync_status() -> dict:
    return {
        "scheduler_active": _scheduler_running,
        "paused": _scheduler_paused,
        "last_sync": _last_sync,
        "schedule": SYNC_TIMES,
        "next_sync": _next_sync_time(),
        "log": _sync_log[-10:],
    }


def _next_sync_time() -> str:
    now = now_riyadh()
    if not is_trading_day(now):
        return "عطلة — أقرب مزامنة يوم التداول القادم"
    current = now.strftime("%H:%M")
    for t in SYNC_TIMES:
        if t > current:
            return f"اليوم الساعة {t}"
    return "غداً الساعة " + SYNC_TIMES[0]


def run_sync(force: bool = False, full: bool = True) -> dict:
    """
    يسحب من Google Sheets ويحلّل.
    إذا full=True: يكمّل التحليل الكامل (Claude+Gemini) ويحفظ التوصيات.
    يتوقف تلقائياً إذا ما فيه تحديث جديد للبيانات.
    """
    global _last_sync

    sheet_url = os.getenv("SHEET_URL", "")
    if not sheet_url:
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r") as f:
                    sheet_url = json.load(f).get("sheet_url", "")
        except Exception:
            pass

    if not sheet_url:
        result = {"ok": False, "error": "رابط Google Sheets غير محدد", "time": now_riyadh().isoformat()}
        _last_sync.update(status="error", error=result["error"], time=now_riyadh().isoformat())
        _log(result)
        return result

    try:
        from sheets_reader import fetch_latest_snapshot
        from indicator_analyzer import analyze_dataframe
        from market_clock import classify_snapshot_time

        snap = fetch_latest_snapshot(sheet_url)
        tab = snap["tab_name"]
        snap_time = snap["snapshot_time"]

        # ── كشف التكرار: لو نفس التبويب، توقّف ولا تكمّل ──
        if not force and _last_sync.get("tab") == tab:
            result = {
                "ok": True, "skipped": True,
                "message": f"⚠️ آخر تحديث لا يزال ساري — البيانات لم تتغير منذ {_last_sync.get('time','?')[11:16]}. تم إيقاف التحليل.",
                "tab": tab, "time": now_riyadh().isoformat(),
            }
            _last_sync["status"] = "no_update"
            _log(result)
            return result

        # ── تحليل جديد ──
        df = snap["df"]
        analysis = analyze_dataframe(df)
        analysis["source"] = {
            "type": "auto", "tab": tab,
            "snapshot_time": snap_time.isoformat() if snap_time else None,
            "market_status": classify_snapshot_time(),
        }

        # تحديث الكاش العام
        try:
            import server
            server._last_analysis["data"] = analysis
        except Exception:
            pass

        result = {
            "ok": True, "skipped": False, "tab": tab,
            "stocks": analysis["summary"]["total_stocks"],
            "mood": analysis["summary"]["market_mood"]["state"],
            "time": now_riyadh().isoformat(),
        }

        # ── التحليل الكامل التلقائي (Claude + Gemini + حفظ) ──
        if full:
            try:
                from dual_evaluator import dual_evaluate
                from tracker import (create_recommendation, update_active,
                                     update_post_watch, performance_report)
                from price_feed import fetch_prices_full
                import server as _srv

                sb = _srv._get_supabase()
                performance, prev_picks = None, []

                if sb:
                    # حدّث التوصيات السابقة أولاً
                    active = sb.table("idx_recommendations").select("*").eq("status", "active").execute().data or []
                    prev_picks = [{"symbol": r["symbol"], "confidence": r.get("score", 0)} for r in active]
                    symbols = list(set(r["symbol"] for r in active))
                    prices = {}
                    if symbols and os.getenv("SAHMK_API_KEY"):
                        prices = fetch_prices_full(symbols)
                    # كمّل من الشيت (السعر + أعلى سعر)
                    if len(prices) < len(symbols):
                        sc = next((c for c in df.columns if str(c).strip() == "الرمز"), None) or next((c for c in df.columns if "الرمز" in str(c)), None)
                        pc = next((c for c in df.columns if str(c).strip() == "آخر"), None) or next((c for c in df.columns if "آخر" in str(c)), None)
                        hc = next((c for c in df.columns if str(c).strip() == "أعلى"), None)
                        for _, row in df.iterrows():
                            try:
                                sym = str(row[sc]) if sc else None
                                if sym and sym in symbols and sym not in prices and pc:
                                    px = float(row[pc])
                                    hi = float(row[hc]) if hc and str(row[hc]) not in ("nan", "") else px
                                    prices[sym] = {"price": px, "high": max(hi, px), "low": px}
                            except Exception:
                                pass
                    update_active(prices, sb)
                    update_post_watch(prices, sb)
                    performance = performance_report(sb)

                # التقييم المزدوج
                eval_result = dual_evaluate(analysis, performance, prev_picks)

                # حفظ التوصيات
                saved = 0
                if sb and eval_result.get("picks"):
                    for pick in eval_result["picks"]:
                        orig = next((s for s in analysis["stocks"] if s["symbol"] == pick["symbol"]), {})
                        merged = {**orig, **pick, "reason": pick.get("reasoning", "")}
                        cat = "short_term" if ("يوم" in pick.get("horizon","") or "ساع" in pick.get("horizon","")) else "long_term"
                        create_recommendation(merged, cat, sb)
                        saved += 1

                result["picks_count"] = len(eval_result.get("picks", []))
                result["saved"] = saved
                result["market_note"] = eval_result.get("market_note", "")
                result["evaluated"] = True
            except Exception as e:
                result["eval_error"] = str(e)[:150]
                result["evaluated"] = False

        _last_sync.update(
            time=now_riyadh().isoformat(), tab=tab, status="success",
            stocks=analysis["summary"]["total_stocks"], error=None,
        )
        _log(result)
        return result

    except Exception as e:
        result = {"ok": False, "error": str(e)[:200], "time": now_riyadh().isoformat()}
        _last_sync.update(status="error", error=str(e)[:200], time=now_riyadh().isoformat())
        _log(result)
        return result


def _log(entry):
    _sync_log.append(entry)
    if len(_sync_log) > 20:
        _sync_log.pop(0)


# ═══════ المجدوِل الداخلي ═══════

def start_scheduler():
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        return {"already_running": True}
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    return {"started": True, "schedule": SYNC_TIMES}


def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
    return {"stopped": True}


def _scheduler_loop():
    """يفحص كل 60 ثانية إذا حان وقت المزامنة."""
    global _scheduler_running
    triggered_today = set()

    while _scheduler_running:
        try:
            now = now_riyadh()
            today_key = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")

            # يوم تداول فقط + غير موقوف
            if is_trading_day(now) and not _scheduler_paused:
                for sync_time in SYNC_TIMES:
                    key = f"{today_key}_{sync_time}"
                    if key not in triggered_today and current_time >= sync_time:
                        # تجاوزنا الوقت ولم نزامن بعد
                        # لكن لا نزامن لو فات أكثر من 30 دقيقة
                        sync_h, sync_m = map(int, sync_time.split(":"))
                        diff_min = (now.hour * 60 + now.minute) - (sync_h * 60 + sync_m)
                        if 0 <= diff_min <= 30:
                            run_sync()
                        triggered_today.add(key)

            # تنظيف: لو تغيّر اليوم
            if any(not k.startswith(today_key) for k in triggered_today):
                triggered_today = {k for k in triggered_today if k.startswith(today_key)}

        except Exception:
            pass

        time.sleep(60)
