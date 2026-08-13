# -*- coding: utf-8 -*-
"""
نظام التتبع والتعلّم
صلاحية 3 أيام تداول · حد أقصى 5 · هدف 1.5% ذروة
ما بعد الحسم: 14 يوم تداول إضافية لقياس الدقة
"""
from datetime import date, datetime, timedelta
from market_clock import add_trading_days, now_riyadh, MARKET_CLOSE

TARGET_PCT       = 1.5
VALIDITY_DAYS    = 3
MAX_DAYS         = 5
FLAT_BAND        = 1.0
POST_WATCH_DAYS  = 14

# المدى البعيد — هدف أكبر ومدة أطول
LT_TARGET_PCT    = 5.0    # هدف +5%
LT_MAX_DAYS      = 20     # حتى 20 يوم تداول (~شهر)


def _is_past_expiry(max_expiry: str) -> bool:
    """
    هل انتهت مدة التوصية فعلياً؟
    التوصية تبقى سارية طوال يوم الانتهاء حتى إغلاق التداول (3:20).
    لا تُحسم خلال التداول — فقط بعد إغلاق يوم الانتهاء أو الأيام التالية.
    """
    if not max_expiry or max_expiry == "9999":
        return False
    try:
        exp = datetime.strptime(max_expiry[:10], "%Y-%m-%d").date()
    except Exception:
        return False
    now = now_riyadh()
    today = now.date()
    # لسه ما وصلنا يوم الانتهاء → سارية
    if today < exp:
        return False
    # بعد يوم الانتهاء → منتهية
    if today > exp:
        return True
    # نفس يوم الانتهاء → تبقى سارية حتى إغلاق التداول
    return now.time() > MARKET_CLOSE

def tick_size(price: float) -> float:
    """
    الخطوة السعرية (tick) في السوق السعودي حسب الفئة السعرية:
    < 25 ريال    → 0.01 (هللة)
    25–50 ريال   → 0.02
    50–100 ريال  → 0.05 (5 هللات)
    ≥ 100 ريال   → 0.10
    """
    if price < 25:
        return 0.01
    elif price < 50:
        return 0.02
    elif price < 100:
        return 0.05
    else:
        return 0.10


def round_to_tick(price: float, direction: str = "nearest") -> float:
    """
    يقرّب السعر لأقرب خطوة سعرية صحيحة قابلة للتداول.
    direction: nearest (الأقرب) · up (لأعلى) · down (لأسفل)
    """
    if not price or price <= 0:
        return price
    tk = tick_size(price)
    steps = price / tk
    if direction == "up":
        import math
        steps = math.ceil(steps)
    elif direction == "down":
        import math
        steps = math.floor(steps)
    else:
        steps = round(steps)
    # تقريب لتفادي أخطاء الفاصلة العائمة
    result = round(steps * tk, 2)
    return result


def create_recommendation(stock, category, supabase=None):
    today = date.today()
    entry = float(stock["price"])
    is_lt = category == "long_term"
    # الهدف والمدة حسب النوع
    tgt_pct = LT_TARGET_PCT if is_lt else TARGET_PCT
    max_d = LT_MAX_DAYS if is_lt else MAX_DAYS
    val_d = LT_MAX_DAYS if is_lt else VALIDITY_DAYS
    # الهدف مقرّب لأقرب خطوة سعرية صحيحة (لأعلى)
    target = round_to_tick(entry * (1 + tgt_pct/100), "up")
    expiry = add_trading_days(datetime.combine(today, datetime.min.time()), val_d).date()
    max_exp = add_trading_days(datetime.combine(today, datetime.min.time()), max_d).date()

    # ── منع التكرار: نفس السهم + نفس النوع فقط (يُسمح بنفس السهم في نوعين) ──
    if supabase:
        try:
            existing = supabase.table("idx_recommendations").select("*") \
                .eq("symbol", stock["symbol"]).eq("status", "active") \
                .eq("category", category) \
                .limit(1).execute().data
            if existing:
                ex = existing[0]
                # حدّث الثقة والنقاط والسبب (مستجدات) دون تغيير تاريخ الدخول
                supabase.table("idx_recommendations").update({
                    "confidence": stock.get("confidence", 0),
                    "score": stock.get("bet_score", 0),
                    "reason": stock.get("reason", ""),
                    "trend": stock.get("trend", ""),
                    "top3_signals": stock.get("top3_signals", []),
                    "updated_at": datetime.now().isoformat(),
                }).eq("id", ex["id"]).execute()
                ex["_updated"] = True
                return ex
        except Exception:
            pass

    # وسم التوصية برقم نسخة الأوزان التي أنتجتها (لقياس أداء كل نسخة)
    try:
        from weights_manager import get_active_version
        weights_version = get_active_version().get("version", 0)
    except Exception:
        weights_version = 0

    rec = {
        "symbol":stock["symbol"],"name":stock["name"],"category":category,
        "entry_price":entry,"target_price":target,"target_pct":tgt_pct,
        "score":stock.get("bet_score",0),
        "confidence":stock.get("confidence",0),
        "trend":stock.get("trend",""),
        "reason":stock.get("reason",""),
        "weights_version":weights_version,
        "signals_summary":{f:stock.get("frame_scores",{}).get(f,{}) for f in ["short","mid","long"]},
        "top3_signals":stock.get("top3_signals",[]),
        # بيانات تحليلية للدراسة
        "indicators":{
            "rsi":stock.get("rsi",0),
            "rsi_state":stock.get("rsi_state",""),
            "mfi":stock.get("mfi",0),
            "net_liquidity":stock.get("net_liquidity",0),
            "weekly_change":stock.get("weekly_change",0),
            "monthly_change":stock.get("monthly_change",0),
            "pre_launch":stock.get("pre_launch",0),
            "total_active":stock.get("total_active",0),
            "penalties":stock.get("penalties",[]),
            "long_term_quality":stock.get("long_term_quality",False),
            "pe":stock.get("pe",0),
        },
        "appeared_date":today.isoformat(),
        "expiry_date":expiry.isoformat(),"max_expiry_date":max_exp.isoformat(),
        "status":"active","outcome":None,
        "highest_price":entry,"lowest_price":entry,"peak_pct":0.0,
        "current_price":entry,"current_pct":0.0,
        "closed_date":None,"post_watch":False,
        "post_watch_peak":0.0,"post_watch_hit":False,
    }
    if supabase:
        try: supabase.table("idx_recommendations").upsert(rec, on_conflict="symbol,appeared_date,category").execute()
        except Exception as e: rec["_save_error"] = str(e)[:150]
    return rec

