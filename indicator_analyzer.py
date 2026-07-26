# -*- coding: utf-8 -*-
"""
محلل المؤشرات — محرك التنبؤ المستقل
مبني على مبادئ راصد برو المُقاسة:
  • أعلى-3 إشارات فقط (لا تشبّع)
  • كشف الفخاخ (8+ مشتعلة = عقوبة)
  • المنطقة الميتة (أسبوعي 0→0.7% = أضعف شريحة)
  • التتابع الصحّي والسيولة = أقوى الإشارات
"""
import pandas as pd, numpy as np
from datetime import datetime

# ═══════ تصنيف الأزمنة ═══════
TIMEFRAME_TAGS = {
    "(5د)":"short","(15د)":"short","(30د)":"short",
    "(60د)":"short","(240د)":"short",
    "(يوم)":"mid",
    "(أسبوع)":"long","(شهر)":"long",
}
FRAME_LABELS = {"short":"قصير المدى","mid":"متوسط المدى","long":"طويل المدى"}
FRAME_HORIZON = {"short":"1–3 أيام","mid":"3–7 أيام","long":"أسابيع–أشهر"}
DEFAULT_WEIGHTS = {"short":1.0,"mid":1.5,"long":2.0}

# ═══════ الأعمدة الأساسية ═══════
COL_MAP = {
    "symbol":["الرمز"],
    "name":["الاسم"],
    "price":["آخر","الاغلاق","السعر"],
    "change":["التغير %","التغير%"],
    "high":["أعلى"],
    "low":["أدنى"],
    "volume":["الحجم"],
    "weekly":["التغير الاسبوعي","التغير الاسبوعي %"],
    "monthly":["التغير الشهري","التغير الشهري %"],
    "yearly":["التغير السنوي"],
    "net_liq":["صافي السيولة"],
    "liq_pct":["نسبة السيولة %"],
    "rsi":["(14)RSI","RSI"],
    "mfi":["(14)MFI","MFI"],
    "pe":["مكرر الارباح"],
    "eps":["ربحية السهم"],
    "ma50_dist":["% عن متوسط 50"],
    "ma100_dist":["% عن متوسط 100"],
    "ma200_dist":["% عن متوسط 200"],
    "accum":["التجميع وتصريف"],
    "alerts":["إشعار"],
}

def _find_col(df, aliases):
    for a in aliases:
        for c in df.columns:
            if a in c: return c
    return None

def _safe(v, d=0.0):
    try:
        f=float(v)
        return f if not np.isnan(f) else d
    except: return d

def _is_binary(s):
    try:
        n = pd.to_numeric(s.dropna(), errors='coerce').dropna().unique()
        return len(n)>0 and set(n).issubset({0.0,1.0})
    except: return False

# ═══════ تحليل رئيسي ═══════
def analyze_file(filepath: str) -> dict:
    return analyze_dataframe(pd.read_excel(filepath))

