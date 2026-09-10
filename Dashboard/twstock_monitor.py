#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twstock_monitor.py — 台股專用市場監控儀表板

籌碼驅動的台股，重點在三大法人與外資動向，而非美股那套折現率邏輯。

資料來源:
  1. 證交所 (TWSE) 官方 OpenAPI  — 三大法人買賣超（免金鑰、官方 T86 資料）
  2. yfinance                     — 報價、加權指數、匯率、費半、油金

安裝:
    pip install yfinance pandas requests

執行:
    python twstock_monitor.py            產生 dashboard_tw.html 並開啟
    python twstock_monitor.py --serve    本機伺服器，自動更新
    python twstock_monitor.py --serve 300 --port 7799
    python twstock_monitor.py --no-open
    python twstock_monitor.py --fast     跳過三大法人抓取（較快）

說明:
  - 三大法人資料為每日收盤後（約 15:00 後）更新，非盤中即時
  - 報價經 yfinance，約 15 分鐘延遲
  - 所有警示為觀察門檻，非買賣訊號
"""

import argparse
import os
import sys
import time
import json
import webbrowser
from datetime import datetime, timedelta

# ── SSL 憑證（沿用 fix_ssl.py 產生的 bundle）─────────────────
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
# 設定區 — 依需求修改
# ═════════════════════════════════════════════════════════════

# 觀察清單。鍵為證交所代號（純數字），value 為 (顯示名稱, 市場)
# 市場: "TW" = 上市, "TWO" = 上櫃
WATCHLIST = {
    "2330": ("台積電",      "TW"),
    "2317": ("鴻海",        "TW"),
    "2454": ("聯發科",      "TW"),
    "2308": ("台達電",      "TW"),
    "2382": ("廣達",        "TW"),
    "3231": ("緯創",        "TW"),
    "6669": ("緯穎",        "TW"),
    "2379": ("瑞昱",        "TW"),
    "3034": ("聯詠",        "TW"),
    "3661": ("世芯-KY",     "TW"),
    "2891": ("中信金",      "TW"),
    "2412": ("中華電",      "TW"),
}

# 總經與大盤指標（走 yfinance）
MACRO = {
    "^TWII":  ("加權指數", "TAIEX", ""),
    "TWD=X":  ("美元/新台幣", "USDTWD", ""),
    "^SOX":   ("費城半導體", "SOX", ""),
    "^IXIC":  ("那斯達克", "Nasdaq", ""),
    "^VIX":   ("VIX 波動率", "VIX", ""),
    "^TNX":   ("美10年殖利率", "US10Y", "%"),
    "GC=F":   ("黃金", "Gold", "$"),
    "CL=F":   ("西德州原油", "WTI", "$"),
}

# 台股專屬警示門檻
ALERTS = {
    "twd_weak":     33.0,   # 台幣貶破 33 → 外資匯出壓力
    "twd_strong":   30.0,   # 台幣升破 30 → 外資回流
    "vix_high":     25.0,
    "sox_drop":     -3.0,   # 費半單日跌逾 3% → 隔日台股電子承壓
    "sox_jump":      3.0,   # 費半單日漲逾 3% → 隔日台股電子受惠
    "foreign_big": 10000,   # 外資單日買賣超（張）絕對值門檻，1 萬張為大額
    "ma200_dev":     2.0,   # 年線乖離 ±2% → 觀察區
}

# 證交所 OpenAPI（免金鑰）
TWSE_T86 = "https://openapi.twse.com.tw/v1/fund/T86"       # 上市三大法人
TPEX_INST = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"  # 上櫃

# ═════════════════════════════════════════════════════════════


def fetch_institutional():
    """
    抓取證交所官方三大法人買賣超（T86，全上市股當日資料）。
    回傳 {代號: {foreign, trust, dealer, total}}，單位為「張」（股數÷1000）。
    抓不到時回傳空 dict，程式自動降級為純報價模式。

    T86 官方欄位（單位：股）:
      證券代號 / 證券名稱
      外陸資買賣超股數(不含外資自營商)   ← 外資主體
      外資自營商買賣超股數
      投信買賣超股數
      自營商買賣超股數                    ← 自營商合計（自行+避險）
      三大法人買賣超股數
    """
    result = {}
    try:
        r = requests.get(TWSE_T86, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  三大法人 API 無法連線（{e}），改為純報價模式")
        return result, None

    def pick(row, *keys):
        for k in keys:
            if k in row and row[k] not in ("", None):
                return row[k]
        return "0"

    def lots(s):
        """股數字串 → 張（÷1000），四捨五入到整數張。"""
        try:
            return round(float(str(s).replace(",", "")) / 1000)
        except Exception:
            return 0

    for row in data:
        code = str(pick(row, "證券代號", "Code")).strip()
        if not code:
            continue
        # 外資 = 外陸資（不含外資自營商）+ 外資自營商，貼近市場習慣的「外資合計」
        foreign_main = lots(pick(row, "外陸資買賣超股數(不含外資自營商)"))
        foreign_dealer = lots(pick(row, "外資自營商買賣超股數"))
        foreign = foreign_main + foreign_dealer
        trust = lots(pick(row, "投信買賣超股數"))
        dealer = lots(pick(row, "自營商買賣超股數"))
        total = lots(pick(row, "三大法人買賣超股數"))
        result[code] = {"foreign": foreign, "trust": trust,
                        "dealer": dealer, "total": total}

    return result, None


def yf_symbol(code, market):
    return f"{code}.{'TWO' if market == 'TWO' else 'TW'}"


def fetch_prices(symbols):
    """抓取報價與 200 日均線。"""
    out = {}
    symbols = list(symbols)
    try:
        data = yf.download(symbols, period="1y", interval="1d",
                          progress=False, auto_adjust=True,
                          group_by="ticker", threads=True)
    except Exception as e:
        print(f"  報價抓取失敗: {e}")
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
                "high_52w": float(close.max()),
                "low_52w": float(close.min()),
            }
        except Exception:
            continue
    return out


def check_alerts(macro, stocks, inst):
    """回傳 [(等級, 標題, 說明)]。等級: critical / watch / calm"""
    out = []
    twd = macro.get("TWD=X", {}).get("price")
    vix = macro.get("^VIX", {}).get("price")
    sox = macro.get("^SOX", {})

    if twd:
        if twd >= ALERTS["twd_weak"]:
            out.append(("critical", f"新台幣 {twd:.3f}",
                        f"貶破 {ALERTS['twd_weak']}，外資匯出壓力，台股常同步承壓"))
        elif twd <= ALERTS["twd_strong"]:
            out.append(("calm", f"新台幣 {twd:.3f}",
                        f"升破 {ALERTS['twd_strong']}，外資回流訊號"))
    if sox and sox.get("chg_pct") is not None:
        c = sox["chg_pct"]
        if c <= ALERTS["sox_drop"]:
            out.append(("watch", f"費半隔夜 {c:+.1f}%",
                        "電子權值隔日開盤易承壓"))
        elif c >= ALERTS["sox_jump"]:
            out.append(("calm", f"費半隔夜 {c:+.1f}%",
                        "電子權值隔日開盤有撐"))
    if vix and vix >= ALERTS["vix_high"]:
        out.append(("critical", f"VIX {vix:.1f}", "美股避險升溫，外資風險偏好下降"))

    # 三大法人外資大額進出
    if inst:
        for code, (name, mkt) in WATCHLIST.items():
            d = inst.get(code)
            if not d:
                continue
            f = d["foreign"]
            if abs(f) >= ALERTS["foreign_big"]:
                lvl = "watch"
                direction = "買超" if f > 0 else "賣超"
                out.append((lvl, f"{name} 外資{direction} {abs(f):,.0f} 張",
                            "外資單日大額進出"))

    # 年線
    for code, (name, mkt) in WATCHLIST.items():
        d = stocks.get(yf_symbol(code, mkt))
        if not d or d.get("ma200_dev") is None:
            continue
        dev = d["ma200_dev"]
        if abs(dev) <= ALERTS["ma200_dev"]:
            out.append(("watch", f"{name} 貼近年線", f"乖離 {dev:+.1f}%"))
    return out


# ═════════════════════════════════════════════════════════════
# HTML
# ═════════════════════════════════════════════════════════════

CSS = """
:root{
  --bg:#0E1116; --panel:#161B22; --panel-2:#1C232C; --edge:#2A333F;
  --tw-red:#E03A3A; --tw-green:#17A673;
  --ink:#E6EDF3; --ink-2:#8B98A5; --ink-3:#5B6875;
  --gold:#D4A73C; --foreign:#4C9BE0; --trust:#E0844C; --dealer:#9B7FD4;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Noto Sans TC','IBM Plex Sans',system-ui,sans-serif;
  background:var(--bg);color:var(--ink);padding:24px 16px 60px;line-height:1.5;
}
.wrap{max-width:1140px;margin:0 auto}
.num{font-family:'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace;
  font-variant-numeric:tabular-nums}