def _extract(price_data):
    """يستخرج (current, high) — يقبل رقم أو dict فيه high."""
    if isinstance(price_data, dict):
        cur = price_data.get("price")
        hi = price_data.get("high") or cur
        return cur, hi
    return price_data, price_data


def update_active(prices, supabase=None):
    if not supabase: return {"error":"Supabase غير متاح"}
    today = date.today()
    stats = {"success":0,"flat":0,"failed":0,"still_active":0}
    try: active = supabase.table("idx_recommendations").select("*").eq("status","active").execute().data or []
    except Exception as e: return {"error":str(e)[:150]}
    for rec in active:
        cur, day_high = _extract(prices.get(rec["symbol"]))
        if cur is None: stats["still_active"]+=1; continue
        entry = rec["entry_price"]
        highest = max(rec.get("highest_price",entry), cur, day_high)
        lowest = min(rec.get("lowest_price",entry), cur)
        peak = round((highest-entry)/entry*100,2)
        cur_pct = round((cur-entry)/entry*100,2)
        upd = {"highest_price":highest,"lowest_price":lowest,"peak_pct":peak,
               "current_price":cur,"current_pct":cur_pct}
        rec_target = rec.get("target_pct", TARGET_PCT) or TARGET_PCT
        if peak >= rec_target:
            upd.update(status="closed",outcome="success",closed_date=today.isoformat())
            # أعلى سعر عند تحقيق الهدف = يبدأ التتبع بعده
            upd["post_target_high"] = highest
            upd["post_target_pct"] = peak
            stats["success"]+=1
        elif _is_past_expiry(rec.get("max_expiry_date", "9999")):
            chg = round((cur-entry)/entry*100,2)
            upd["outcome"] = "flat" if abs(chg)<=FLAT_BAND else "failed"
            upd.update(status="closed",closed_date=today.isoformat(),post_watch=True)
            stats[upd["outcome"]]+=1
        else: stats["still_active"]+=1
        try: supabase.table("idx_recommendations").update(upd).eq("id",rec["id"]).execute()
        except: pass

    # ── تتبع أعلى سعر بعد تحقيق الهدف (للناجحة) ──
    try:
        succeeded = supabase.table("idx_recommendations").select("*") \
            .eq("outcome","success").execute().data or []
        for rec in succeeded:
            cur, day_high = _extract(prices.get(rec["symbol"]))
            if cur is None: continue
            entry = rec["entry_price"]
            prev_pth = rec.get("post_target_high") or rec.get("highest_price") or entry
            new_high = max(prev_pth, cur, day_high)
            if new_high > prev_pth:
                pth_pct = round((new_high-entry)/entry*100,2)
                supabase.table("idx_recommendations").update({
                    "post_target_high": new_high, "post_target_pct": pth_pct,
                }).eq("id", rec["id"]).execute()
    except Exception:
        pass

    return stats

def update_post_watch(prices, supabase=None):
    if not supabase: return {}
    try: watched = supabase.table("idx_recommendations").select("*").eq("post_watch",True).in_("outcome",["failed","flat"]).execute().data or []
    except: return {}
    late_hits = 0
    for rec in watched:
        cur, day_high = _extract(prices.get(rec["symbol"]))
        if cur is None: continue
        entry = rec["entry_price"]
        cd = rec.get("closed_date")
        if not cd: continue
        try: watch_end = add_trading_days(datetime.combine(date.fromisoformat(cd),datetime.min.time()),POST_WATCH_DAYS).date()
        except: continue
        peak = max(rec.get("post_watch_peak",0), round((max(cur,day_high)-entry)/entry*100,2))
        hit = peak >= TARGET_PCT
        upd = {"post_watch_peak":peak,"post_watch_hit":hit}
        if date.today() > watch_end: upd["post_watch"]=False
        if hit: late_hits+=1
        try: supabase.table("idx_recommendations").update(upd).eq("id",rec["id"]).execute()
        except: pass
    return {"watched":len(watched),"late_hits":late_hits}

