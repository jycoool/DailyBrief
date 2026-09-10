#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rotation_monitor.py — 資金輪動監控模組（美股）

輪動看不到於絕對價格，只顯形於「比值」。本模組把一組配對（如 SMH/SPY）
相除成相對強弱線（RS line），量化它的方向、動能與「領導權換號」，
把「誰在領、誰在被輪出」變成每天收盤可讀的儀表板。

核心概念
--------
  RS line = A_close / B_close
    - 上揚 → 資金流入 A（A 領先 B）
    - 下彎 → 資金流出 A（A 落後 B）
  真正有用的訊號是「斜率翻號」：RS 線由上穿長均線翻成下穿（或反之），
  代表領導權易主 —— 這正是輪動的起點。

資料來源
--------
  yfinance（日線，約 15 分鐘延遲；輪動是多日現象，延遲無妨）

安裝
----
    pip install yfinance pandas numpy

執行
----
    python rotation_monitor.py              # 收盤跑一次，產生 rotation.html 並開啟
    python rotation_monitor.py --no-open    # 不自動開瀏覽器
    python rotation_monitor.py --console    # 只印文字摘要，不產 HTML
    python rotation_monitor.py --lookback 180   # 抓取天數（預設 1 年）

判讀（詳見 HTML 底部圖例，或問你的研究夥伴）
--------------------------------------------
  🟢 A 領先且動能向上   🔴 A 落後且動能向下
  🟡 剛翻多 / 剛翻空（近 5 日換號，最該注意）   ⚪ 盤整無明確方向
  所有訊號皆為「觀察門檻」，非買賣訊號。
