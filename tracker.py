# -*- coding: utf-8 -*-
"""
نظام التتبع والتعلّم
صلاحية 3 أيام تداول · حد أقصى 5 · هدف 1.5% ذروة
ما بعد الحسم: 14 يوم تداول إضافية لقياس الدقة
"""
from datetime import date, datetime, timedelta
from market_clock import add_trading_days

TARGET_PCT       = 1.5
VALIDITY_DAYS    = 3
MAX_DAYS         = 5
FLAT_BAND        = 1.0
POST_WATCH_DAYS  = 14

def create_recommendation(stock, category, supabase=None):
    today = date.today()
    entry = float(stock["price"])
    target = round(entry * (1 + TARGET_PCT/100), 3)
    expiry = add_trading_days(datetime.combine(today, datetime.min.time()), VALIDITY_DAYS).date()
    max_exp = add_trading_days(datetime.combine(today, datetime.min.time()), MAX_DAYS).date()

    # ── منع التكرار: لو فيه توصية جارية لنفس السهم، حدّث قوتها فقط ──
    if supabase:
        try:
            existing = supabase.table("idx_recommendations").select("*") \
                .eq("symbol", stock["symbol"]).eq("status", "active") \
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

    rec = {
        "symbol":stock["symbol"],"name":stock["name"],"category":category,
        "entry_price":entry,"target_price":target,"target_pct":TARGET_PCT,
        "score":stock.get("bet_score",0),
        "confidence":stock.get("confidence",0),
        "trend":stock.get("trend",""),
        "reason":stock.get("reason",""),
        "signals_summary":{f:stock.get("frame_scores",{}).get(f,{}) for f in ["short","mid","long"]},
        "top3_signals":stock.get("top3_signals",[]),
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
        # أعلى سعر = الأعلى بين (المحفوظ، السعر الحالي، أعلى سعر اليوم)
        highest = max(rec.get("highest_price",entry), cur, day_high)
        lowest = min(rec.get("lowest_price",entry), cur)
        peak = round((highest-entry)/entry*100,2)
        cur_pct = round((cur-entry)/entry*100,2)
        upd = {"highest_price":highest,"lowest_price":lowest,"peak_pct":peak,
               "current_price":cur,"current_pct":cur_pct}
        if peak >= TARGET_PCT:
            upd.update(status="closed",outcome="success",closed_date=today.isoformat())
            stats["success"]+=1
        elif str(today) > rec.get("max_expiry_date","9999"):
            chg = round((cur-entry)/entry*100,2)
            upd["outcome"] = "flat" if abs(chg)<=FLAT_BAND else "failed"
            upd.update(status="closed",closed_date=today.isoformat(),post_watch=True)
            stats[upd["outcome"]]+=1
        else: stats["still_active"]+=1
        try: supabase.table("idx_recommendations").update(upd).eq("id",rec["id"]).execute()
        except: pass
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
    active = [r for r in all_r if r["status"]=="active"]
    closed = [r for r in all_r if r["status"]=="closed"]
    success = [r for r in closed if r["outcome"]=="success"]
    failed = [r for r in closed if r["outcome"]=="failed"]
    flat = [r for r in closed if r["outcome"]=="flat"]
    late = [r for r in closed if r.get("post_watch_hit")]
    rate = round(len(success)/len(closed)*100,1) if closed else 0
    avg_peak = round(sum(r.get("peak_pct",0) for r in closed)/len(closed),2) if closed else 0
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
            "success_rate":rate,"avg_peak":avg_peak,
            "late_success":len(late),
            "late_success_pct":round(len(late)/len(failed)*100,1) if failed else 0,
            "by_category":by_cat,"signal_stats":sig_list,
            "mature":len(closed)>=20}