/* 台股紅漲綠跌 */
.up{color:var(--tw-red)} .down{color:var(--tw-green)} .flat{color:var(--ink-2)}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink-3);font-weight:600}

header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;
  flex-wrap:wrap;padding-bottom:14px;margin-bottom:22px;
  border-bottom:1px solid var(--edge);position:relative}
header::after{content:"";position:absolute;left:0;bottom:-1px;width:88px;height:3px;
  background:var(--tw-red)}
h1{font-size:26px;font-weight:700;letter-spacing:.02em}
h1 small{font-size:13px;color:var(--ink-3);font-weight:400;letter-spacing:.08em;
  margin-left:8px}
.conv{font-size:11px;color:var(--ink-3);margin-top:4px}
.conv b{color:var(--tw-red)} .conv i{color:var(--tw-green);font-style:normal}
.stamp{text-align:right}
.stamp .t{font-size:14px;margin-top:2px}

section{margin-bottom:26px}
.shead{display:flex;align-items:baseline;gap:10px;margin-bottom:11px}
.shead::after{content:"";flex:1;height:1px;background:var(--edge)}

.macro{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:1px;
  background:var(--edge);border:1px solid var(--edge);border-radius:6px;overflow:hidden}
.cell{background:var(--panel);padding:11px 13px}
.cell .lbl{font-size:11px;color:var(--ink-2);margin-bottom:4px}
.cell .lbl small{color:var(--ink-3);font-size:9.5px;letter-spacing:.05em}
.cell .val{font-size:20px;font-weight:500}
.cell .chg{font-size:12px;margin-top:1px}