"""

import argparse
import os
import sys
import webbrowser
from datetime import datetime

# ── SSL 憑證（沿用 market_monitor / fix_ssl 的 bundle）─────────────
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
    import numpy as np
    import pandas as pd
    import yfinance as yf
except ImportError:
    sys.exit("請先安裝套件:  pip install yfinance pandas numpy")


# ═════════════════════════════════════════════════════════════
# 設定區 — 依需求修改
# ═════════════════════════════════════════════════════════════

# 輪動配對。每筆 = (分子, 分母, 顯示名, 框架對應, A 領先時的白話意義)
# 讀法：RS = 分子/分母。RS 上揚代表「分子在贏分母」。
PAIRS = [
    ("SMH",  "SPY",  "晶片 vs 大盤",
     "河/框架一 收錢方", "半導體在領大盤（AI 交易的主心臟）"),
    ("MU",   "SMH",  "記憶體 vs 半導體",
     "框架五 循環vs結構", "記憶體在領半導體（金絲雀；最先斷的一段）"),
    ("RSP",  "SPY",  "等權 vs 市值權",
     "框架七 集中度",   "行情在變廣（不只靠巨頭）；下彎=集中度升高"),
    ("XLU",  "SPY",  "電力/公用 vs 大盤",
     "框架四 電力約束",  "電力這條 AI 下游被買（算力→電力二次擴散）"),
    ("SMH",  "XLU",  "算力 vs 電力",
     "河 上下游",       "算力層領先電力層；下彎=資金往電力搬"),
    ("QQQ",  "IWD",  "成長 vs 價值",
     "框架三 折現率",    "成長股在領價值股（利率順風時通常如此）"),
    ("IWM",  "SPY",  "小型 vs 大盤",
     "景氣循環",         "小型股在領（賭景氣復甦/風險偏好上升）"),
    ("XLY",  "XLP",  "非必需 vs 必需消費",
     "風險胃納",         "非必需消費領先（risk-on）；下彎=轉防禦"),
    ("SMH",  "QQQ",  "晶片 vs 科技整體",
     "河 收錢方vs科技",  "晶片在領廣義科技（軟體/巨頭相對被冷落）"),
]

# 避險腿：不做比值，只看自身趨勢。科技下跌日這些若同步走強 = 資金換防禦。
RISK_OFF = {
    "TMF":  "20年+美債 3x（長端利率賭注）",
    "GLDM": "黃金",
    "SGOV": "0-3月美債（現金停泊）",
    "^VIX": "VIX 波動率",
}

# 廣度用的美股宇宙（若同資料夾有 watchlist.txt 會優先讀取）
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "NVDA", "AVGO", "TSM", "MU", "INTC",
    "TSLA", "SNOW", "QQQ", "SPY", "GEV", "BWXT", "AMD", "ORCL",
]

# 參數（可調）
MA_SHORT = 20      # 短均線
MA_LONG = 50       # 長均線（RS 線的多空分界）
MOM_LB = 20        # 動能回看天數
MOM_THRESH = 2.0   # 動能門檻（%）：|20日動能| > 2% 才算「有方向」
FLIP_LB = 5        # 翻號回看天數（近 5 日內穿越長均線 = 剛翻多/翻空）
NH_LB = 60         # 新高/新低回看天數


# ── 因子暴露地圖（框架七：反假分散）─────────────────────────────
# 每筆 = (key, 驅動代號, 是否為殖利率, 顯示名, 強相關方向, beta 單位, 白話)
#   是否為殖利率=True → 因子「移動」用 diff()（殖利率變動，點；0.01=1bp）
#                =False → 用 pct_change()（價格報酬）
#   強相關方向：這個因子「危險的那一側」是正相關(+1)還是負相關(-1)，只影響白話
FACTORS = [
    ("30Y", "^TYX",     True,  "利率 30Y",  -1, "%/10bp",
     "利率賭注（殖利率漲它跌）"),
    ("SMH", "SMH",      False, "AI-beta",   +1, "×",
     "跟 AI 情緒同生共死"),
    ("DXY", "DX-Y.NYB", False, "美元 DXY",  -1, "×",
     "被強美元侵蝕（跨境部位）"),
]
FACTOR_CORR_FLAG   = 0.6    # |corr| 門檻：超過即「強曝險」標紅
FACTOR_FLAG_MIN_N  = 3      # 同一因子 ≥N 檔強曝險 → 假分散紅旗（框架七）
FACTOR_WIN_LONG    = 60     # 主窗（框架七的中期定義）
FACTOR_WIN_SHORT   = 20     # 副窗（看曝險是否收緊）
FACTOR_TIGHTEN_TH  = 0.10   # 平均|corr20|-|corr60| 超過此值 → 曝險收緊
FACTOR_SHOW_MIN    = 0.40   # 表格只列 |corr60|≥此值 的有效曝險（濾雜訊）


# ═════════════════════════════════════════════════════════════
# 工具函式
# ═════════════════════════════════════════════════════════════

def load_universe():
    """讀 watchlist.txt（沿用你的格式）取得廣度宇宙；沒有就用內建。"""
    path = os.path.join(_HERE, "watchlist.txt")
    if not os.path.exists(path):
        return list(DEFAULT_UNIVERSE)
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 逗號或空白後是名稱，取第一段當代號
        token = line.replace(",", " ").split()[0]
        out.append(token.upper())
    return out or list(DEFAULT_UNIVERSE)


def all_tickers():
    """所有要抓的代號（配對 + 避險 + 廣度宇宙），去重。"""
    s = set()
    for a, b, *_ in PAIRS:
        s.add(a)
        s.add(b)
    s.update(RISK_OFF.keys())
    s.update(f[1] for f in FACTORS)   # 因子驅動代號
    s.update(load_universe())
    return sorted(s)


def linreg_slope_pct(series, n):
    """對最後 n 點做線性回歸，回傳『每日斜率佔均值的百分比』。
    正=向上、負=向下。用來量化 RS 線的動能方向。"""
    y = series.dropna().iloc[-n:].values.astype(float)
    if len(y) < max(3, n // 2):
        return None
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]           # 每日變化（比值單位）
    base = y.mean()
    return (slope / base * 100) if base else None


def fetch_closes(tickers, lookback_days):
    """一次抓所有代號的收盤價，回傳 DataFrame（欄=代號）。"""
    period = f"{max(lookback_days, MA_LONG + NH_LB + 10)}d"
    try:
        data = yf.download(
            tickers, period=period, interval="1d",
            progress=False, auto_adjust=True, group_by="ticker",
        )
    except Exception as e:
        print(f"資料抓取失敗: {e}")
        return pd.DataFrame()

    closes = {}
    for t in tickers:
        try:
            df = data[t] if len(tickers) > 1 else data
            c = df["Close"].dropna()
            if len(c) >= MA_LONG:
                closes[t] = c
        except Exception:
            continue
    return pd.DataFrame(closes)


# ═════════════════════════════════════════════════════════════
# 核心：單一配對的輪動指標
# ═════════════════════════════════════════════════════════════

def analyze_pair(closes, num, den):
    """計算一個配對的 RS 線指標。回傳 dict 或 None。"""
    if num not in closes or den not in closes:
        return None
    r = (closes[num] / closes[den]).dropna()
    if len(r) < MA_LONG + FLIP_LB + 1:
        return None

    ma_s = r.rolling(MA_SHORT).mean()
    ma_l = r.rolling(MA_LONG).mean()
    r_last = float(r.iloc[-1])
    ma_l_last = float(ma_l.iloc[-1])

    # 20 日動能（%）與線性斜率（%/日）
    mom = (r_last / float(r.iloc[-(MOM_LB + 1)]) - 1) * 100
    slope = linreg_slope_pct(r, MOM_LB)

    # 多空位置：現值 vs 長均線
    above = r_last > ma_l_last

    # 翻號偵測：FLIP_LB 天前在均線的哪一側，跟現在比
    prev_above = float(r.iloc[-(FLIP_LB + 1)]) > float(ma_l.iloc[-(FLIP_LB + 1)])
    flipped = (above != prev_above)

    # 連續在均線同側的天數（持續性）
    diff = (r - ma_l).dropna()
    sign_now = 1 if diff.iloc[-1] > 0 else -1
    streak = 0
    for v in diff.iloc[::-1]:
        if (1 if v > 0 else -1) == sign_now:
            streak += 1
        else:
            break

    # 新高/新低（回看 NH_LB）
    window = r.iloc[-NH_LB:]
    at_high = r_last >= window.max() * 0.999
    at_low = r_last <= window.min() * 1.001

    # 訊號分類
    strong = (mom is not None and abs(mom) >= MOM_THRESH)
    if flipped:
        signal = "flip_up" if above else "flip_down"      # 🟡 剛翻多/翻空
    elif above and strong:
        signal = "lead"                                   # 🟢 領先且動能向上
    elif (not above) and strong:
        signal = "lag"                                    # 🔴 落後且動能向下
    else:
        signal = "flat"                                   # ⚪ 盤整

    return {
        "num": num, "den": den,
        "rs": r_last, "ma_long": ma_l_last,
        "above": above, "mom": mom, "slope": slope,
        "streak": streak, "at_high": at_high, "at_low": at_low,
        "flipped": flipped, "signal": signal,
    }


def analyze_risk_off(closes):
    """避險腿：看自身 20 日動能與是否站上 50 日線。"""
    out = {}
    for t in RISK_OFF:
        if t not in closes:
            continue
        c = closes[t].dropna()
        if len(c) < MA_LONG + 1:
            continue
        last = float(c.iloc[-1])
        mom = (last / float(c.iloc[-(MOM_LB + 1)]) - 1) * 100
        ma_l = float(c.rolling(MA_LONG).mean().iloc[-1])
        out[t] = {"price": last, "mom": mom, "above_ma": last > ma_l}
    return out


def analyze_breadth(closes, universe):
    """廣度代理：宇宙內站上 50/200 日線的比例（非全市場，僅 watchlist 代理）。"""
    n = above50 = above200 = 0
    for t in universe:
        if t not in closes:
            continue
        c = closes[t].dropna()
        if len(c) < 50:
            continue
        n += 1
        last = float(c.iloc[-1])
        if last > float(c.rolling(50).mean().iloc[-1]):
            above50 += 1
        if len(c) >= 200 and last > float(c.rolling(200).mean().iloc[-1]):
            above200 += 1
    if n == 0:
        return None
    return {
        "n": n,
        "pct50": above50 / n * 100,
        "pct200": above200 / n * 100,
    }


# ═════════════════════════════════════════════════════════════
# 因子暴露地圖（框架七）
# ═════════════════════════════════════════════════════════════

def _factor_move(series, is_yield):
    """把驅動因子的原始序列轉成『每日移動量』。
    殖利率 → diff()（點；0.01=1bp）；價格 → pct_change()（報酬分數）。"""
    s = series.astype(float)
    if is_yield:
        # 防呆：某些資料源用 ×10 慣例（52.1 代表 5.21%），自動還原成 %
        med = s.median(skipna=True)
        if med is not None and med > 15:
            s = s / 10.0
        return s.diff()
    return s.pct_change()


def factor_stats(stock_close, factor_series, is_yield, window):
    """個股日報酬 vs 因子移動量，回傳 (corr, beta) over 最後 window 筆對齊樣本。
    beta = 回歸斜率（個股報酬分數 / 單位因子移動）。回傳 None 表樣本不足。"""
    sr = stock_close.astype(float).pct_change()
    fm = _factor_move(factor_series, is_yield)
    df = pd.concat([sr, fm], axis=1, keys=["s", "f"]).dropna()
    need = max(10, window // 2)
    if len(df) < need:
        return None
    df = df.iloc[-window:]
    if len(df) < need:
        return None
    s = df["s"].to_numpy()
    f = df["f"].to_numpy()
    fv = f.var(ddof=0)
    if fv == 0 or np.isnan(fv):
        return None
    corr = np.corrcoef(s, f)[0, 1]
    if np.isnan(corr):
        return None
    beta = np.cov(s, f, ddof=0)[0, 1] / fv        # 斜率（分數 / 單位因子移動）
    return float(corr), float(beta)


def _beta_display(beta, is_yield):
    """把回歸斜率換成人看得懂的敏感度。
    殖利率：斜率是『每 1.0 殖利率點的報酬分數』→ 每 10bp(0.10點) 的 %：×0.10×100 = ×10。
    價格因子：斜率本身就是『每 1% 因子動，個股動幾倍』（無單位）。"""
    if is_yield:
        return beta * 10.0        # %/10bp
    return beta                    # ×（倍數）


def compute_factor_map(closes, universe):
    """對每個因子，算 watchlist 各檔的 corr(20/60) 與 beta，並做假分散彙總。"""
    result = {}
    for key, drv, is_yield, disp, hot_dir, bunit, plain in FACTORS:
        if drv not in closes:
            result[key] = {"missing": True, "disp": disp, "driver": drv,
                           "bunit": bunit}
            continue
        fser = closes[drv]
        rows = []
        for t in universe:
            if t == drv:                     # 因子自己不計入曝險
                continue
            if t not in closes:
                continue
            st60 = factor_stats(closes[t], fser, is_yield, FACTOR_WIN_LONG)
            if st60 is None:
                continue
            st20 = factor_stats(closes[t], fser, is_yield, FACTOR_WIN_SHORT)
            c60, b60 = st60
            c20 = st20[0] if st20 else None
            rows.append({
                "t": t, "c60": c60, "c20": c20,
                "beta": _beta_display(b60, is_yield),
            })
        # 依 c60 升冪：負相關（利率/美元受害者）排最前，最直覺
        rows.sort(key=lambda r: r["c60"])
        flagged = [r for r in rows if abs(r["c60"]) > FACTOR_CORR_FLAG]
        # 收緊偵測：有雙窗樣本的平均 |corr| 差
        both = [r for r in rows if r["c20"] is not None]
        tighten = None
        if both:
            tighten = (float(np.mean([abs(r["c20"]) for r in both]))
                       - float(np.mean([abs(r["c60"]) for r in both])))
        result[key] = {
            "missing": False, "disp": disp, "driver": drv, "is_yield": is_yield,
            "hot_dir": hot_dir, "bunit": bunit, "plain": plain,
            "rows": rows, "n_flag": len(flagged),
            "flag": len(flagged) >= FACTOR_FLAG_MIN_N,
            "tighten": tighten,
        }
    return result


# ═════════════════════════════════════════════════════════════
# 呈現：文字 / HTML
# ═════════════════════════════════════════════════════════════

SIG_LABEL = {
    "flip_up":   ("🟡 剛翻多", "#f0c000"),
    "flip_down": ("🟡 剛翻空", "#f0c000"),
    "lead":      ("🟢 領先↑", "#2ecc71"),
    "lag":       ("🔴 落後↓", "#e74c3c"),
    "flat":      ("⚪ 盤整",  "#8a8f98"),
}


def _corr_color(c):
    a = abs(c)
    if a > FACTOR_CORR_FLAG:  # >0.6 強
        return "#e74c3c"
    if a > FACTOR_SHOW_MIN:   # 0.4–0.6 中
        return "#f0c000"
    return "#8a8f98"          # 弱


def _factor_verdict(fd):
    """一句話結論 + 顏色。"""
    n = fd["n_flag"]
    disp = fd["disp"]
    if fd["flag"]:
        return (f"🔴 {n} 檔 |corr|>0.6 → 你的真實曝險是「{disp}」，不是這些公司",
                "#e74c3c")
    if n >= 1:
        return (f"🟡 {n} 檔強曝險，未達假分散門檻（≥{FACTOR_FLAG_MIN_N}）",
                "#f0c000")
    return (f"⚪ 無顯著「{disp}」曝險", "#8a8f98")


def render_factor_map_html(fmap):
    if not fmap:
        return ""
    cards = ""
    tables = ""
    for key, drv, is_yield, disp, *_ in FACTORS:
        fd = fmap.get(key)
        if not fd:
            continue
        if fd.get("missing"):
            cards += (f'<div class="rk"><div class="rkt">{disp}</div>'
                      f'<div class="rkd">驅動 {fd["driver"]}</div>'
                      f'<div class="rkm" style="color:#8a8f98">資料缺</div>'
                      f'<div class="rkma">未抓到 {fd["driver"]}，'
                      f'可改用代理（如 DXY→UUP）</div></div>')
            continue
        verdict, vcol = _factor_verdict(fd)
        # 收緊註記
        tnote = ""
        tg = fd["tighten"]
        if tg is not None:
            if tg > FACTOR_TIGHTEN_TH:
                tnote = (f'<div class="ftight warn2">⚠ 曝險收緊中'
                         f'（20日−60日 {tg:+.2f}）— 壓力/流動性特徵</div>')
            elif tg < -FACTOR_TIGHTEN_TH:
                tnote = (f'<div class="ftight">曝險鬆動（20日−60日 {tg:+.2f}）</div>')
        cards += (f'<div class="rk">'
                  f'<div class="rkt">{disp}</div>'
                  f'<div class="rkd">驅動 {drv}·{fd["plain"]}</div>'
                  f'<div class="rkm" style="color:{vcol};font-size:13px;'
                  f'line-height:1.35">{verdict}</div>{tnote}</div>')

        # 明細表：只列 |c60|≥FACTOR_SHOW_MIN 的有效曝險
        vis = [r for r in fd["rows"] if abs(r["c60"]) >= FACTOR_SHOW_MIN]
        bunit = fd["bunit"]
        body = ""
        for r in vis:
            ccol = _corr_color(r["c60"])
            c20 = f'{r["c20"]:+.2f}' if r["c20"] is not None else "—"
            bfmt = (f'{r["beta"]:+.2f}' if not is_yield
                    else f'{r["beta"]:+.2f}')
            body += (f'<tr><td class="ft">{r["t"]}</td>'
                     f'<td class="num"><span class="cchip" '
                     f'style="background:{ccol}">{r["c60"]:+.2f}</span></td>'
                     f'<td class="num fsub">{c20}</td>'
                     f'<td class="num">{bfmt}<span class="fu">{bunit}</span></td>'
                     f'</tr>')
        if not body:
            body = ('<tr><td colspan="4" class="fsub">'
                    '此因子下無 |corr|≥0.4 的顯著曝險</td></tr>')
        tables += (f'<div class="ftbox"><div class="fthead">{disp}'
                   f'<span class="fsub">　corr 越負＝越受此因子傷害；'
                   f'排序：最負在上</span></div>'
                   f'<table class="ftable"><tr>'
                   f'<th>代號</th><th>corr 60d</th><th>corr 20d</th>'
                   f'<th>beta</th></tr>{body}</table></div>')

    return (f'<h2>因子暴露地圖（框架七·反假分散）</h2>'
            f'<div class="rkgrid">{cards}</div>'
            f'<div class="ftwrap">{tables}</div>'
            f'<div class="fhint">corr＝個股日報酬 vs 因子移動的相關性'
            f'（可靠度，−1~+1）；beta＝敏感度（30Y 為 %/10bp、其餘為每 1% 的倍數）。'
            f'　|corr|&gt;0.6 標紅＝強曝險；同因子 ≥3 檔即亮假分散紅旗。'
            f'　只列 |corr60|≥0.4 的有效曝險。</div>')


def print_factor_console(fmap):
    if not fmap:
        return
    print("-" * 62)
    print("  因子暴露地圖（框架七）：")
    for key, drv, is_yield, disp, *_ in FACTORS:
        fd = fmap.get(key)
        if not fd:
            continue
        if fd.get("missing"):
            print(f"    {disp:<10} 資料缺（未抓到 {fd['driver']}）")
            continue
        verdict, _ = _factor_verdict(fd)
        print(f"    {disp:<10} {verdict}")
        tg = fd["tighten"]
        if tg is not None and abs(tg) > FACTOR_TIGHTEN_TH:
            state = "收緊" if tg > 0 else "鬆動"
            print(f"    {'':<10} └ 曝險{state}（20日−60日 {tg:+.2f}）")
        vis = [r for r in fd["rows"] if abs(r["c60"]) >= FACTOR_SHOW_MIN][:5]
        for r in vis:
            c20 = f'{r["c20"]:+.2f}' if r["c20"] is not None else "  — "
            print(f"    {'':<10}   {r['t']:<7} c60 {r['c60']:+.2f} "
                  f"c20 {c20}  β {r['beta']:+.2f}{fd['bunit']}")


def print_console(pairs, risk, breadth):
    print("\n" + "=" * 62)
    print(f"  資金輪動監控   {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 62)
    if breadth:
        print(f"  廣度代理（watchlist {breadth['n']} 檔）："
              f"站上50日 {breadth['pct50']:.0f}% ／ 站上200日 {breadth['pct200']:.0f}%")
    print("-" * 62)
    print(f"  {'配對':<18}{'訊號':<10}{'20日動能':>9}{'連續天':>7}")
    print("-" * 62)
    for p in pairs:
        if not p:
            continue
        name = f"{p['num']}/{p['den']}"
        label = SIG_LABEL[p["signal"]][0]
        mom = f"{p['mom']:+.1f}%"
        tag = "＊新高" if p["at_high"] else ("＊新低" if p["at_low"] else "")
        print(f"  {name:<18}{label:<10}{mom:>9}{p['streak']:>6}d  {tag}")
    print("-" * 62)
    print("  避險腿（科技下跌日若同步走強＝資金換防禦）：")
    for t, d in risk.items():
        arrow = "↑" if d["mom"] > 0 else "↓"
        print(f"    {t:<7}{RISK_OFF[t]:<22} 20日 {d['mom']:+.1f}% {arrow}"
              f"  {'站上50日' if d['above_ma'] else '在50日下'}")
    print("=" * 62)
    print("  皆為觀察門檻，非買賣訊號。\n")


def render_html(pairs, risk, breadth, factor_section=""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 配對表列
    rows = ""
    for i, p in enumerate(pairs):
        if not p:
            continue
        label, color = SIG_LABEL[p["signal"]]
        name = f"{p['num']} / {p['den']}"
        # 從 PAIRS 取白話與框架對應
        meta = PAIRS[i]
        disp, fw, plain = meta[2], meta[3], meta[4]
        mom = f"{p['mom']:+.1f}%"
        slope = f"{p['slope']:+.2f}" if p["slope"] is not None else "—"
        pos = "＞長均" if p["above"] else "＜長均"
        tag = " 🔺新高" if p["at_high"] else (" 🔻新低" if p["at_low"] else "")
        rows += f"""
        <tr>
          <td class="pair">{name}<div class="disp">{disp}</div></td>
          <td><span class="sig" style="background:{color}">{label}</span></td>
          <td class="num">{pos}</td>
          <td class="num">{mom}</td>
          <td class="num">{p['streak']}d{tag}</td>
          <td class="plain">{plain}</td>
          <td class="fw">{fw}</td>
        </tr>"""

    # 避險腿
    rk = ""
    for t, d in risk.items():
        arrow = "▲" if d["mom"] > 0 else "▼"
        acol = "#2ecc71" if d["mom"] > 0 else "#e74c3c"
        ma = "站上50日" if d["above_ma"] else "在50日下"
        rk += f"""
        <div class="rk">
          <div class="rkt">{t}</div>
          <div class="rkd">{RISK_OFF[t]}</div>
          <div class="rkm" style="color:{acol}">{arrow} {d['mom']:+.1f}%</div>
          <div class="rkma">{ma}</div>
        </div>"""

    bd = ""
    if breadth:
        bd = (f"廣度代理（watchlist {breadth['n']} 檔）："
              f"站上 50 日線 <b>{breadth['pct50']:.0f}%</b>　·　"
              f"站上 200 日線 <b>{breadth['pct200']:.0f}%</b>"
              f"　<span class='hint'>（&gt;60% 偏廣、健康；&lt;40% 偏窄、只剩少數在撐）</span>")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>資金輪動監控</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ background:#14171c; color:#e6e8eb; font-family:-apple-system,
         "Segoe UI","PingFang TC","Microsoft JhengHei",sans-serif;
         margin:0; padding:24px; }}
  .wrap {{ max-width:1000px; margin:0 auto; }}
  h1 {{ font-size:20px; margin:0 0 2px; }}
  .ts {{ color:#8a8f98; font-size:13px; margin-bottom:16px; }}
  .breadth {{ background:#1b1f26; border:1px solid #2a2f37; border-radius:10px;
             padding:12px 16px; font-size:14px; margin-bottom:16px; }}
  .breadth b {{ color:#f0c000; }}
  .hint {{ color:#8a8f98; font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; background:#1b1f26;
          border-radius:10px; overflow:hidden; }}
  th,td {{ padding:11px 12px; text-align:left; font-size:14px;
          border-bottom:1px solid #23272f; }}
  th {{ background:#20242c; color:#a9b0ba; font-weight:600; font-size:12px; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .pair {{ font-weight:600; }}
  .disp {{ color:#8a8f98; font-size:12px; font-weight:400; }}
  .sig {{ padding:3px 9px; border-radius:20px; color:#111; font-weight:700;
         font-size:12px; white-space:nowrap; }}
  .plain {{ color:#c3c8cf; font-size:13px; }}
  .fw {{ color:#7f8896; font-size:12px; }}
  .rkgrid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
            gap:10px; margin-top:8px; }}
  .rk {{ background:#1b1f26; border:1px solid #2a2f37; border-radius:10px;
        padding:12px; }}
  .rkt {{ font-weight:700; font-size:15px; }}
  .rkd {{ color:#8a8f98; font-size:11px; min-height:28px; }}
  .rkm {{ font-weight:700; font-size:15px; margin-top:4px; }}
  .rkma {{ color:#a9b0ba; font-size:12px; }}
  h2 {{ font-size:15px; margin:22px 0 6px; color:#c3c8cf; }}
  .legend {{ background:#1b1f26; border:1px solid #2a2f37; border-radius:10px;
            padding:14px 18px; font-size:13px; line-height:1.7; color:#c3c8cf;
            margin-top:20px; }}
  .legend b {{ color:#e6e8eb; }}
  .ftight {{ color:#8a8f98; font-size:11px; margin-top:6px; }}
  .ftight.warn2 {{ color:#f0c86e; }}
  .ftwrap {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
             gap:12px; margin-top:12px; }}
  .ftbox {{ background:#1b1f26; border:1px solid #2a2f37; border-radius:10px;
            padding:10px 12px; }}
  .fthead {{ font-weight:700; font-size:13px; color:#c3c8cf; margin-bottom:6px; }}
  .ftable {{ width:100%; border-collapse:collapse; background:transparent;
             border-radius:0; }}
  .ftable th {{ background:transparent; color:#7f8896; font-size:11px;
                padding:4px 6px; border-bottom:1px solid #23272f; text-align:right; }}
  .ftable th:first-child {{ text-align:left; }}
  .ftable td {{ padding:5px 6px; font-size:13px; border-bottom:1px solid #20242c; }}
  .ftable td.ft {{ font-weight:600; }}
  .cchip {{ padding:2px 7px; border-radius:12px; color:#111; font-weight:700;
            font-size:12px; font-variant-numeric:tabular-nums; }}
  .fsub {{ color:#8a8f98; font-size:11px; font-weight:400; }}
  .fu {{ color:#7f8896; font-size:10px; margin-left:2px; }}
  .fhint {{ color:#7f8896; font-size:12px; margin-top:10px; line-height:1.6; }}
  .foot {{ color:#6b7280; font-size:12px; margin-top:16px; }}
</style></head>
<body><div class="wrap">
  <h1>資金輪動監控</h1>
  <div class="ts">{ts}　·　RS 線＝分子／分母，上揚代表分子在贏分母</div>
  <div class="breadth">{bd}</div>

  <table>
    <tr><th>配對</th><th>訊號</th><th>位置</th><th>20日動能</th>
        <th>連續</th><th>白話（分子領先時）</th><th>框架</th></tr>
    {rows}
  </table>

  <h2>避險腿（regime 溫度計）</h2>
  <div class="rkgrid">{rk}</div>

  {factor_section}

  <div class="legend">
    <b>怎麼判讀</b><br>
    · <b>🟢 領先↑</b>：分子站上長均線且 20 日動能 &gt; +2%，這個方向的輪動正在進行。<br>
    · <b>🔴 落後↓</b>：分子跌破長均線且動能 &lt; −2%，分子正被輪出。<br>
    · <b>🟡 剛翻多／翻空</b>：近 5 日內 RS 線穿越長均線 —— <b>領導權換號，最該注意</b>，
       但需配合廣度與連續天數確認，別被單日雜訊騙。<br>
    · <b>⚪ 盤整</b>：無明確方向。<br><br>
    <b>三步確認一次真輪動</b>：①訊號燈翻號 → ②連續天數開始累積（&ge;3d）
       → ③廣度同向（變廣配 risk-on、變窄配 risk-off）。三者齊備才動作。<br><br>
    <b>對持股的意義</b>：SMH/SPY 是你組合主心臟；MU/SMH 是記憶體金絲雀（最先斷）；
       RSP/SPY 上彎代表你被迫承擔的巨頭集中度在下降。避險腿在「科技下跌日」
       同步走強＝資金在換防禦，是最重要的 regime 轉折訊號。
  </div>
  <div class="foot">資料 yfinance（約 15 分延遲）·　皆為觀察門檻，非投資建議。</div>
</div></body></html>"""


