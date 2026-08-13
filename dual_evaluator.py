# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════
  المُقيّم المزدوج — Claude + Gemini
═══════════════════════════════════════════════════════════════
① Claude يقيّم المرشحين ويختار 3–5
② Gemini يراجع اختيارات Claude ويوافق أو يعترض
③ يتفقون على قائمة نهائية
④ يقيّمون النتائج السابقة ويعدّلون الاستراتيجية
⑤ تُحفظ الاستراتيجية المعدّلة للتعلّم المستمر
═══════════════════════════════════════════════════════════════
"""
import json, os, requests
from datetime import datetime

MAX_CANDIDATES = 12
MAX_FINAL = 3
MIN_CONFIDENCE = 8

# ═══════ استدعاء النماذج ═══════

def _call_claude(system: str, prompt: str) -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key: raise RuntimeError("ANTHROPIC_API_KEY غير موجود")
    import time as _t
    last_err = ""
    for attempt in range(4):  # 4 محاولات عند الزحمة
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 2500,
                      "system": system, "messages": [{"role": "user", "content": prompt}]},
                timeout=60)
            if r.status_code in (200, 201):
                return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            # 529 = مزحوم، 429 = تجاوز الحد، 503 = مؤقت → أعد المحاولة
            if r.status_code in (429, 529, 503, 500, 502):
                last_err = f"HTTP {r.status_code}"
                _t.sleep(2 * (attempt + 1))  # انتظار متزايد: 2,4,6,8 ثانية
                continue
            raise RuntimeError(f"Claude HTTP {r.status_code}: {r.text[:150]}")
        except requests.exceptions.RequestException as e:
            last_err = str(e)[:100]
            _t.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Claude مزحوم — فشلت 4 محاولات ({last_err}). حاول بعد دقيقة.")


def _call_gemini(prompt: str) -> str:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key: raise RuntimeError("GEMINI_API_KEY غير موجود")
    import time as _t
    last_err = ""
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60)
            if r.status_code in (200, 201):
                data = r.json()
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts)
            if r.status_code in (429, 503, 500, 502):
                last_err = f"HTTP {r.status_code}"
                _t.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:150]}")
        except requests.exceptions.RequestException as e:
            last_err = str(e)[:100]
            _t.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Gemini مزحوم — فشلت المحاولات ({last_err}).")


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"): text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"): text = text[:-3]
    return json.loads(text.strip())


# ═══════ البرومبتات ═══════

SYSTEM_EVAL = """أنت محلل فني متقدم (CMT Level 3) ومتخصص في البيانات الكمية.
مهمتك: تقييم مرشحين وتحديد أيّهم يستحق المراهنة عليه.
أنت حارس بوابة صارم — أقل عدد ممكن بأعلى ثقة.

المبادئ المُقاسة (165 توصية):
• فخ كلاسيكي: 8+ مشتعلة مع صعود = انتهت حركته
• المنطقة الميتة: أسبوعي 0–0.7% = 51% نجاح
• الربع الذهبي: أسبوعي 0.7–1.6% = 80% نجاح
• 3 إشارات فقط لها قيمة: تتابع صحّي، سيولة متراكمة، حجم نسبي عالي
• بذرة الحركة ضرورية: السكون التام ليس مثالياً

⚠️ قاعدة RSI الحاسمة (مهمة جداً):
• RSI منخفض (<45) وحده = خمول ميّت = ضعف حقيقي، السهم يظل بارد. استبعده.
• RSI منخفض + سيولة صاعدة/حجم = تجميع مبكر = فرصة (هدوء قبل الانطلاق).
• RSI 50–60 + سيولة = ارتداد صحّي بدأ = جيد.
• RSI مرتفع (60+) + سيولة داخلة = زخم مؤكد = وقود حقيقي للاستمرار = ممتاز.
• RSI مرتفع (60+) بلا سيولة = قد يكون تشبّع/قمة = حذر.
الحقل rsi_state في بيانات كل مرشح يوضّح حالته — اعتمد عليه.
لا ترشّح سهماً حالته 'خمول ميّت' مهما بدت إشاراته."""

REVIEW_PROMPT = """أنت محلل فني مستقل ومراجع خبير. مهمتك مراجعة تقييم محلل آخر.

المرشحون الأصليون:
{candidates}

تقييم المحلل الأول:
{claude_picks}

المطلوب:
1. هل توافق على كل اختيار؟ لكل سهم: وافق / اعترض مع السبب
2. هل فاته سهم من المرشحين يستحق المراهنة؟
3. رتّب القائمة النهائية المتفق عليها (3–5 فقط)

