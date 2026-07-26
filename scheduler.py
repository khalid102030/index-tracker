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

SYNC_TIMES = ["10:30", "12:00", "14:00", "16:00"]
_scheduler_running = False
_scheduler_thread = None
_last_sync = {"time": None, "tab": None, "status": None, "stocks": 0, "error": None}
_sync_log = []  # آخر 20 محاولة


def get_sync_status() -> dict:
    return {
        "scheduler_active": _scheduler_running,
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


def run_sync(force: bool = False) -> dict:
    """يسحب من Google Sheets ويحلّل — يكشف التكرار تلقائياً."""
    global _last_sync

    sheet_url = os.getenv("SHEET_URL", "")
    if not sheet_url:
        # محاولة من config.json
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                    sheet_url = cfg.get("sheet_url", "")
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

        # كشف التكرار: هل هذا نفس التبويب اللي سحبناه قبل؟
        if not force and _last_sync.get("tab") == tab:
            result = {
                "ok": True, "skipped": True,
                "message": f"⚠️ آخر تحديث لا يزال ساري — لم تتغير البيانات منذ {_last_sync.get('time', '?')}",
                "tab": tab, "time": now_riyadh().isoformat(),
            }
            _last_sync["status"] = "no_update"
            _log(result)
            return result

        # تحليل جديد
        df = snap["df"]
        analysis = analyze_dataframe(df)
        analysis["source"] = {
            "type": "google_sheets_auto",
            "tab": tab,
            "snapshot_time": snap_time.isoformat() if snap_time else None,
            "market_status": classify_snapshot_time(),
        }

        # تحديث الكاش العام (يستخدمه التقييم)
        # نستورد _last_analysis من server مباشرة
        try:
            import server
            server._last_analysis["data"] = analysis
        except Exception:
            pass

        _last_sync.update(
            time=now_riyadh().isoformat(),
            tab=tab,
            status="success",
            stocks=analysis["summary"]["total_stocks"],
            error=None,
        )

        result = {
            "ok": True, "skipped": False,
            "tab": tab, "stocks": analysis["summary"]["total_stocks"],
            "mood": analysis["summary"]["market_mood"]["state"],
            "time": now_riyadh().isoformat(),
            "recommendations": {
                k: len(v) for k, v in analysis.get("recommendations", {}).items()
            },
        }
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

            # يوم تداول فقط
            if is_trading_day(now):
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
