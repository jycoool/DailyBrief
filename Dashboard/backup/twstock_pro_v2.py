#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twstock_pro.py — 台股基本面監控儀表板（擴充版）

仿基本面選股表的密集欄位版本，整合多個證交所官方免費 API：

  可靠取得（官方 API）：
    本益比、殖利率、股價淨值比   ← BWIBBU_ALL
    月營收 MoM / YoY 增率        ← opendata/t187ap05_L
    三大法人買賣超               ← fund/T86
    除權除息預告（除息日等）     ← opendata/t187ap04_L（TWSE 除權息預告）
    價格、漲跌、年線乖離         ← yfinance

  無法免費可靠取得（會留空白，需付費資料源）：
    毛利率、稅後純益率、EPS 明細、ROE、股利政策、股本

安裝:
    pip install yfinance pandas requests

執行:
    python twstock_pro.py            產生 dashboard_tw_pro.html 並開啟
    python twstock_pro.py --serve    本機伺服器自動更新（預設每 600 秒）
    python twstock_pro.py --port 7801
    python twstock_pro.py --fast     只抓報價與估值，跳過營收/法人/除權息
    python twstock_pro.py --no-open

注意:
  - 官方基本面為每日收盤後更新的「最新快照」，非盤中即時
  - 上櫃股（.TWO）的官方基本面走另一組 TPEx 端點，本版以上市為主
  - 所有資訊僅供研究，非投資建議