أجب بـ JSON فقط:
{{"agreements": [{{"symbol":"...","agree":true/false,"reason":"..."}}],
  "missed": [{{"symbol":"...","reason":"..."}}],
  "final_picks": [{{"symbol":"...","name":"...","confidence":1-10,
    "horizon":"...","reasoning":"...","key_signal":"...","risk":"..."}}],
  "consensus_note": "ملاحظة عن مستوى الاتفاق"}}"""

LEARN_PROMPT = """أنت خبير في تحسين استراتيجيات التداول الكمية.

نتائج التوصيات السابقة:
{performance}

الاستراتيجية الحالية:
- هدف: {target_pct}% خلال {validity} أيام تداول
- أوزان الإشارات: {weights}
- عتبات الفخاخ: 8+ مؤشرات مشتعلة

المطلوب:
1. قيّم الأداء — ما اللي اشتغل وما اللي ما اشتغل؟
2. اقترح تعديلات محددة (أرقام) على الأوزان أو العتبات
3. هل الهدف والمهلة مناسبين؟
4. مهم: انتبه لنسبة "بلا حركة" (flat_rate) — التوصيات التي أغلقت دون حركة تُذكر.
   هذه إشارة أن الاختيار كان ضعيف الزخم. اقترح تعديلات ترفع الزخم
   (مثل رفع وزن السيولة/الزخم المؤكد، وتقليل الاختيارات الهادئة جداً)
   لتقليل التوصيات عديمة الحركة مستقبلاً.
5. انتبه لمعايرة الثقة: إن كانت التوصيات الأقل ثقة تنجح أكثر من الأعلى ثقة،
   فمقياس الثقة معكوس — اذكر ذلك في تقييمك واقترح ما الذي يجب أن يرفع الثقة فعلاً
   (بناءً على الإشارات الفعلية الناجحة في البيانات).

أجب بـ JSON:
{{"assessment": "تقييم عام في 2-3 جمل (اذكر نسبة بلا حركة إن كانت مرتفعة)",
  "adjustments": [{{"parameter":"...","current":"...","suggested":"...","reason":"..."}}],
  "new_weights": {{"signal_name": weight_number}},
  "target_recommendation": {{"pct": number, "days": number, "reason":"..."}},
  "save_strategy": true/false}}"""


# ═══════ التقييم المزدوج ═══════

def dual_evaluate(analysis_result: dict, performance: dict = None,
                  prev_picks: list = None, movers_lesson: dict = None) -> dict:
    """
    ① Claude يقيّم → ② Gemini يراجع → ③ توافق نهائي
    مع مراعاة: الأداء السابق + التوصيات السابقة + تشريح حركة أمس.
    """
    stocks = analysis_result.get("stocks", [])
    recs = analysis_result.get("recommendations", {})

    # جمع المرشحين — الاستراتيجية القصيرة فقط (المدى البعيد قسم منفصل)
    seen, candidates = set(), []
    for s in recs.get("short_term", []):
        if s["symbol"] not in seen and not s.get("is_trap"):
            seen.add(s["symbol"]); candidates.append(_slim(s))
    for s in recs.get("speculative", []):
        if len(candidates) >= MAX_CANDIDATES: break
        if s["symbol"] not in seen and not s.get("is_trap"):
            seen.add(s["symbol"]); candidates.append(_slim(s))
    # أسهم عامة قوية (لكن ليست طويلة المدى صرفة)
    for s in stocks:
        if len(candidates) >= MAX_CANDIDATES: break
        fs = s.get("frame_scores", {})
        long_pct = fs.get("long", {}).get("pct", 0)
        short_pct = fs.get("short", {}).get("pct", 0)
        mid_pct = fs.get("mid", {}).get("pct", 0)
        # نتجاوز الأسهم التي إشاراتها طويلة فقط (تخص قسم المدى البعيد)
        is_pure_long = long_pct >= 40 and short_pct < 20 and mid_pct < 25
        if (s["symbol"] not in seen and not s.get("is_trap")
                and s["bet_score"] >= 30 and not is_pure_long):
            seen.add(s["symbol"]); candidates.append(_slim(s))

    if not candidates:
        return {"picks": [], "consensus": "لا مرشحين", "eval_time": datetime.now().isoformat()}

    cand_json = json.dumps(candidates, ensure_ascii=False, indent=1)

    # سياق الأداء السابق (لتجويد القرار)
    history_context = ""
    if performance and performance.get("closed", 0) >= 3:
        sig_stats = performance.get("signal_stats", [])
        top_signals = [f"{s['signal']} ({s['rate']}%)" for s in sig_stats[:3] if s.get("rate", 0) >= 55]
        weak_signals = [f"{s['signal']} ({s['rate']}%)" for s in sig_stats if s.get("rate", 100) <= 40]
        history_context = f"""

