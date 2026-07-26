# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════
  المُقيّم النهائي — حارس البوابة
═══════════════════════════════════════════════════════════════════════
يأخذ أفضل المرشحين من المحلّل الرياضي ويعرضهم على Claude كمحلل
فني متخصص. Claude يقيّم كل مرشح ويقرّر:
  • هل فعلاً يستحق المراهنة؟
  • ما مستوى الثقة؟
  • ما المبرر الفني الدقيق؟

الناتج: 3–5 أسهم فقط. أقل = أفضل.

الفلسفة (من راصد برو):
  "السهم الذي تشتعل فيه كل المؤشرات هو غالباً الذي انتهت حركته.
   المطلوب: اصطياد التجميع الصامت قبل الانطلاق."
═══════════════════════════════════════════════════════════════════════
"""
import json
import requests
from datetime import datetime

MAX_CANDIDATES = 15        # أقصى عدد يُعرض على المُقيّم
MAX_FINAL_PICKS = 5        # أقصى عدد توصيات نهائية
MIN_CONFIDENCE = 7         # حد أدنى للثقة (من 10)

EVAL_SYSTEM = """أنت محلل فني متقدم (CMT Level 3) ومتخصص في البيانات الكمية.
مهمتك الوحيدة: تقييم مرشحين مختارين بواسطة خوارزمية تنبؤية وتحديد أيّهم يستحق المراهنة عليه فعلاً.

أنت حارس بوابة صارم — وظيفتك أن تمنع التوصيات الضعيفة من المرور.
الهدف: أقل عدد ممكن من التوصيات بأعلى ثقة ممكنة.

═══ المبادئ المُقاسة (من 165 توصية محسومة) ═══

① الفخ الكلاسيكي: سهم مشتعل فيه 8+ مؤشرات مع صعود أسبوعي > 5% = غالباً انتهت حركته
② المنطقة الميتة: أسبوعي بين 0% و +0.7% = أضعف شريحة (51% نجاح فقط)
③ الربع الذهبي: أسبوعي بين +0.7% و +1.6% = أقوى شريحة (80% نجاح)
④ ثلاث إشارات فقط لها قيمة تنبؤية حقيقية:
   - تتابع صحّي (قصيرة+متوسطة نشطة، طويلة لا) = +11%
   - سيولة متراكمة (صافي سيولة صاعد مع سعر هادئ) = +13%
   - حجم نسبي عالي ≥ 1.75 = +16%
⑤ باقي الإشارات (تجميع صامت، بوادر مبكرة، سيولة صامتة) = ضجيج (0% إلى -4%)
⑥ بذرة الحركة ضرورية: السكون التام ليس مثالياً — يحتاج +0.7% أسبوعياً

═══ معايير القبول ═══

• سعر هادئ (تغير يومي < 2.5%، أسبوعي < 5%)
• سيولة صاعدة أو متراكمة
• مؤشرات قليلة نشطة (1–5) لا كثيرة (تشبّع)
• يفضّل الربع الذهبي (أسبوعي 0.7–1.6%)
• لا فخاخ (8+ مشتعلة مع صعود سابق)

═══ معايير الرفض ═══

• صعود حاد سابق (أسبوعي > 8% أو شهري > 20%)
• مؤشرات مشتعلة بالكامل (8+)
• في المنطقة الميتة بدون إشارة تعويضية
• تضارب بين الإشارات (قصيرة إيجابية + طويلة سلبية)
• سيولة هابطة"""

EVAL_PROMPT = """هؤلاء {count} مرشح اختارتهم الخوارزمية التنبؤية من {total} سهم:

{candidates_json}

═══ المطلوب ═══

1. قيّم كل مرشح بناءً على المبادئ المُقاسة
2. اختر فقط الأسهم التي تستحق المراهنة عليها فعلاً (3–5 حد أقصى، وقد لا تختار أياً)
3. لكل سهم مختار: مستوى ثقة (1–10)، مبرر فني مختصر، والأفق الزمني

⚠️ إذا لم تجد مرشحاً يستحق ثقة ≥7، قل ذلك بوضوح ولا تخفض معاييرك.