# ═════════════════════════════════════════════════════════════
# 主流程
# ═════════════════════════════════════════════════════════════

def run(lookback, make_html=True, open_browser=True, console=False):
    tickers = all_tickers()
    print(f"抓取 {len(tickers)} 個代號…")
    closes = fetch_closes(tickers, lookback)
    if closes.empty:
        sys.exit("沒有抓到資料，請檢查網路 / SSL 設定。")

    pairs = [analyze_pair(closes, a, b) for a, b, *_ in PAIRS]
    risk = analyze_risk_off(closes)
    universe = load_universe()
    breadth = analyze_breadth(closes, universe)
    fmap = compute_factor_map(closes, universe)

    if console:
        print_console(pairs, risk, breadth)
        print_factor_console(fmap)
        return

    print_console(pairs, risk, breadth)  # 一律也印文字，方便日誌
    print_factor_console(fmap)

    if make_html:
        html = render_html(pairs, risk, breadth, render_factor_map_html(fmap))
        out = os.path.join(_HERE, "rotation.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"已產生 {out}")
        if open_browser:
            webbrowser.open("file://" + os.path.abspath(out))


def main():
    ap = argparse.ArgumentParser(description="資金輪動監控（美股）")
    ap.add_argument("--lookback", type=int, default=365, help="抓取天數（預設 365）")
    ap.add_argument("--no-open", action="store_true", help="不自動開瀏覽器")
    ap.add_argument("--console", action="store_true", help="只印文字，不產 HTML")
    args = ap.parse_args()
    run(args.lookback, make_html=not args.console,
        open_browser=not args.no_open, console=args.console)


if __name__ == "__main__":
    main()