📊 من نتائجك السابقة ({performance.get('closed')} توصية محسومة، نجاح {performance.get('success_rate')}%):
- أنجح الإشارات: {', '.join(top_signals) if top_signals else 'لا يوجد بعد'}
- أضعف الإشارات: {', '.join(weak_signals) if weak_signals else 'لا يوجد'}
رجّح المرشحين الذين يحملون الإشارات الناجحة، واحذر من الإشارات الضعيفة."""

    prev_context = ""
    if prev_picks:
        prev_syms = [f"{p.get('symbol')} (ثقة {p.get('confidence')})" for p in prev_picks]
        prev_context = f"""

🔁 توصياتك النشطة السابقة (ما زالت تحت المتابعة): {', '.join(prev_syms)}
مهم جداً: ليس مطلوباً إصدار توصيات جديدة كل مرة. التوصيات السابقة تستمر تحت المتابعة حتى تُحسم.
- إذا لم يظهر مرشح جديد يستحق ثقة ≥8، لا تخترع توصية — أرجع قائمة فارغة، وستستمر السابقة.
- إذا ظهر أحد السابقة بين المرشحين وما زال قوياً، ثبّته. إذا ضعف، لا تعده.
- الجودة أهم من الاستمرارية. يوم بلا توصية جديدة أفضل من توصية ضعيفة."""

    # تشريح حركة السوق — الدرس الأهم للتعلّم
    movers_context = ""
    if movers_lesson and (movers_lesson.get("missed") or movers_lesson.get("caught")):
        caught = movers_lesson.get("caught", [])
        missed = movers_lesson.get("missed", [])
        def _fmt(m):
            return (f"{m['name']}({m['symbol']}) +{m['change_pct']}% "
                    f"[RSI {m['rsi']:.0f}/{m['rsi_state']}, أسبوعي {m['weekly']}%, "
                    f"نشط {m['total_active']}, {m['trend']}]")
        missed_txt = "\n".join("  ✗ " + _fmt(m) for m in missed[:6])
        caught_txt = "\n".join("  ✓ " + _fmt(m) for m in caught[:4])
        movers_context = f"""

🔍 تشريح حركة السوق (تعلّم فعلي — {movers_lesson.get('note','')}):
{'أسهم ارتفعت ورصدناها:' if caught else ''}
{caught_txt}
{'⚠️ أسهم ارتفعت وفاتتنا (حلّل لماذا فاتتنا — ما القاسم المشترك في مؤشراتها؟):' if missed else ''}
{missed_txt}

مهمتك: افحص الأسهم التي فاتتنا رغم ارتفاعها. ما النمط المشترك؟ (RSI معيّن؟ حالة سيولة؟ توليفة إشارات؟)
إذا وجدت مرشحاً حالياً يشبه نمط الفائتين الرابحين، ارفع ثقتك فيه. تعلّم من الفرص الضائعة."""

    # ① Claude يقيّم
    claude_prompt = f"""هؤلاء {len(candidates)} مرشح من {analysis_result['summary']['total_stocks']} سهم:
{cand_json}{history_context}{prev_context}{movers_context}

