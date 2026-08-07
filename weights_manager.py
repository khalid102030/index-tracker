# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════
  مدير أوزان التعلّم — بموافقة + رجوع آمن
═══════════════════════════════════════════════════════════════
- اقتراحات التعلّم تُخزّن (بانتظار موافقة المالك)
- عند الاعتماد: تُطبّق + تُحفظ النسخة السابقة (نسخة احتياطية)
- تتبّع أداء كل نسخة → لو الجديدة أسوأ، رجوع للسابقة
- لا ينسى الطرق القديمة (كل نسخة محفوظة)
═══════════════════════════════════════════════════════════════
"""
import json
from datetime import datetime

# الأوزان الأساسية (النسخة الأصلية — لا تُمسّ)
BASE_SIGNAL_WEIGHTS = {
    "زخم مؤكد": 32, "تتابع صحّي": 30, "سيولة متراكمة": 28,
    "تجميع صامت": 25, "بوادر مبكرة": 22, "تجميع مبكر": 20,
    "قرب من المتوسط": 15, "قصيرة نشطة": 14,
}

_ACTIVE_KEY = "weights_active"      # النسخة المطبّقة حالياً
_PENDING_KEY = "weights_pending"    # اقتراح بانتظار الموافقة
_HISTORY_KEY = "weights_history"    # كل النسخ السابقة


def _get_sb():
    try:
        import server
        return server._get_supabase()
    except Exception:
        return None


def _read(key, default):
    sb = _get_sb()
    if sb:
        try:
            rows = sb.table("idx_settings").select("*").eq("key", key).limit(1).execute().data
            if rows:
                val = rows[0].get("value") or {}
                if isinstance(val, str):
                    val = json.loads(val)
                return val
        except Exception:
            pass
    return default


def _write(key, value):
    sb = _get_sb()
    if sb:
        try:
            sb.table("idx_settings").upsert(
                {"key": key, "value": value}, on_conflict="key").execute()
            return True
        except Exception:
            pass
    return False


# ═══════ الأوزان الفعّالة (تُستخدم بالتحليل) ═══════

def get_active_weights() -> dict:
    """الأوزان المطبّقة حالياً (أو الأساسية إن لم يُعتمد شيء)."""
    active = _read(_ACTIVE_KEY, {})
    weights = active.get("weights") if active else None
    return {**BASE_SIGNAL_WEIGHTS, **(weights or {})}


def get_active_version() -> dict:
    """معلومات النسخة الفعّالة."""
    active = _read(_ACTIVE_KEY, {})
    return {
        "version": active.get("version", 0),
        "applied_at": active.get("applied_at"),
        "is_base": not bool(active.get("weights")),
        "success_rate_at_apply": active.get("success_rate_at_apply"),
    }


# ═══════ الاقتراح (بانتظار الموافقة) ═══════

def save_pending(weights: dict, assessment: str, current_rate: float) -> dict:
    """يحفظ اقتراح تعلّم بانتظار موافقة المالك."""
    pending = {
        "weights": weights,
        "assessment": assessment,
        "suggested_at": datetime.now().isoformat(),
        "rate_when_suggested": current_rate,
    }
    _write(_PENDING_KEY, pending)
    return pending


def get_pending() -> dict:
    """الاقتراح المعلّق (إن وُجد)."""
    return _read(_PENDING_KEY, {})


def clear_pending():
    _write(_PENDING_KEY, {})


# ═══════ الاعتماد (تطبيق + حفظ نسخة احتياطية) ═══════

def approve_pending(current_rate: float = None) -> dict:
    """يعتمد الاقتراح: يطبّقه ويحفظ النسخة الحالية للتاريخ."""
    pending = get_pending()
    if not pending or not pending.get("weights"):
        return {"ok": False, "error": "لا يوجد اقتراح للاعتماد"}

    # احفظ النسخة الحالية في التاريخ (قبل استبدالها)
    history = _read(_HISTORY_KEY, {"versions": []})
    versions = history.get("versions", [])
    current_active = _read(_ACTIVE_KEY, {})
    new_version = (current_active.get("version", 0) + 1)

    # أرشف الحالية
    if current_active:
        versions.append({
            "version": current_active.get("version", 0),
            "weights": current_active.get("weights", {}),
            "applied_at": current_active.get("applied_at"),
            "retired_at": datetime.now().isoformat(),
            "rate_at_apply": current_active.get("success_rate_at_apply"),
            "rate_at_retire": current_rate,
        })
    # احتفظ بآخر 10 نسخ فقط
    versions = versions[-10:]
    _write(_HISTORY_KEY, {"versions": versions})

    # طبّق الجديدة
    new_active = {
        "version": new_version,
        "weights": pending["weights"],
        "applied_at": datetime.now().isoformat(),
        "success_rate_at_apply": current_rate,
        "assessment": pending.get("assessment", ""),
    }
    _write(_ACTIVE_KEY, new_active)
    clear_pending()
    return {"ok": True, "version": new_version,
            "message": f"تم اعتماد النسخة {new_version} — النسخة السابقة محفوظة"}


def reject_pending() -> dict:
    """يرفض الاقتراح (يبقى على الأوزان الحالية)."""
    clear_pending()
    return {"ok": True, "message": "رُفض الاقتراح — الأوزان الحالية مستمرة"}


# ═══════ الرجوع (لو الأداء ساء) ═══════

def rollback() -> dict:
    """يرجع للنسخة السابقة (آخر نسخة بالتاريخ)."""
    history = _read(_HISTORY_KEY, {"versions": []})
    versions = history.get("versions", [])
    if not versions:
        # ارجع للأساسية
        _write(_ACTIVE_KEY, {"version": 0, "weights": {}, "applied_at": datetime.now().isoformat()})
        return {"ok": True, "message": "رجعت للأوزان الأساسية الأصلية"}
    # آخر نسخة محفوظة
    prev = versions.pop()
    _write(_HISTORY_KEY, {"versions": versions})
    _write(_ACTIVE_KEY, {
        "version": prev.get("version", 0),
        "weights": prev.get("weights", {}),
        "applied_at": datetime.now().isoformat(),
        "success_rate_at_apply": prev.get("rate_at_apply"),
        "restored": True,
    })
    return {"ok": True, "version": prev.get("version", 0),
            "message": f"رجعت للنسخة {prev.get('version', 0)} — الطريقة السابقة استُعيدت"}


def get_history() -> list:
    """كل النسخ السابقة (للمراجعة)."""
    history = _read(_HISTORY_KEY, {"versions": []})
    return history.get("versions", [])


def get_status() -> dict:
    """الحالة الكاملة: الفعّالة + المعلّق + التاريخ."""
    active = get_active_version()
    pending = get_pending()
    history = get_history()
    return {
        "active": active,
        "has_pending": bool(pending and pending.get("weights")),
        "pending": pending if pending.get("weights") else None,
        "history_count": len(history),
        "history": history[-5:],
    }