def performance_report(supabase=None):
    if not supabase: return {"error":"Supabase غير متاح"}
    try: all_r = supabase.table("idx_recommendations").select("*").order("appeared_date",desc=True).limit(2000).execute().data or []
    except Exception as e: return {"error":str(e)[:150]}
    # حماية: إزالة التكرار (نفس السهم+الدخول) — تُحسب مرة واحدة
    # الأولوية: نشطة > ناجحة > غيرها
    _uniq = {}
    for r in all_r:
        key = (r["symbol"], r.get("entry_price"), r.get("category"))
        if key not in _uniq:
            _uniq[key] = r
        else:
            ex = _uniq[key]
            # فضّل النشطة، ثم الناجحة
            if r["status"] == "active" and ex["status"] != "active":
                _uniq[key] = r
            elif r.get("outcome") == "success" and ex.get("outcome") != "success" and ex["status"] != "active":
                _uniq[key] = r
    all_r = list(_uniq.values())

    active = [r for r in all_r if r["status"]=="active"]
    closed = [r for r in all_r if r["status"]=="closed"]
    success = [r for r in closed if r["outcome"]=="success"]
    failed = [r for r in closed if r["outcome"]=="failed"]
    flat = [r for r in closed if r["outcome"]=="flat"]
    late = [r for r in closed if r.get("post_watch_hit")]
    # ═══ فصل القصير عن البعيد (أهداف مختلفة = نسب منفصلة) ═══
    short_closed = [r for r in closed if r.get("category") != "long_term"]
    long_closed = [r for r in closed if r.get("category") == "long_term"]
    short_succ = [r for r in short_closed if r["outcome"]=="success"]
    long_succ = [r for r in long_closed if r["outcome"]=="success"]

    # النسبة الرئيسية = القصير فقط (الاستراتيجية الأساسية، هدف +1.5%)
    rate = round(len(short_succ)/len(short_closed)*100,1) if short_closed else 0
    avg_peak = round(sum(r.get("peak_pct",0) for r in short_closed)/len(short_closed),2) if short_closed else 0
    # نسبة "بلا حركة" للقصير (للعلم فقط — غير ناجحة، لكن مهمة للتطوير)
    short_flat = [r for r in short_closed if r["outcome"]=="flat"]
    flat_rate = round(len(short_flat)/len(short_closed)*100,1) if short_closed else 0
    short_fail = [r for r in short_closed if r["outcome"]=="failed"]
    short_active = [r for r in active if r.get("category") != "long_term"]
    # نسبة المدى البعيد منفصلة (هدف +5%)
    lt_rate = round(len(long_succ)/len(long_closed)*100,1) if long_closed else 0
    lt_avg_peak = round(sum(r.get("peak_pct",0) for r in long_closed)/len(long_closed),2) if long_closed else 0

    by_cat = {}
    for cat in ["short_term","long_term","speculative"]:
        cc = [r for r in closed if r.get("category")==cat]
        cs = [r for r in cc if r["outcome"]=="success"]
        by_cat[cat] = {"total":len(cc),"success":len(cs),
                       "rate":round(len(cs)/len(cc)*100,1) if cc else 0}
    # دقة الإشارات
    sig_stats = {}
    for r in closed:
        for sig in r.get("top3_signals",[]):
            if sig not in sig_stats: sig_stats[sig]={"total":0,"success":0}
            sig_stats[sig]["total"]+=1
            if r["outcome"]=="success": sig_stats[sig]["success"]+=1
    sig_list = [{"signal":k,"total":v["total"],"success":v["success"],
                 "rate":round(v["success"]/v["total"]*100,1) if v["total"]>=3 else 0}
                for k,v in sig_stats.items() if v["total"]>=3]
    sig_list.sort(key=lambda x:x["rate"],reverse=True)
    return {"total":len(all_r),"active":len(active),"closed":len(closed),
            "success":len(success),"failed":len(failed),"flat":len(flat),
            "success_rate":rate,"avg_peak":avg_peak,"flat_rate":flat_rate,
            "short_success_n":len(short_succ),"short_fail_n":len(short_fail),
            "short_flat_n":len(short_flat),"short_active_n":len(short_active),
            "short_closed":len(short_closed),"short_success":len(short_succ),
            "longterm":{"closed":len(long_closed),"success":len(long_succ),
                        "rate":lt_rate,"avg_peak":lt_avg_peak,"target":"+5%"},
            "late_success":len(late),
            "late_success_pct":round(len(late)/len(failed)*100,1) if failed else 0,
            "by_category":by_cat,"signal_stats":sig_list,
            "mature":len(short_closed)>=20}
