# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════
  تكامل Telegram — مستقل، قابل للتشغيل/الإيقاف
═══════════════════════════════════════════════════════════════
① إرسال التوصيات الجديدة تلقائياً (Push)
② بوت تفاعلي: /active أو زر "التوصيات الجارية"
③ إعدادات محفوظة في Supabase (تفعيل/توكن/chat id)
   لا يرسل ولا يستجيب إذا كانت الخاصية مطفأة.
═══════════════════════════════════════════════════════════════
"""
import os, json, requests

TG_API = "https://api.telegram.org/bot{token}/{method}"
_SETTINGS_KEY = "telegram"

# إعدادات افتراضية
_defaults = {"enabled": False, "bot_token": "", "chat_id": ""}


def _get_sb():
    try:
        import server
        return server._get_supabase()
    except Exception:
        return None


def get_settings() -> dict:
    """يجلب إعدادات تيليجرام من Supabase."""
    sb = _get_sb()
    if sb:
        try:
            rows = sb.table("idx_settings").select("*").eq("key", _SETTINGS_KEY).limit(1).execute().data
            if rows:
                val = rows[0].get("value") or {}
                if isinstance(val, str):
                    val = json.loads(val)
                return {**_defaults, **val}
        except Exception:
            pass
    # احتياطي: متغيرات البيئة
    return {
        "enabled": os.getenv("TELEGRAM_ENABLED", "false").lower() == "true",
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }


def save_settings(enabled=None, bot_token=None, chat_id=None) -> dict:
    """يحفظ إعدادات تيليجرام في Supabase."""
    cur = get_settings()
    if enabled is not None: cur["enabled"] = bool(enabled)
    if bot_token is not None and not str(bot_token).startswith("•"): cur["bot_token"] = bot_token
    if chat_id is not None: cur["chat_id"] = chat_id
    sb = _get_sb()
    if sb:
        try:
            sb.table("idx_settings").upsert(
                {"key": _SETTINGS_KEY, "value": cur}, on_conflict="key").execute()
            return {"ok": True, "enabled": cur["enabled"]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:150]}
    return {"ok": False, "error": "Supabase غير متصل"}


def is_enabled() -> bool:
    s = get_settings()
    return bool(s.get("enabled") and s.get("bot_token") and s.get("chat_id"))


# ═══════ الإرسال ═══════

def _send(method: str, payload: dict, token: str = None) -> dict:
    s = get_settings()
    tok = token or s.get("bot_token")
    if not tok:
        return {"ok": False, "error": "لا يوجد توكن"}
    try:
        r = requests.post(TG_API.format(token=tok, method=method), json=payload, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}


def send_message(text: str, reply_markup: dict = None, chat_id: str = None) -> dict:
    """يرسل رسالة — فقط إذا كانت الخدمة مفعّلة."""
    s = get_settings()
    if not s.get("enabled"):
        return {"ok": False, "skipped": "الخدمة مطفأة"}
    cid = chat_id or s.get("chat_id")
    if not cid:
        return {"ok": False, "error": "لا يوجد chat_id"}
    payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return _send("sendMessage", payload)


def notify_new_recommendation(pick: dict) -> dict:
    """يرسل تنبيه توصية جديدة (يُستدعى بعد السحب التلقائي)."""
    if not is_enabled():
        return {"skipped": True}
    horizon = pick.get("horizon") or pick.get("category", "")
    hz_ar = "قريبة" if ("يوم" in horizon or "ساع" in horizon or "short" in horizon) else "طويلة" if "long" in horizon else horizon or "قريبة"
    conf = pick.get("confidence", "")
    text = (
        f"🚨 <b>توصية جديدة</b>\n\n"
        f"📊 السهم: <b>{pick.get('name','')}</b> ({pick.get('symbol','')})\n"
        f"📉 سعر الدخول: <b>{pick.get('price') or pick.get('entry_price','')}</b>\n"
        f"🎯 الهدف: <b>{pick.get('target_price','')}</b>\n"
        f"⏱️ نوع التوصية: {hz_ar}\n"
        f"⭐ الثقة: {conf}/10"
    )
    kb = {"inline_keyboard": [[{"text": "📈 التوصيات الجارية", "callback_data": "active"}]]}
    return send_message(text, reply_markup=kb)


def notify_batch(picks: list) -> dict:
    """يرسل دفعة توصيات جديدة."""
    if not is_enabled() or not picks:
        return {"skipped": True}
    sent = 0
    for p in picks:
        r = notify_new_recommendation(p)
        if r.get("ok"):
            sent += 1
    return {"sent": sent}


# ═══════ بوت الاستعلام ═══════

def _format_active(rows: list) -> str:
    if not rows:
        return "📭 لا توجد توصيات جارية حالياً."
    lines = ["📈 <b>التوصيات الجارية</b>\n"]
    for i, r in enumerate(rows[:15], 1):
        cur = r.get("current_price")
        entry = r.get("entry_price", 0)
        pct = r.get("current_pct", 0)
        arrow = "🟢" if pct > 0 else "🔴" if pct < 0 else "⚪"
        cur_txt = f" · حالي {cur} ({'+' if pct>0 else ''}{pct}%)" if cur else ""
        lines.append(
            f"{i}. <b>{r.get('name','')}</b> ({r.get('symbol','')}) {arrow}\n"
            f"   دخول {entry} · هدف {r.get('target_price','')}{cur_txt}"
        )
    return "\n".join(lines)


def get_active_recommendations() -> list:
    sb = _get_sb()
    if not sb:
        return []
    try:
        return sb.table("idx_recommendations").select("*") \
            .eq("status", "active").order("score", desc=True).limit(15).execute().data or []
    except Exception:
        return []


def handle_update(update: dict) -> dict:
    """
    يعالج تحديثات تيليجرام (webhook).
    لا يستجيب إذا كانت الخدمة مطفأة.
    """
    if not is_enabled():
        return {"skipped": "مطفأة"}

    # رسالة نصية (أمر)
    msg = update.get("message")
    if msg:
        text = (msg.get("text") or "").strip().lower()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if text in ("/active", "/start", "التوصيات", "توصيات"):
            if text == "/start":
                welcome = ("👋 أهلاً بك في <b>راصد بلس</b>\n\n"
                           "اضغط الزر لعرض التوصيات الجارية، أو أرسل /active")
                kb = {"inline_keyboard": [[{"text": "📈 التوصيات الجارية", "callback_data": "active"}]]}
                return send_message(welcome, reply_markup=kb, chat_id=chat_id)
            rows = get_active_recommendations()
            kb = {"inline_keyboard": [[{"text": "🔄 تحديث", "callback_data": "active"}]]}
            return send_message(_format_active(rows), reply_markup=kb, chat_id=chat_id)

    # زر تفاعلي (callback)
    cb = update.get("callback_query")
    if cb:
        data = cb.get("data", "")
        chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
        cb_id = cb.get("id")
        # رد على الضغطة
        _send("answerCallbackQuery", {"callback_query_id": cb_id})
        if data == "active":
            rows = get_active_recommendations()
            kb = {"inline_keyboard": [[{"text": "🔄 تحديث", "callback_data": "active"}]]}
            return send_message(_format_active(rows), reply_markup=kb, chat_id=chat_id)

    return {"ok": True, "no_action": True}


def set_webhook(base_url: str) -> dict:
    """يسجّل webhook مع تيليجرام."""
    s = get_settings()
    if not s.get("bot_token"):
        return {"ok": False, "error": "لا يوجد توكن"}
    url = f"{base_url.rstrip('/')}/api/telegram/webhook"
    return _send("setWebhook", {"url": url})


def delete_webhook() -> dict:
    return _send("deleteWebhook", {})


def test_connection() -> dict:
    """يختبر التوكن ويرسل رسالة تجريبية."""
    s = get_settings()
    if not s.get("bot_token"):
        return {"ok": False, "error": "لا يوجد توكن"}
    # getMe للتأكد من التوكن
    me = _send("getMe", {})
    if not me.get("ok"):
        return {"ok": False, "error": "التوكن غير صحيح"}
    bot_name = me.get("result", {}).get("username", "")
    # رسالة تجريبية
    if s.get("chat_id"):
        _send("sendMessage", {"chat_id": s["chat_id"],
              "text": f"✅ تم ربط <b>راصد بلس</b> بنجاح مع @{bot_name}", "parse_mode": "HTML"})
    return {"ok": True, "bot": bot_name}
