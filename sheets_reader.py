# -*- coding: utf-8 -*-
"""
قارئ Google Sheets — يقرأ شيت عام بعدة تبويبات.
كل تبويب = لقطة بتاريخ ووقت.
يدعم صيغ التسمية:
  2026-07-25_15-43  |  25-07-2026 3-43  |  13-Jul_11-08
"""
import re, io, requests
import pandas as pd
from datetime import datetime
from urllib.parse import quote

def extract_sheet_id(url: str) -> str:
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
    if m: return m.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{20,}$', url): return url
    raise ValueError("رابط Google Sheets غير صالح")

def list_sheet_tabs(sheet_id: str) -> list:
    """يجلب أسماء التبويبات — يعطي أولوية لأسماء التواريخ."""
    for view in ("edit", "htmlview"):
        try:
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/{view}"
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                continue
            txt = r.text

            # ① الأولوية: تبويبات بأسماء تواريخ (YYYY-MM-DD_HH-MM) — الأكثر موثوقية
            dated = re.findall(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})', txt)
            if dated:
                seen, result = set(), []
                for t in dated:
                    if t not in seen:
                        seen.add(t); result.append(t)
                return result

            # ② احتياطي: أنماط أخرى لأسماء تبويبات عامة
            for pat in (r'\{"name":"([^"]+)"', r'"sheetId":\d+,"title":"([^"]+)"',
                        r'"title":"([^"]+)","sheetType"'):
                found = re.findall(pat, txt)
                if found:
                    seen, result = set(), []
                    for t in found:
                        t = str(t).strip()
                        if t and t not in seen and 2 < len(t) < 60 and "{" not in t:
                            seen.add(t); result.append(t)
                    if result:
                        return result
        except Exception:
            continue
    return ["Sheet1"]

def read_tab(sheet_id: str, tab_name: str = None) -> pd.DataFrame:
    if tab_name:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={quote(tab_name)}"
    else:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))

def parse_snapshot_time(tab_name: str) -> datetime:
    """يستخرج تاريخ/وقت اللقطة من اسم التبويب — يدعم صيغ متعددة."""
    MONTHS = {"Jan":"01","Feb":"02","Mar":"03","Apr":"04","May":"05","Jun":"06",
              "Jul":"07","Aug":"08","Sep":"09","Oct":"10","Nov":"11","Dec":"12"}
    patterns = [
        (r'(\d{4})-(\d{1,2})-(\d{1,2})[_ ](\d{1,2})-(\d{2})', 'ymd'),
        (r'(\d{1,2})-(\d{1,2})-(\d{4})[_ ](\d{1,2})-(\d{2})', 'dmy'),
        (r'(\d{1,2})-([A-Za-z]{3})[_ ](\d{1,2})-(\d{2})', 'dM'),
    ]
    for pat, fmt in patterns:
        m = re.search(pat, tab_name)
        if not m: continue
        try:
            if fmt == 'ymd':
                return datetime(int(m[1]),int(m[2]),int(m[3]),int(m[4]),int(m[5]))
            elif fmt == 'dmy':
                return datetime(int(m[3]),int(m[2]),int(m[1]),int(m[4]),int(m[5]))
            elif fmt == 'dM':
                mon = MONTHS.get(m[2].title(),"01")
                yr = datetime.now().year
                return datetime(yr,int(mon),int(m[1]),int(m[3]),int(m[4]))
        except Exception: continue
    return None

def fetch_latest_snapshot(sheet_url: str) -> dict:
    sid = extract_sheet_id(sheet_url)
    tabs = list_sheet_tabs(sid)
    if not tabs: raise RuntimeError("لا توجد تبويبات بالشيت")
    with_dates = [(t, parse_snapshot_time(t)) for t in tabs]
    with_dates = [(t,d) for t,d in with_dates if d]
    if with_dates:
        # يوجد تبويبات بتواريخ — خذ الأحدث
        with_dates.sort(key=lambda x: x[1], reverse=True)
        tab, snap_time = with_dates[0]
    else:
        # لا تبويب بتاريخ (مثل Sheet1) — استخدم وقت السحب الحالي
        from market_clock import now_riyadh
        tab = tabs[-1]
        snap_time = now_riyadh().replace(tzinfo=None)
    df = read_tab(sid, tab)
    # اسم عرض واضح: التاريخ_الوقت (حتى لو التبويب اسمه عام)
    display_name = tab
    if snap_time and not parse_snapshot_time(tab):
        display_name = snap_time.strftime("%Y-%m-%d_%H-%M")
    return {"df": df, "tab_name": tab, "display_name": display_name,
            "snapshot_time": snap_time, "all_tabs": tabs, "sheet_id": sid}