.alerts{display:flex;flex-direction:column;gap:1px;background:var(--edge);
  border:1px solid var(--edge);border-radius:6px;overflow:hidden}
.al{background:var(--panel);padding:11px 14px;display:flex;gap:11px;align-items:baseline}
.al .bar{width:3px;align-self:stretch;flex:0 0 3px;border-radius:2px}
.al.critical .bar{background:var(--tw-red)}
.al.watch .bar{background:var(--gold)}
.al.calm .bar{background:var(--tw-green)}
.al .h{font-weight:600;font-size:13.5px}
.al .d{font-size:12px;color:var(--ink-2)}
.empty{background:var(--panel);border:1px solid var(--edge);border-radius:6px;
  padding:15px;font-size:13px;color:var(--ink-2)}

table{width:100%;border-collapse:collapse;background:var(--panel);
  border:1px solid var(--edge);border-radius:6px;overflow:hidden;font-size:13px}
th{font-size:10.5px;letter-spacing:.06em;color:var(--ink-3);text-align:right;
  padding:9px 10px;border-bottom:1px solid var(--edge);font-weight:600;
  text-transform:uppercase;white-space:nowrap}
th:first-child,th:nth-child(2){text-align:left}
td{padding:9px 10px;border-bottom:1px solid #1E2530;text-align:right;white-space:nowrap}
td:first-child,td:nth-child(2){text-align:left}
tr:last-child td{border-bottom:0}
tr:hover td{background:var(--panel-2)}
.tick{font-weight:600} .cd{color:var(--ink-3);font-size:11px}
.name{color:var(--ink-2);font-size:12.5px}

/* 三大法人買賣超條 */
.flow{display:inline-flex;align-items:center;gap:6px;justify-content:flex-end}
.flowbar{height:14px;display:inline-block;border-radius:2px;min-width:1px}
.pill{display:inline-block;padding:1px 6px;font-size:10.5px;border:1px solid var(--gold);
  color:var(--gold);border-radius:3px;margin-left:5px}

.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:9px;font-size:11.5px;
  color:var(--ink-2)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;
  margin-right:5px;vertical-align:middle}

footer{margin-top:30px;padding-top:13px;border-top:1px solid var(--edge);
  font-size:11px;color:var(--ink-3);line-height:1.9}