"""

import argparse
import os
import sys
import time
import webbrowser
from datetime import datetime

# ── SSL 憑證 ─────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_BUNDLE = os.path.join(_HERE, "ca_bundle.pem")
if os.path.exists(_BUNDLE):
    _CA = _BUNDLE
else:
    try:
        import certifi
        _CA = certifi.where()
    except ImportError:
        _CA = None
if _CA:
    os.environ["CURL_CA_BUNDLE"] = _CA
    os.environ["SSL_CERT_FILE"] = _CA
    os.environ["REQUESTS_CA_BUNDLE"] = _CA

try:
    import requests
    import yfinance as yf
    import pandas as pd
except ImportError:
    sys.exit("請先安裝套件:  pip install yfinance pandas requests")


# ═════════════════════════════════════════════════════════════
# 設定區
# ═════════════════════════════════════════════════════════════

# 觀察清單：代號 -> (顯示名稱, 市場)  市場 TW=上市, TWO=上櫃
WATCHLIST = {
    "1513": ("中興電", "TW"),
    "2101": ("南港",   "TW"),
    "3260": ("威剛",   "TW"),
    "3413": ("京鼎",   "TW"),
    "6239": ("力成",   "TW"),
    "6271": ("同欣電", "TW"),
    "3711": ("日月光投控", "TW"),
    "3227": ("原相",   "TW"),
    "8114": ("振樺電", "TW"),
    "2327": ("國巨",   "TW"),
    "2303": ("聯電",   "TW"),
    "2330": ("台積電", "TW"),
    "2317": ("鴻海",   "TW"),
    "2454": ("聯發科", "TW"),
    "8299": ("群聯",   "TW"),
    "6147": ("頎邦",   "TWO"),
    "3293": ("鈊象",   "TWO"),
}

# 大盤 / 國際連動（yfinance）
MACRO = {
    "^TWII":  ("加權指數", "TAIEX", ""),
    "TWD=X":  ("美元/台幣", "USDTWD", ""),
    "^SOX":   ("費城半導體", "SOX", ""),
    "^IXIC":  ("那斯達克", "Nasdaq", ""),
    "^VIX":   ("VIX", "VIX", ""),
    "GC=F":   ("黃金", "Gold", "$"),
}

ALERTS = {
    "twd_weak": 33.0, "twd_strong": 30.0,
    "vix_high": 25.0, "sox_drop": -3.0, "sox_jump": 3.0,
    "foreign_big": 10000,       # 外資單日買賣超（張）
    "ma200_dev": 2.0,
    "pe_low": 10.0,             # 本益比 < 10 → 低估值標記
    "yield_high": 5.0,          # 殖利率 > 5% → 高殖利率標記
    "rev_yoy_high": 30.0,       # 營收 YoY > 30% → 高成長標記
    "rev_yoy_low": -20.0,       # 營收 YoY < -20% → 衰退警示
}

# 官方端點
EP_BWIBBU = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"   # 本益比殖利率淨值比
EP_REVENUE = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"        # 月營收
EP_T86 = "https://openapi.twse.com.tw/v1/fund/T86"                       # 三大法人
EP_EXRIGHT = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"        # 除權除息預告

# ═════════════════════════════════════════════════════════════


def _get_json(url, timeout=20):
    """抓取 JSON。若回傳空白或非 JSON（常見於台灣非交易時段），回傳 None 而非拋例外。"""
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    r.raise_for_status()
    body = r.text.strip()
    if not body:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _f(s):
    """字串轉 float，失敗回 None。處理逗號與空白。"""
    try:
        s = str(s).replace(",", "").strip()
        if s in ("", "-", "--", "N/A"):
            return None
        return float(s)
    except Exception:
        return None


def _roc_to_date(s):
    """民國年 1150730 → 2026/07/30。"""
    try:
        s = str(s).strip()
        if len(s) < 6:
            return None
        # 可能 7 碼(1150730) 或帶分隔
        digits = "".join(c for c in s if c.isdigit())
        if len(digits) == 7:
            y = int(digits[:3]) + 1911
            return f"{y}/{digits[3:5]}/{digits[5:7]}"
    except Exception:
        pass
    return None


def fetch_valuation():
    """本益比、殖利率、股價淨值比（BWIBBU_ALL）。回傳 {code: {...}}。"""
    out = {}
    try:
        data = _get_json(EP_BWIBBU)
    except Exception as e:
        print(f"  估值 API 失敗（{e}）")
        return out
    if not data:
        return out
    for row in data:
        code = str(row.get("Code", "")).strip()
        if not code:
            continue
        out[code] = {
            "pe": _f(row.get("PEratio")),
            "yield": _f(row.get("DividendYield")),
            "pb": _f(row.get("PBratio")),
        }
    return out


def fetch_revenue():
    """月營收（t187ap05_L）。回傳 {code: {rev, mom, yoy, month}}。"""
    out = {}
    try:
        data = _get_json(EP_REVENUE)
    except Exception as e:
        print(f"  月營收 API 失敗（{e}）")
        return out
    if not data:
        return out
    for row in data:
        code = str(row.get("公司代號", "")).strip()
        if not code:
            continue
        out[code] = {
            "rev": _f(row.get("營業收入-當月營收")),
            "mom": _f(row.get("營業收入-上月比較增減(%)")),
            "yoy": _f(row.get("營業收入-去年同月增減(%)")),
            "month": str(row.get("資料年月", "")).strip(),
        }
    return out


def fetch_institutional():
    """三大法人（T86）。回傳 {code: {foreign, trust, dealer}} 單位張。"""
    out = {}
    try:
        data = _get_json(EP_T86)
    except Exception as e:
        print(f"  三大法人 API 失敗（{e}）")
        return out

    def pick(row, *keys):
        for k in keys:
            if k in row and row[k] not in ("", None):
                return row[k]
        return "0"

    def lots(s):
        v = _f(s)
        return round(v / 1000) if v is not None else 0

    if not data:
        return out
    for row in data:
        code = str(pick(row, "證券代號", "Code")).strip()
        if not code:
            continue
        fm = lots(pick(row, "外陸資買賣超股數(不含外資自營商)"))
        fd = lots(pick(row, "外資自營商買賣超股數"))
        out[code] = {
            "foreign": fm + fd,
            "trust": lots(pick(row, "投信買賣超股數")),
            "dealer": lots(pick(row, "自營商買賣超股數")),
        }
    return out


def fetch_exright():
    """除權除息預告（t187ap04_L）。回傳 {code: {ex_date, kind}}。"""
    out = {}
    try:
        data = _get_json(EP_EXRIGHT)
    except Exception as e:
        print(f"  除權息 API 失敗（{e}）")
        return out
    if not data:
        return out
    for row in data:
        code = str(row.get("股票代號", row.get("公司代號", ""))).strip()
        if not code:
            continue
        ex = _roc_to_date(row.get("除權息交易日", row.get("除息交易日", "")))
        kind = str(row.get("權息", row.get("除權息", ""))).strip()
        if ex:
            out[code] = {"ex_date": ex, "kind": kind}
    return out


def yf_symbol(code, market):
    return f"{code}.{'TWO' if market == 'TWO' else 'TW'}"


def fetch_prices(symbols):
    out = {}
    symbols = list(symbols)
    try:
        data = yf.download(symbols, period="1y", interval="1d",
                          progress=False, auto_adjust=True,
                          group_by="ticker", threads=True)
    except Exception as e:
        print(f"  報價失敗: {e}")
        return out
    if data is None or len(data) == 0:
        return out
    for s in symbols:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if s in data.columns.get_level_values(0):
                    df = data[s]
                else:
                    continue
            else:
                df = data
            close = df["Close"].dropna()
            if len(close) < 2:
                continue
            last, prev = float(close.iloc[-1]), float(close.iloc[-2])
            ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
            out[s] = {
                "price": last,
                "chg_pct": (last - prev) / prev * 100 if prev else 0.0,
                "ma200_dev": ((last - ma200) / ma200 * 100) if ma200 else None,
            }
        except Exception:
            continue
    return out


def check_alerts(macro, stocks, inst, rev):
    out = []
    twd = macro.get("TWD=X", {}).get("price")
    vix = macro.get("^VIX", {}).get("price")
    sox = macro.get("^SOX", {})
    if twd:
        if twd >= ALERTS["twd_weak"]:
            out.append(("critical", f"新台幣 {twd:.3f}", "貶破 33，外資匯出壓力"))
        elif twd <= ALERTS["twd_strong"]:
            out.append(("calm", f"新台幣 {twd:.3f}", "升破 30，外資回流訊號"))
    if sox and sox.get("chg_pct") is not None:
        c = sox["chg_pct"]
        if c <= ALERTS["sox_drop"]:
            out.append(("watch", f"費半隔夜 {c:+.1f}%", "電子權值隔日易承壓"))
        elif c >= ALERTS["sox_jump"]:
            out.append(("calm", f"費半隔夜 {c:+.1f}%", "電子權值隔日有撐"))
    if vix and vix >= ALERTS["vix_high"]:
        out.append(("critical", f"VIX {vix:.1f}", "美股避險升溫"))
    for code, (name, mkt) in WATCHLIST.items():
        d = inst.get(code)
        if d and abs(d["foreign"]) >= ALERTS["foreign_big"]:
            dr = "買超" if d["foreign"] > 0 else "賣超"
            out.append(("watch", f"{name} 外資{dr} {abs(d['foreign']):,.0f} 張", "外資大額進出"))
        rv = rev.get(code)
        if rv and rv.get("yoy") is not None:
            if rv["yoy"] >= ALERTS["rev_yoy_high"]:
                out.append(("calm", f"{name} 月營收 YoY +{rv['yoy']:.0f}%", "營收高成長"))
            elif rv["yoy"] <= ALERTS["rev_yoy_low"]:
                out.append(("watch", f"{name} 月營收 YoY {rv['yoy']:.0f}%", "營收衰退"))
    return out


# ═════════════════════════════════════════════════════════════
# HTML
# ═════════════════════════════════════════════════════════════

CSS = """
:root{
  --bg:#0D1017;--panel:#151A21;--panel2:#1B222B;--edge:#28313D;
  --red:#E8503A;--green:#18A47C;--ink:#E6EDF3;--ink2:#8593A0;--ink3:#5A6673;
  --today:#E8A33D;--gold:#D4A73C;--fg:#4C9BE0;--tr:#E0844C;--de:#9B7FD4;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Noto Sans TC',system-ui,sans-serif;background:var(--bg);
  color:var(--ink);padding:20px 14px 56px;line-height:1.45;font-size:13px}