def analyze_dataframe(df: pd.DataFrame) -> dict:
    # تصنيف الأعمدة
    bin_cols = {"short":[],"mid":[],"long":[]}
    for col in df.columns:
        for tag, frame in TIMEFRAME_TAGS.items():
            if tag in col and _is_binary(df[col]):
                bin_cols[frame].append(col); break

    # تحديد الأعمدة الأساسية
    col_idx = {k: _find_col(df, v) for k,v in COL_MAP.items()}

    # تحليل كل سهم
    stocks = []
    for _, row in df.iterrows():
        sym = str(row.get(col_idx["symbol"] or "الرمز",""))
        if not sym or sym=="nan": continue
        name  = str(row.get(col_idx["name"] or "",""))
        price = _safe(row.get(col_idx["price"] or "",0))
        chg   = _safe(row.get(col_idx["change"] or "",0))
        wkly  = _safe(row.get(col_idx["weekly"] or "",0))
        mnth  = _safe(row.get(col_idx["monthly"] or "",0))
        net_l = _safe(row.get(col_idx["net_liq"] or "",0))
        rsi   = _safe(row.get(col_idx["rsi"] or "",0))
        mfi   = _safe(row.get(col_idx["mfi"] or "",0))

        # عدّ الإشارات لكل إطار
        frame_data = {}
        total_active = 0
        for frame in ["short","mid","long"]:
            bc = bin_cols[frame]
            active = [c for c in bc if _safe(row.get(c,0))==1.0]
            frame_data[frame] = {"count":len(active),"total":len(bc),
                                 "pct":round(len(active)/len(bc)*100,1) if bc else 0}
            total_active += len(active)

        # ═══ محرك التنبؤ (مبادئ راصد برو) ═══

        # ① أعلى-3 مكافآت
        bonuses = []
        price_quiet  = abs(chg) < 2.5 and abs(wkly) < 5
        price_v_quiet= abs(chg) < 1.5 and abs(wkly) < 3
        net_rising   = net_l > 0

        # تتابع صحّي (أقوى إشارة: +11%)
        s_cnt = frame_data["short"]["count"]
        m_cnt = frame_data["mid"]["count"]
        l_cnt = frame_data["long"]["count"]
        cascade = s_cnt >= 1 and m_cnt >= 1 and l_cnt == 0 and price_quiet
        if cascade: bonuses.append(("تتابع صحّي", 30))

        # سيولة متراكمة (+13%)
        if net_rising and price_quiet:
            bonuses.append(("سيولة متراكمة", 28))

        # تجميع صامت
        if price_quiet and net_rising and 1 <= total_active <= 5:
            bonuses.append(("تجميع صامت", 25))

        # بوادر مبكرة
        if s_cnt >= 1 and l_cnt <= 1 and price_quiet:
            bonuses.append(("بوادر مبكرة", 22))

        # ثبات فوق المتوسط
        ma50 = _safe(row.get(col_idx["ma50_dist"] or "",0))
        if 0 < ma50 < 5:
            bonuses.append(("قرب من المتوسط", 15))

        # قصيرة تتصاعد
        if s_cnt >= 2:
            bonuses.append(("قصيرة نشطة", 14))

        # أعلى 3 فقط
        bonuses.sort(key=lambda x: x[1], reverse=True)
        top3 = bonuses[:3]
        pre_launch = sum(b[1] for b in top3)

        # ② العقوبات (كشف الفخاخ)
        penalties = []
        if total_active >= 8 and (wkly > 5 or mnth > 15):
            penalties.append(("فخ كلاسيكي", -45))
        if total_active >= 8:
            penalties.append(("اشتعال كامل", -30))
        if chg > 4 or wkly > 8 or mnth > 20:
            penalties.append(("صعود متأخر", -25))
        if total_active >= 11:
            penalties.append(("اشتعال مفرط", -20))

        penalty_total = sum(p[1] for p in penalties)
        pre_launch = max(0, min(100, pre_launch + penalty_total))

        # ③ طبقة التعديل (المنطقة الميتة)
        adj = 0
        if 0.7 <= wkly < 1.61:    adj += 30   # الربع الذهبي (80%)
        elif wkly >= 1.61:         adj += 20
        elif -0.53 <= wkly < 0.7:  adj -= 25   # المنطقة الميتة (51%)
        elif wkly >= 0:            adj += 5

        if cascade: adj += 18
        if net_rising and price_quiet: adj += 14

        # نقاط الرهان النهائية
        classify = "متوازن" if s_cnt>0 and l_cnt>0 else "مضاربي" if s_cnt>0 else "استثماري" if l_cnt>0 else "محايد"
        type_bonus = {"متوازن":6,"استثماري":4,"مضاربي":3,"محايد":0}[classify]
        bet_score = round(pre_launch * 0.9 + type_bonus + l_cnt * 1.2 + adj, 1)
        bet_score = max(0, bet_score)

        # تصنيف الاتجاه
        if l_cnt > 0 and m_cnt > 0: trend = "صاعد قوي"
        elif l_cnt > 0:             trend = "صاعد طويل"
        elif m_cnt > 0 and s_cnt > 0:trend = "زخم قصير"
        elif m_cnt > 0:             trend = "زخم متوسط"
        elif s_cnt > 0:             trend = "إشارة قصيرة"
        else:                       trend = "محايد"

        # هل فخ؟
        is_trap = len(penalties) > 0 and total_active >= 8

        stocks.append({
            "symbol":sym, "name":name, "price":price, "change_pct":chg,
            "bet_score":bet_score, "pre_launch":pre_launch,
            "weighted_score":bet_score,  # للتوافق مع الواجهة
            "trend":trend, "classify":classify, "is_trap":is_trap,
            "frame_scores":frame_data, "total_active":total_active,
            "top3_signals":[b[0] for b in top3],
            "penalties":[p[0] for p in penalties],
            "weekly_change":wkly, "monthly_change":mnth,
            "rsi":rsi, "mfi":mfi, "net_liquidity":net_l,
            "pe":_safe(row.get(col_idx["pe"] or "",0)),
            "eps":_safe(row.get(col_idx["eps"] or "",0)),
        })

    stocks.sort(key=lambda x: x["bet_score"], reverse=True)
    recs = _build_recommendations(stocks)
    mood = _market_mood(stocks)

    return {
        "summary":{"total_stocks":len(stocks),
                    "analysis_time":datetime.now().isoformat(),
                    "indicator_counts":{f:{"binary":len(bin_cols[f])} for f in bin_cols},
                    "market_mood":mood},
        "stocks":stocks,
        "recommendations":recs,
    }