@media (max-width:640px){
  body{padding:16px 10px 44px}
  h1{font-size:21px}
  .name,.hl-col{display:none}
}
"""


def _cls(v):
    return "up" if v > 0.03 else "down" if v < -0.03 else "flat"


def build_html(macro, stocks, inst, inst_date, refresh=0):
    now = datetime.now()
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""

    twd = macro.get("TWD=X", {}).get("price")
    conv = ""
    if twd:
        conv = (f'<div class="conv">台股慣例 <b>紅漲</b> · <i>綠跌</i>　｜　'
                f'新台幣 <span class="num">{twd:.3f}</span></div>')

    # ── 警示
    al = check_alerts(macro, stocks, inst)
    if al:
        order = {"critical": 0, "watch": 1, "calm": 2}
        al.sort(key=lambda x: order.get(x[0], 9))
        alerts_html = '<div class="alerts">' + "".join(
            f'<div class="al {lv}"><span class="bar"></span>'
            f'<span class="h">{h}</span><span class="d">{d}</span></div>'
            for lv, h, d in al) + '</div>'
    else:
        alerts_html = '<div class="empty">目前沒有指標觸及設定門檻。</div>'

    # ── 總經
    cells = []
    for t, (label, sub, unit) in MACRO.items():
        d = macro.get(t)
        if not d:
            continue
        v = d["price"]
        vs = f"{v:.2f}%" if unit == "%" else f"{v:,.2f}"
        cells.append(
            f'<div class="cell"><div class="lbl">{label} '
            f'<small>{sub}</small></div>'
            f'<div class="val num">{vs}</div>'
            f'<div class="chg num {_cls(d["chg_pct"])}">{d["chg_pct"]:+.2f}%</div></div>')
    macro_html = f'<div class="macro">{"".join(cells)}</div>' if cells \
        else '<div class="empty">未取得總經資料</div>'

    # ── 三大法人最大買賣超尺度（給條長度正規化）
    max_flow = 1.0
    if inst:
        for code in WATCHLIST:
            d = inst.get(code)
            if d:
                max_flow = max(max_flow, abs(d["foreign"]), abs(d["trust"]),
                              abs(d["dealer"]))

    # ── 觀察清單表
    rows = []
    for code, (name, mkt) in WATCHLIST.items():
        d = stocks.get(yf_symbol(code, mkt))
        price_cell = chg_cell = dev_cell = '<span class="flat">—</span>'
        if d:
            price_cell = f'<span class="num">{d["price"]:,.2f}</span>'
            chg_cell = f'<span class="num {_cls(d["chg_pct"])}">{d["chg_pct"]:+.2f}%</span>'
            dev = d.get("ma200_dev")
            if dev is not None:
                near = abs(dev) <= ALERTS["ma200_dev"]
                tag = '<span class="pill">年線</span>' if near else ''
                dev_cell = f'<span class="num {_cls(dev)}">{dev:+.1f}%</span>{tag}'

        # 三大法人
        fo = tr = de = None
        if inst and code in inst:
            fo = inst[code]["foreign"]
            tr = inst[code]["trust"]
            de = inst[code]["dealer"]

        def flow_cell(v, color):
            if v is None:
                return '<span class="flat">—</span>'
            w = min(abs(v) / max_flow * 60, 60)
            cls = "up" if v > 0 else "down" if v < 0 else "flat"
            sign = "+" if v > 0 else ""
            return (f'<span class="flow"><span class="num {cls}">{sign}{v:,.0f}</span>'
                    f'<span class="flowbar" style="width:{w:.0f}px;background:{color}"></span></span>')

        rows.append(f"""<tr>
  <td class="tick">{name}<span class="cd"> {code}</span></td>
  <td class="hl-col name">{'上市' if mkt=='TW' else '上櫃'}</td>
  <td>{price_cell}</td>
  <td>{chg_cell}</td>
  <td>{dev_cell}</td>
  <td>{flow_cell(fo, 'var(--foreign)')}</td>
  <td>{flow_cell(tr, 'var(--trust)')}</td>
  <td>{flow_cell(de, 'var(--dealer)')}</td>
</tr>""")

    inst_note = ""
    if not inst:
        inst_note = ('<div class="empty" style="margin-bottom:10px">三大法人資料暫時無法取得'
                     '（證交所收盤後約 15:00 更新，或今日非交易日）。'
                     '以下僅顯示報價。</div>')

    table_html = inst_note + f"""<table>