.wrap{max-width:1500px;margin:0 auto}
.num{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
.up{color:var(--red)}.down{color:var(--green)}.flat{color:var(--ink2)}
.eyebrow{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;
  color:var(--ink3);font-weight:600}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;
  flex-wrap:wrap;padding-bottom:12px;margin-bottom:18px;border-bottom:1px solid var(--edge);
  position:relative}
header::after{content:"";position:absolute;left:0;bottom:-1px;width:76px;height:3px;background:var(--red)}
h1{font-size:23px;font-weight:700}
h1 small{font-size:12px;color:var(--ink3);font-weight:400;margin-left:7px}
.conv{font-size:11px;color:var(--ink3);margin-top:3px}
.conv b{color:var(--red)}.conv i{color:var(--green);font-style:normal}
.stamp{text-align:right}.stamp .t{font-size:13px;margin-top:2px}
section{margin-bottom:22px}
.shead{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}
.shead::after{content:"";flex:1;height:1px;background:var(--edge)}
.macro{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:1px;
  background:var(--edge);border:1px solid var(--edge);border-radius:6px;overflow:hidden}
.cell{background:var(--panel);padding:10px 12px}
.cell .lbl{font-size:10.5px;color:var(--ink2);margin-bottom:3px}
.cell .lbl small{color:var(--ink3);font-size:9px}
.cell .val{font-size:18px;font-weight:500}
.cell .chg{font-size:11.5px;margin-top:1px}
.alerts{display:flex;flex-direction:column;gap:1px;background:var(--edge);
  border:1px solid var(--edge);border-radius:6px;overflow:hidden}
