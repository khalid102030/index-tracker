# -*- coding: utf-8 -*-
"""ساعات السوق السعودي · أحد–خميس · 9:30–15:20"""
from datetime import datetime, time, timedelta
try:
    import pytz
    RIYADH_TZ = pytz.timezone("Asia/Riyadh")
except ImportError:
    from datetime import timezone
    RIYADH_TZ = timezone(timedelta(hours=3))

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(15, 20)
TRADING_DAYS = {6, 0, 1, 2, 3}          # Sun–Thu

def now_riyadh():
    return datetime.now(RIYADH_TZ)

def is_trading_day(dt=None):
    return (dt or now_riyadh()).weekday() in TRADING_DAYS

def is_market_open(dt=None):
    dt = dt or now_riyadh()
    return is_trading_day(dt) and MARKET_OPEN <= dt.time() <= MARKET_CLOSE

def classify_snapshot_time(dt=None):
    dt = dt or now_riyadh()
    if not is_trading_day(dt):       return "weekend"
    if dt.time() < time(10, 0):      return "pre_open"
    if dt.time() > MARKET_CLOSE:     return "post_close"
    return "live"

def add_trading_days(dt, n):
    d = dt; added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() in TRADING_DAYS: added += 1
    return d