def _market_mood(stocks):
    if not stocks: return {"state":"غير محدد","positive_pct":0}
    pos = sum(1 for s in stocks if s["change_pct"]>0)
    pct = round(pos/len(stocks)*100,1)
    state = "صاعد" if pct>=65 else "هابط" if pct<35 else "متذبذب"
    strong = sum(1 for s in stocks if s["bet_score"]>=40)
    return {"state":state,"positive_count":pos,
            "negative_count":len(stocks)-pos,
            "strong_signals_count":strong,"positive_pct":pct}

def _build_recommendations(stocks):
    short_term, long_term, speculative, traps = [], [], [], []
    for s in stocks:
        fs = s["frame_scores"]
        s_pct = fs["short"]["pct"]
        m_pct = fs["mid"]["pct"]
        l_pct = fs["long"]["pct"]

        if s["is_trap"]:
            traps.append({**s, "reason":"فخ: مؤشرات كثيرة مشتعلة مع صعود سابق"})
            continue

        # الربع الذهبي (أسبوعي 0.7–1.6%) + تتابع أو سيولة
        golden = 0.7 <= s["weekly_change"] < 1.61
        has_cascade = "تتابع صحّي" in s.get("top3_signals",[])

        # فرص قريبة: توافق قصير+متوسط + سعر هادئ
        if s_pct>=15 and m_pct>=20 and abs(s["change_pct"])<3 and s["bet_score"]>=30:
            short_term.append({**s,"reason":"توافق إشارات + سعر هادئ" + (" + الربع الذهبي" if golden else ""),
                               "target_horizon":"1–5 أيام","confidence":s["bet_score"]})

        # فرص طويلة: إشارات أسبوعية/شهرية قوية
        if l_pct>=25 and s["bet_score"]>=25 and s["weekly_change"]>=-8:
            long_term.append({**s,"reason":"إشارات طويلة قوية" + (" + تتابع صحّي" if has_cascade else ""),
                              "target_horizon":"أسابيع–أشهر","confidence":l_pct})

        # مضاربة: قصيرة نشطة + تغير إيجابي
        if s_pct>=30 and s["change_pct"]>0 and s["total_active"]<=7:
            speculative.append({**s,"reason":"إشارات قصيرة نشطة بدون اشتعال",
                                "target_horizon":"ساعات–يومين","confidence":s_pct})

    for lst in [short_term, long_term, speculative]:
        lst.sort(key=lambda x: x.get("confidence",0), reverse=True)

    return {"short_term":short_term[:7],"long_term":long_term[:7],
            "speculative":speculative[:5],"traps":traps[:5]}

if __name__=="__main__":
    import json, sys
    fp = sys.argv[1] if len(sys.argv)>1 else "/mnt/user-data/uploads/مؤشرات_عبدالعزيز_العتيبي.xlsx"
    r = analyze_file(fp)
    print(f"تحليل {r['summary']['total_stocks']} سهم · السوق: {r['summary']['market_mood']['state']}")
    for cat,lbl in [("short_term","🎯 قريبة"),("long_term","📈 طويلة"),("speculative","⚡ مضاربة"),("traps","⚠️ فخاخ")]:
        items = r["recommendations"][cat]
        if items: print(f"\n{lbl} ({len(items)}):")
        for s in items: print(f"  {s['symbol']:6} {s['name']:18} نقاط:{s['bet_score']:>5}  {s.get('reason','')}")
