# -*- coding: utf-8 -*-
"""
أسعار لحظية من سهمك — /quote/{symbol}/
"""
import os, time, requests

SAHMK_BASE = "https://app.sahmk.sa/api/v1"
_session = requests.Session()
_throttle = [0.0]

def _get_key():
    return os.getenv("SAHMK_API_KEY", "")

def fetch_price(symbol: str) -> dict:
    key = _get_key()
    if not key:
        return {"symbol": symbol, "error": "SAHMK_API_KEY غير موجود"}
    elapsed = time.time() - _throttle[0]
    if elapsed < 0.4:
        time.sleep(0.4 - elapsed)
    try:
        r = requests.get(
            f"{SAHMK_BASE}/quote/{symbol}/",
            headers={"X-API-Key": key, "Accept": "application/json"},
            timeout=15,
        )
        _throttle[0] = time.time()
        if r.status_code != 200:
            return {"symbol": symbol, "error": f"HTTP {r.status_code}"}
        d = r.json()
        return {
            "symbol": symbol,
            "price": d.get("price"),
            "open": d.get("open"),
            "high": d.get("high"),
            "low": d.get("low"),
            "change_pct": d.get("change_percent"),
            "volume": d.get("volume"),
            "updated": d.get("updated_at", ""),
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)[:150]}


def fetch_prices_bulk(symbols: list) -> dict:
    """يجلب أسعار مجموعة أسهم — يرجّع {symbol: price}"""
    prices = {}
    for sym in symbols:
        data = fetch_price(sym)
        if data.get("price"):
            prices[sym] = data["price"]
    return prices


def fetch_prices_with_details(symbols: list) -> list:
    """يرجّع قائمة كاملة بالتفاصيل لكل سهم"""
    results = []
    for sym in symbols:
        results.append(fetch_price(sym))
    return results