<thead><tr>
  <th>股票</th><th class="hl-col">市場</th><th>股價</th><th>漲跌</th>
  <th>年線乖離</th><th>外資</th><th>投信</th><th>自營商</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="legend">
  <span><i style="background:var(--foreign)"></i>外資買賣超</span>
  <span><i style="background:var(--trust)"></i>投信買賣超</span>
  <span><i style="background:var(--dealer)"></i>自營商買賣超</span>
  <span style="color:var(--ink-3)">單位：張（1張=1000股）　紅正買超 · 綠負賣超</span>
</div>"""

    refresh_note = f"　每 {refresh} 秒更新" if refresh else ""
    inst_date_note = f"　三大法人：{inst_date}" if inst_date else ""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{meta}
<title>台股監控儀表板</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body><div class="wrap">

<header>
  <div>
    <h1>台股監控 <small>TW Market Monitor</small></h1>
    {conv}
  </div>
  <div class="stamp">
    <div class="eyebrow">資料時間</div>
    <div class="t num">{now:%Y-%m-%d %H:%M}</div>
    <div class="eyebrow" style="margin-top:2px">報價約 15 分延遲{refresh_note}{inst_date_note}</div>
  </div>
</header>

<section>
  <div class="shead"><span class="eyebrow">門檻警示</span></div>
  {alerts_html}
</section>

<section>
  <div class="shead"><span class="eyebrow">大盤與國際連動</span></div>
  {macro_html}
</section>

<section>
  <div class="shead"><span class="eyebrow">觀察清單 · 三大法人買賣超</span></div>
  {table_html}
</section>

<footer>
  三大法人資料來源：證交所（TWSE）官方 OpenAPI，收盤後每日更新。<br>
  報價、指數、匯率來源：yfinance，約 15 分鐘延遲，非即時。<br>
  外資動向與台幣匯率是台股短線最關鍵的籌碼指標，但三大法人為「當日收盤後」資料，非盤中。<br>
  警示為自訂觀察門檻，非買賣訊號。所有投資決策請自行判斷並自負風險。
</footer>

</div></body></html>"""


# ═════════════════════════════════════════════════════════════

def collect(with_inst=True):
    print("  抓取大盤與國際指標…")
    macro_out = fetch_prices(list(MACRO))

    print("  抓取個股報價…")
    syms = [yf_symbol(c, m) for c, (n, m) in WATCHLIST.items()]
    stocks = fetch_prices(syms)

    inst, inst_date = ({}, None)
    if with_inst:
        print("  抓取證交所三大法人買賣超…")
        inst, inst_date = fetch_institutional()
        if inst:
            inst_date = datetime.now().strftime("%m/%d")
    return macro_out, stocks, inst, inst_date


def write_html(path, refresh=0, with_inst=True):
    macro, stocks, inst, inst_date = collect(with_inst)
    html = build_html(macro, stocks, inst, inst_date, refresh)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    ninst = len(inst) if inst else 0
    print(f"  已更新 {path}（大盤 {len(macro)} / 個股 {len(stocks)} / 法人資料 {ninst} 檔）")
    return path


def serve(port, interval, with_inst):
    import http.server, socketserver, threading
    out = os.path.join(_HERE, "dashboard_tw.html")
    write_html(out, refresh=interval, with_inst=with_inst)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=_HERE, **kw)
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.path = "/dashboard_tw.html"
            return super().do_GET()
        def log_message(self, *a): pass

    def loop():
        while True:
            time.sleep(interval)
            try:
                write_html(out, refresh=interval, with_inst=with_inst)
            except Exception as e:
                print(f"  更新失敗: {e}")

    threading.Thread(target=loop, daemon=True).start()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), H) as httpd:
        url = f"http://127.0.0.1:{port}/"
        print(f"\n  台股儀表板啟動：{url}")
        print(f"  每 {interval} 秒重新抓取，按 Ctrl+C 結束\n")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止。")


def main():
    p = argparse.ArgumentParser(description="台股監控儀表板")
    p.add_argument("--serve", nargs="?", const=300, type=int, metavar="SEC")
    p.add_argument("--port", type=int, default=7799)
    p.add_argument("--fast", action="store_true", help="跳過三大法人抓取")
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    with_inst = not args.fast
    if args.serve:
        serve(args.port, args.serve, with_inst)
    else:
        out = args.out or os.path.join(_HERE, "dashboard_tw.html")
        write_html(out, refresh=0, with_inst=with_inst)
        if not args.no_open:
            webbrowser.open("file://" + os.path.abspath(out))


if __name__ == "__main__":
    main()