.al{background:var(--panel);padding:10px 13px;display:flex;gap:10px;align-items:baseline}
.al .bar{width:3px;align-self:stretch;flex:0 0 3px;border-radius:2px}
.al.critical .bar{background:var(--red)}.al.watch .bar{background:var(--gold)}
.al.calm .bar{background:var(--green)}
.al .h{font-weight:600;font-size:13px}.al .d{font-size:11.5px;color:var(--ink2)}
.empty{background:var(--panel);border:1px solid var(--edge);border-radius:6px;
  padding:14px;font-size:12.5px;color:var(--ink2)}
.tbl-wrap{overflow-x:auto;border:1px solid var(--edge);border-radius:6px}
table{width:100%;border-collapse:collapse;background:var(--panel);font-size:12.5px;
  min-width:1180px}
th{font-size:10px;letter-spacing:.03em;color:var(--ink3);text-align:right;
  padding:8px 9px;border-bottom:1px solid var(--edge);font-weight:600;white-space:nowrap;
  position:sticky;top:0;background:var(--panel2);z-index:1}
th.grp{color:var(--ink2);border-bottom:1px solid var(--edge);text-align:center;
  font-size:9.5px;letter-spacing:.1em}
td{padding:7px 9px;border-bottom:1px solid #1A212A;text-align:right;white-space:nowrap}
td.l,th.l{text-align:left}
tr:last-child td{border-bottom:0}tr:hover td{background:var(--panel2)}
.tick{font-weight:600}.cd{color:var(--ink3);font-size:10.5px;margin-left:3px}
.sub{color:var(--ink3);font-size:10px}
.pill{display:inline-block;padding:0 5px;font-size:10px;border-radius:3px;
  border:1px solid;margin-left:3px}
.pill.pe{border-color:var(--green);color:var(--green)}
.pill.yd{border-color:var(--gold);color:var(--gold)}
.pill.ma{border-color:var(--today);color:var(--today)}
.today{color:var(--today)}
.flowbar{height:11px;display:inline-block;border-radius:2px;min-width:1px;
  vertical-align:middle;margin-left:5px}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:var(--ink2)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;
  vertical-align:middle}