أجب بصيغة JSON فقط:
{{
  "market_note": "ملاحظة عامة عن حالة السوق في جملة واحدة",
  "picks": [
    {{
      "symbol": "الرمز",
      "name": "الاسم",
      "confidence": رقم 1-10,
      "horizon": "الأفق (ساعات / 1-3 أيام / أسبوع)",
      "entry_zone": "منطقة الدخول المقترحة",
      "reasoning": "المبرر الفني في 2-3 جمل",
      "key_signal": "أقوى إشارة واحدة",
      "risk": "أهم مخاطرة"
    }}
  ],
  "rejected_notable": [
    {{
      "symbol": "الرمز",
      "reason": "سبب الرفض في جملة"
    }}
  ]
}}"""


def evaluate_candidates(analysis_result: dict, claude_key: str) -> dict:
    """
    يأخذ نتائج التحليل ويعرض أفضل المرشحين على Claude للتقييم النهائي.

    analysis_result: ناتج analyze_dataframe()
    claude_key: مفتاح Claude API

    يرجّع:
      - picks: التوصيات النهائية (3–5)
      - rejected: المرفوضين مع الأسباب
      - market_note: ملاحظة عن السوق
      - eval_time: وقت التقييم
    """
    if not claude_key:
        return {"error": "مفتاح Claude غير موجود", "picks": []}

    stocks = analysis_result.get("stocks", [])
    recs = analysis_result.get("recommendations", {})

    # جمع أفضل المرشحين من كل الفئات (بدون تكرار)
    seen = set()
    candidates = []

    # أولوية 1: الفرص القريبة (أقوى احتمالية)
    for s in recs.get("short_term", []):
        if s["symbol"] not in seen and not s.get("is_trap"):
            seen.add(s["symbol"])
            candidates.append(_slim(s, "قريبة"))

    # أولوية 2: أعلى نقاط رهان عموماً
    for s in stocks:
        if len(candidates) >= MAX_CANDIDATES:
            break
        if s["symbol"] not in seen and not s.get("is_trap") and s["bet_score"] >= 30:
            seen.add(s["symbol"])
            candidates.append(_slim(s, "عامة"))

    # أولوية 3: طويلة المدى
    for s in recs.get("long_term", []):
        if len(candidates) >= MAX_CANDIDATES:
            break
        if s["symbol"] not in seen and not s.get("is_trap"):
            seen.add(s["symbol"])
            candidates.append(_slim(s, "طويلة"))

    if not candidates:
        return {"picks": [], "market_note": "لا يوجد مرشحون مناسبون",
                "rejected": [], "eval_time": datetime.now().isoformat()}

    # إعداد البرومبت
    cand_json = json.dumps(candidates, ensure_ascii=False, indent=1)
    prompt = EVAL_PROMPT.format(
        count=len(candidates),
        total=analysis_result["summary"]["total_stocks"],
        candidates_json=cand_json
    )

    # استدعاء Claude
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": claude_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 2000,
                "system": EVAL_SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )

        if r.status_code not in (200, 201):
            return {"error": f"Claude HTTP {r.status_code}", "picks": [],
                    "raw": r.text[:300]}

        data = r.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        # تنظيف وتحليل JSON
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)

        # إثراء بالبيانات الأصلية
        picks = result.get("picks", [])
        stock_map = {s["symbol"]: s for s in stocks}

        for pick in picks:
            sym = pick.get("symbol", "")
            orig = stock_map.get(sym, {})
            pick["price"] = orig.get("price", 0)
            pick["change_pct"] = orig.get("change_pct", 0)
            pick["bet_score"] = orig.get("bet_score", 0)
            pick["weekly_change"] = orig.get("weekly_change", 0)
            pick["frame_scores"] = orig.get("frame_scores", {})
            pick["top3_signals"] = orig.get("top3_signals", [])

        # فلترة: فقط ثقة >= MIN_CONFIDENCE
        picks = [p for p in picks if p.get("confidence", 0) >= MIN_CONFIDENCE]
        picks = picks[:MAX_FINAL_PICKS]

        return {
            "picks": picks,
            "rejected": result.get("rejected_notable", []),
            "market_note": result.get("market_note", ""),
            "eval_time": datetime.now().isoformat(),
            "candidates_count": len(candidates),
            "model": "claude-sonnet-4-6",
        }

    except json.JSONDecodeError as e:
        return {"error": f"خطأ في تحليل رد Claude: {str(e)[:100]}",
                "picks": [], "raw_text": text[:500]}
    except Exception as e:
        return {"error": str(e)[:200], "picks": []}


def _slim(stock: dict, source: str) -> dict:
    """يختصر بيانات السهم للمُقيّم (لتقليل التوكنز)."""
    fs = stock.get("frame_scores", {})
    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "price": stock.get("price", 0),
        "change_pct": stock.get("change_pct", 0),
        "weekly_change": stock.get("weekly_change", 0),
        "monthly_change": stock.get("monthly_change", 0),
        "bet_score": stock.get("bet_score", 0),
        "pre_launch": stock.get("pre_launch", 0),
        "trend": stock.get("trend", ""),
        "classify": stock.get("classify", ""),
        "total_active": stock.get("total_active", 0),
        "top3_signals": stock.get("top3_signals", []),
        "penalties": stock.get("penalties", []),
        "short_signals": f"{fs.get('short',{}).get('count',0)}/{fs.get('short',{}).get('total',0)}",
        "mid_signals": f"{fs.get('mid',{}).get('count',0)}/{fs.get('mid',{}).get('total',0)}",
        "long_signals": f"{fs.get('long',{}).get('count',0)}/{fs.get('long',{}).get('total',0)}",
        "rsi": stock.get("rsi", 0),
        "mfi": stock.get("mfi", 0),
        "net_liquidity": stock.get("net_liquidity", 0),
        "source": source,
    }