اختر 1–3 أسهم فقط — الأفضل حصراً (ثقة ≥8). الأقل أفضل. إذا واحد فقط يستحق، اختر واحداً. إذا لا شيء يستحق ثقة 8، لا تختر. أجب بـ JSON:
{{"market_note":"...","picks":[{{"symbol":"...","name":"...","confidence":1-10,"horizon":"...","reasoning":"...","key_signal":"...","risk":"..."}}],
"rejected_notable":[{{"symbol":"...","reason":"..."}}],
"missed_pattern":"النمط المشترك في الأسهم التي فاتتنا رغم ارتفاعها، أو فراغ"}}"""

    claude_raw = _call_claude(SYSTEM_EVAL, claude_prompt)
    claude_result = _parse_json(claude_raw)

    # ② Gemini يراجع
    review_prompt = REVIEW_PROMPT.format(
        candidates=cand_json,
        claude_picks=json.dumps(claude_result, ensure_ascii=False, indent=1)
    ) + history_context + movers_context
    gemini_raw = _call_gemini(review_prompt)
    gemini_result = _parse_json(gemini_raw)

    # ③ بناء التوافق
    final_picks = gemini_result.get("final_picks", claude_result.get("picks", []))
    final_picks = [p for p in final_picks if p.get("confidence", 0) >= MIN_CONFIDENCE][:MAX_FINAL]

    # إثراء بالبيانات الأصلية
    stock_map = {s["symbol"]: s for s in stocks}
    for pick in final_picks:
        orig = stock_map.get(pick.get("symbol", ""), {})
        pick["price"] = orig.get("price", 0)
        pick["change_pct"] = orig.get("change_pct", 0)
        pick["bet_score"] = orig.get("bet_score", 0)
        pick["weekly_change"] = orig.get("weekly_change", 0)
        pick["frame_scores"] = orig.get("frame_scores", {})
        pick["top3_signals"] = orig.get("top3_signals", [])

    return {
        "picks": final_picks,
        "claude_picks": claude_result.get("picks", []),
        "gemini_review": gemini_result.get("agreements", []),
        "missed": gemini_result.get("missed", []),
        "market_note": claude_result.get("market_note", ""),
        "consensus_note": gemini_result.get("consensus_note", ""),
        "rejected": claude_result.get("rejected_notable", []),
        "missed_pattern": claude_result.get("missed_pattern", ""),
        "movers_note": (movers_lesson or {}).get("note", ""),
        "eval_time": datetime.now().isoformat(),
        "candidates_count": len(candidates),
        "models": ["claude-sonnet-4-6", "gemini-2.5-flash"],
    }


# ═══════ تقييم النتائج وتعلّم ═══════

def evaluate_and_learn(performance: dict, current_weights: dict = None,
                       target_pct: float = 1.5, validity: int = 3) -> dict:
    """
    Claude + Gemini يقيّمون النتائج ويقترحون تعديلات.
    """
    weights = current_weights or {
        "تتابع صحّي": 30, "سيولة متراكمة": 28,
        "تجميع صامت": 25, "بوادر مبكرة": 22,
    }

    prompt = LEARN_PROMPT.format(
        performance=json.dumps(performance, ensure_ascii=False, indent=1),
        target_pct=target_pct, validity=validity,
        weights=json.dumps(weights, ensure_ascii=False)
    )

    # Claude يحلّل
    claude_learn = _parse_json(_call_claude(
        "أنت خبير استراتيجيات تداول كمية. حلّل الأداء واقترح تعديلات.",
        prompt))

    # Gemini يراجع
    gemini_learn = _parse_json(_call_gemini(
        f"راجع هذا التحليل لأداء استراتيجية تداول واقترح تعديلاتك:\n\n"
        f"تحليل المحلل الأول:\n{json.dumps(claude_learn, ensure_ascii=False, indent=1)}\n\n"
        f"النتائج الفعلية:\n{json.dumps(performance, ensure_ascii=False, indent=1)}\n\n"
        f"أجب بـ JSON: {{\"agree_with_adjustments\": true/false, "
        f"\"additional_adjustments\": [...], \"final_weights\": {{...}}, "
        f"\"notes\": \"...\"}}"
    ))

    # دمج التعديلات
    final_weights = gemini_learn.get("final_weights") or claude_learn.get("new_weights") or weights
    target_rec = claude_learn.get("target_recommendation", {"pct": target_pct, "days": validity})

    return {
        "claude_assessment": claude_learn.get("assessment", ""),
        "adjustments": claude_learn.get("adjustments", []),
        "gemini_notes": gemini_learn.get("notes", ""),
        "agreed": gemini_learn.get("agree_with_adjustments", True),
        "new_weights": final_weights,
        "target_recommendation": target_rec,
        "save_strategy": claude_learn.get("save_strategy", False),
        "learn_time": datetime.now().isoformat(),
    }


def save_strategy(weights: dict, target: dict, supabase=None) -> dict:
    """يحفظ الاستراتيجية المحدّثة في Supabase"""
    if not supabase:
        return {"saved": False, "error": "Supabase غير متاح"}
    try:
        for signal, weight in weights.items():
            supabase.table("idx_learned_weights").upsert({
                "signal_name": signal,
                "weight": weight,
                "last_updated": datetime.now().isoformat(),
            }, on_conflict="signal_name").execute()
        return {"saved": True, "signals_updated": len(weights)}
    except Exception as e:
        return {"saved": False, "error": str(e)[:150]}


def _slim(stock: dict) -> dict:
    fs = stock.get("frame_scores", {})
    return {
        "symbol": stock["symbol"], "name": stock["name"],
        "price": stock.get("price", 0), "change_pct": stock.get("change_pct", 0),
        "weekly_change": stock.get("weekly_change", 0),
        "monthly_change": stock.get("monthly_change", 0),
        "bet_score": stock.get("bet_score", 0),
        "trend": stock.get("trend", ""), "total_active": stock.get("total_active", 0),
        "top3_signals": stock.get("top3_signals", []),
        "penalties": stock.get("penalties", []),
        "short": f"{fs.get('short',{}).get('count',0)}/{fs.get('short',{}).get('total',0)}",
        "mid": f"{fs.get('mid',{}).get('count',0)}/{fs.get('mid',{}).get('total',0)}",
        "long": f"{fs.get('long',{}).get('count',0)}/{fs.get('long',{}).get('total',0)}",
        "rsi": stock.get("rsi", 0), "rsi_state": stock.get("rsi_state", ""),
        "net_liquidity": stock.get("net_liquidity", 0),
    }