.na{color:var(--ink3)}
footer{margin-top:26px;padding-top:12px;border-top:1px solid var(--edge);
  font-size:10.5px;color:var(--ink3);line-height:1.85}
@media (max-width:640px){body{padding:14px 8px 40px}h1{font-size:19px}}
"""


def _cls(v):
    if v is None:
        return "flat"
    return "up" if v > 0.03 else "down" if v < -0.03 else "flat"


def _n(v, dec=2, pct=False, plus=False):
    if v is None:
        return '<span class="na">—</span>'
    sign = "+" if (plus and v > 0) else ""
    s = f"{sign}{v:,.{dec}f}"
    if pct:
        s += "%"
    return s


def build_html(macro, stocks, val, rev, inst, exr, refresh=0):
    now = datetime.now()
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    twd = macro.get("TWD=X", {}).get("price")
    twd_str = f'　｜　台幣 <span class="num">{twd:.3f}</span>' if twd else ""
    conv = f'<div class="conv">台股 <b>紅漲</b> · <i>綠跌</i>{twd_str}</div>'

    # 警示
    al = check_alerts(macro, stocks, inst, rev)
    if al:
        order = {"critical": 0, "watch": 1, "calm": 2}
        al.sort(key=lambda x: order.get(x[0], 9))
        alerts_html = '<div class="alerts">' + "".join(
            f'<div class="al {lv}"><span class="bar"></span>'
            f'<span class="h">{h}</span><span class="d">{d}</span></div>'
            for lv, h, d in al) + '</div>'
    else:
        alerts_html = '<div class="empty">目前沒有指標觸及門檻。</div>'

    # 總經
    cells = []
    for t, (label, sub, unit) in MACRO.items():
        d = macro.get(t)
        if not d:
            continue
        v = d["price"]
        vs = f"{v:,.2f}"
        cells.append(f'<div class="cell"><div class="lbl">{label} <small>{sub}</small></div>'
                     f'<div class="val num">{vs}</div>'
                     f'<div class="chg num {_cls(d["chg_pct"])}">{d["chg_pct"]:+.2f}%</div></div>')
    macro_html = f'<div class="macro">{"".join(cells)}</div>' if cells else \
        '<div class="empty">未取得總經資料</div>'

    # 三大法人條長度正規化
    max_flow = 1.0
    for code in WATCHLIST:
        d = inst.get(code)
        if d:
            for k in ("foreign", "trust", "dealer"):
                max_flow = max(max_flow, abs(d[k]))

    def flow(v, color):
        if v is None:
            return '<span class="na">—</span>'
        w = min(abs(v) / max_flow * 42, 42)
        cls = "up" if v > 0 else "down" if v < 0 else "flat"
        sign = "+" if v > 0 else ""
        return (f'<span class="num {cls}">{sign}{v:,.0f}</span>'
                f'<span class="flowbar" style="width:{w:.0f}px;background:{color}"></span>')

    # 個股列
    rows = []
    for code, (name, mkt) in WATCHLIST.items():
        d = stocks.get(yf_symbol(code, mkt))
        v = val.get(code, {})
        rv = rev.get(code, {})
        it = inst.get(code, {})
        ex = exr.get(code, {})

        price = _n(d["price"], 2) if d else '<span class="na">—</span>'
        chg = (f'<span class="num {_cls(d["chg_pct"])}">{d["chg_pct"]:+.2f}%</span>'
               if d else '<span class="na">—</span>')

        dev = d.get("ma200_dev") if d else None
        if dev is None:
            dev_c = '<span class="na">—</span>'
        else:
            near = abs(dev) <= ALERTS["ma200_dev"]
            tag = '<span class="pill ma">年線</span>' if near else ''
            dev_c = f'<span class="num {_cls(dev)}">{dev:+.1f}%</span>{tag}'

        pe = v.get("pe")
        pe_tag = '<span class="pill pe">低</span>' if (pe and pe < ALERTS["pe_low"]) else ''
        pe_c = (f'<span class="num">{pe:.1f}</span>{pe_tag}' if pe else '<span class="na">—</span>')

        pb = v.get("pb")
        pb_c = f'<span class="num">{pb:.2f}</span>' if pb else '<span class="na">—</span>'

        yd = v.get("yield")
        yd_tag = '<span class="pill yd">高</span>' if (yd and yd > ALERTS["yield_high"]) else ''
        yd_c = (f'<span class="num">{yd:.2f}%</span>{yd_tag}' if yd is not None else '<span class="na">—</span>')

        mom = rv.get("mom")
        mom_c = (f'<span class="num {_cls(mom)}">{mom:+.1f}%</span>' if mom is not None else '<span class="na">—</span>')
        yoy = rv.get("yoy")
        yoy_c = (f'<span class="num {_cls(yoy)}">{yoy:+.1f}%</span>' if yoy is not None else '<span class="na">—</span>')

        exd = ex.get("ex_date")
        ex_c = f'<span class="today num">{exd}</span>' if exd else '<span class="na">—</span>'

        rows.append(f"""<tr>
  <td class="l"><span class="tick">{name}</span><span class="cd">{code}</span>
    <span class="sub"> {'上市' if mkt=='TW' else '上櫃'}</span></td>
  <td>{price}</td><td>{chg}</td><td>{dev_c}</td>
  <td>{pe_c}</td><td>{pb_c}</td><td>{yd_c}</td>
  <td>{mom_c}</td><td>{yoy_c}</td>
  <td>{flow(it.get('foreign'), 'var(--fg)')}</td>
  <td>{flow(it.get('trust'), 'var(--tr)')}</td>
  <td>{flow(it.get('dealer'), 'var(--de)')}</td>
  <td>{ex_c}</td>
</tr>""")

    rev_month = ""
    for code in WATCHLIST:
        if rev.get(code, {}).get("month"):
            rev_month = rev[code]["month"]
            break

    table = f"""<div class="tbl-wrap"><table>
<thead>
<tr>
  <th class="l" rowspan="2">股票</th>
  <th rowspan="2">股價</th><th rowspan="2">漲跌</th><th rowspan="2">年線乖離</th>
  <th class="grp" colspan="3">估值（證交所）</th>
  <th class="grp" colspan="2">月營收增率{f'（{rev_month}）' if rev_month else ''}</th>
  <th class="grp" colspan="3">三大法人買賣超（張）</th>
  <th rowspan="2">除息日</th>
</tr>
<tr>
  <th>本益比</th><th>市淨比</th><th>殖利率</th>
  <th>MoM</th><th>YoY</th>
  <th>外資</th><th>投信</th><th>自營商</th>
</tr>
</thead>
<tbody>{''.join(rows)}</tbody>
</table></div>
<div class="legend">
  <span><i style="background:var(--fg)"></i>外資</span>
  <span><i style="background:var(--tr)"></i>投信</span>
  <span><i style="background:var(--de)"></i>自營商</span>
  <span><span class="pill pe">低</span>本益比&lt;10</span>
  <span><span class="pill yd">高</span>殖利率&gt;5%</span>
  <span style="color:var(--ink3)">— 表示免費 API 無資料</span>
</div>"""

    refresh_note = f"　每 {refresh}s 更新" if refresh else ""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{meta}<title>台股基本面監控</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">

<header>
  <div><h1>台股基本面監控 <small>TW Fundamentals</small></h1>{conv}</div>
  <div class="stamp"><div class="eyebrow">資料時間</div>
    <div class="t num">{now:%Y-%m-%d %H:%M}</div>
    <div class="eyebrow" style="margin-top:2px">基本面為收盤後快照{refresh_note}</div></div>
</header>

<section><div class="shead"><span class="eyebrow">門檻警示</span></div>{alerts_html}</section>
<section><div class="shead"><span class="eyebrow">大盤與國際連動</span></div>{macro_html}</section>
<section><div class="shead"><span class="eyebrow">觀察清單 · 估值 · 營收 · 籌碼</span></div>{table}</section>

<footer>
  估值（本益比／市淨比／殖利率）、月營收、三大法人、除權息：證交所官方 OpenAPI，每日收盤後更新。<br>
  價格與年線：yfinance，約 15 分鐘延遲。上櫃股估值/營收部分官方走另一組 TPEx 端點，本版以上市為主，上櫃可能顯示 —。<br>
  毛利率、EPS 明細、ROE、股利政策等需解析財報，免費 API 無法可靠取得，故未列入。<br>
  所有資訊僅供研究，非投資建議。投資決策請自行判斷並自負風險。
</footer>
</div></body></html>"""


# ═════════════════════════════════════════════════════════════

def collect(with_fund=True):
    print("  抓取大盤與國際指標…")
    macro = fetch_prices(list(MACRO))
    print("  抓取個股報價…")
    syms = [yf_symbol(c, m) for c, (n, m) in WATCHLIST.items()]
    stocks = fetch_prices(syms)

    val, rev, inst, exr = {}, {}, {}, {}
    if with_fund:
        print("  抓取證交所估值（本益比/殖利率/市淨比）…")
        val = fetch_valuation()
        print("  抓取月營收…")
        rev = fetch_revenue()
        print("  抓取三大法人…")
        inst = fetch_institutional()
        print("  抓取除權息預告…")
        exr = fetch_exright()
    return macro, stocks, val, rev, inst, exr


def write_html(path, refresh=0, with_fund=True):
    macro, stocks, val, rev, inst, exr = collect(with_fund)
    html = build_html(macro, stocks, val, rev, inst, exr, refresh)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  已更新 {path}"
          f"（估值 {len(val)} / 營收 {len(rev)} / 法人 {len(inst)} / 除息 {len(exr)}）")
    return path


def serve(port, interval, with_fund):
    import http.server, socketserver, threading
    out = os.path.join(_HERE, "dashboard_tw_pro.html")
    write_html(out, refresh=interval, with_fund=with_fund)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=_HERE, **kw)
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.path = "/dashboard_tw_pro.html"
            return super().do_GET()
        def log_message(self, *a): pass

    def loop():
        while True:
            time.sleep(interval)
            try:
                write_html(out, refresh=interval, with_fund=with_fund)
            except Exception as e:
                print(f"  更新失敗: {e}")

    threading.Thread(target=loop, daemon=True).start()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), H) as httpd:
        url = f"http://127.0.0.1:{port}/"
        print(f"\n  台股基本面儀表板：{url}\n  每 {interval}s 更新，Ctrl+C 結束\n")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止。")


def main():
    p = argparse.ArgumentParser(description="台股基本面監控儀表板")
    p.add_argument("--serve", nargs="?", const=600, type=int, metavar="SEC")
    p.add_argument("--port", type=int, default=7801)
    p.add_argument("--fast", action="store_true", help="只抓報價與估值，跳過營收/法人/除息")
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    with_fund = not args.fast
    if args.serve:
        serve(args.port, args.serve, with_fund)
    else:
        out = args.out or os.path.join(_HERE, "dashboard_tw_pro.html")
        write_html(out, refresh=0, with_fund=with_fund)
        if not args.no_open:
            webbrowser.open("file://" + os.path.abspath(out))


if __name__ == "__main__":
    main()
