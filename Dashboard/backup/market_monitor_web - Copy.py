#!/usr/bin/env python3
"""
market_monitor_web.py — 市場監控儀表板（網頁版）

與終端機版相同的監控邏輯，但輸出為 HTML 儀表板。

安裝:
    pip install yfinance pandas requests

執行:
    python market_monitor_web.py            產生 dashboard.html 並開啟瀏覽器
    python market_monitor_web.py --serve    啟動本機伺服器，網頁每 5 分鐘自動更新
    python market_monitor_web.py --serve 60 自訂更新秒數
    python market_monitor_web.py --port 8080
    python market_monitor_web.py --no-open  只產生檔案，不開瀏覽器
    python market_monitor_web.py --fast     跳過本益比抓取（較快）

注意:
  - 資料來源 yfinance，約 15 分鐘延遲
  - 警示為觀察門檻，非買賣訊號
  - 相容 yfinance 0.2.x 與 1.5.x
"""

import argparse
import os
import sys
import time
import webbrowser
from datetime import datetime

# ── SSL 憑證設定 ──────────────────────────────────────────────
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
    import yfinance as yf
    import pandas as pd
except ImportError:
    sys.exit("請先安裝套件:  pip install yfinance pandas requests")


# ═════════════════════════════════════════════════════════════
# 設定區 — 依需求修改
# ═════════════════════════════════════════════════════════════

FRED_API_KEY = ""   # https://fred.stlouisfed.org/docs/api/api_key.html

WATCHLIST = {
    "AAPL":  "Apple",
    "MSFT":  "Microsoft",
    "GOOGL": "Alphabet",
    "NVDA":  "NVIDIA",
    "AMD":   "AMD",
    "INTC":  "Intel",
    "MU":    "Micron",
    "TSM":   "台積電 ADR",
    "GEV":   "GE Vernova",
    "ASML":  "ASML",
    "ARM":   "Arm",
    "AVGO":  "Broadcom",
}


def _looks_like_ticker(s):
    s = s.strip()
    if not (1 <= len(s) <= 6):
        return False
    core = s.replace(".", "").replace("-", "")
    return core.isascii() and core.isalnum() and any(c.isalpha() for c in core)


def load_watchlist():
    """
    若同資料夾有 watchlist.txt 就改用它，否則用內建 WATCHLIST。
    格式：一行一檔，代號在前；名稱可用逗號或空白接在後面；# 開頭為註解。
    範例：
        AAPL
        MSFT, 微軟
        TSM 台積電 ADR
    """
    path = os.path.join(_HERE, "watchlist.txt")
    if not os.path.exists(path):
        return WATCHLIST
    parsed = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "," in line:
                    code, _, name = line.partition(",")
                    code = code.strip().upper()
                    name = name.strip()
                    if code:
                        parsed[code] = name or code
                    continue
                tokens = line.split()
                if not tokens:
                    continue
                if len(tokens) == 1:
                    t = tokens[0].upper()
                    parsed[t] = t
                elif all(_looks_like_ticker(x) for x in tokens):
                    for tok in tokens:
                        t = tok.upper()
                        parsed[t] = t
                else:
                    t = tokens[0].upper()
                    parsed[t] = " ".join(tokens[1:])
    except Exception as e:
        print(f"  讀取 watchlist.txt 失敗，改用內建清單：{e}")
        return WATCHLIST
    if not parsed:
        return WATCHLIST
    print(f"  已從 watchlist.txt 載入 {len(parsed)} 檔")
    return parsed

MACRO = {
    "^TNX":     ("10年期公債殖利率", "%"),
    "^TYX":     ("30年期公債殖利率", "%"),
    "DX-Y.NYB": ("美元指數 DXY", ""),
    "JPY=X":    ("美元 / 日圓", ""),
    "GC=F":     ("黃金 期貨", "$"),
    "SI=F":     ("白銀 期貨", "$"),
    "CL=F":     ("西德州原油", "$"),
    "^VIX":     ("VIX 波動率", ""),
    # ── 指數（點位）──
    "^SOX":     ("費城半導體 指數", ""),
    "^GSPC":    ("標普500 指數", ""),
    "^NDX":     ("那斯達克100 指數", ""),
    # ── ETF（股價，不是指數點位）──
    # 註：SPY / RSP 同時供「市場廣度」計算使用，勿刪
    "QQQ":      ("QQQ · 那指100 ETF", "$"),
    "SPY":      ("SPY · 標普500 ETF", "$"),
    "RSP":      ("RSP · 標普500 等權重 ETF", "$"),
}

# 貴金屬持倉（獨立區塊）
PRECIOUS_METALS = {
    "GLDM":  "黃金 ETF MiniShares",
    "SIVR":  "白銀 ETF 實物",
}

ALERTS = {
    "tnx_high":      5.00,   # 10Y 站上 5% → 企業投資決策受驚擾
    "tyx_high":      5.27,   # 30Y 突破近 20 年高點
    "tnx_low":       4.00,   # 10Y 跌破 4% → 降息預期明確
    "dxy_high":     102.0,
    "dxy_low":       96.0,
    "jpy_high":     160.0,   # 干預區間
    "jpy_low":      155.0,   # 套利平倉風險
    "vix_high":      25.0,
    "vix_low":       15.0,   # 過度平靜。2026/08 由 14.0 上調：
                             # 8/14 消費崩、油價漲、AI 融資疑慮、30Y 重測高點，
                             # VIX 仍在 14.25 → 14.0 的門檻抓不到這種矛盾
    "ma200_dev":      2.0,   # 乖離在 ±2% 內 → 年線觀察區
    "ma200_stretch": 25.0,
    # 貴金屬
    "gold_silver_high": 80.0,  # 金銀比偏高 → 白銀相對低估
    "gold_silver_low":  50.0,  # 金銀比偏低 → 白銀追漲
    # ── 循環頂點嫌疑：低本益比 + 高乖離同時成立 ──
    # 記憶體、能源等循環股在獲利頂點時 E 被推到極大 → PE 看似極便宜。
    # 這不是價值訊號，是 E 即將反轉的訊號。
    "cyclical_pe":   10.0,   # trailing PE 低於此
    "cyclical_dev":  40.0,   # 且高於年線此幅度 → 觸發
}

# ── 現金／超短債型 ETF ────────────────────────────────────────
# 淨值近乎不動，年線乖離沒有意義。完全排除於警示之外，
# 否則會持續產生「貼近年線」的假訊號（SGOV 就是典型案例）。
CASH_LIKE = {
    "SGOV", "BIL", "SHV", "USFR", "TFLO", "ICSH", "JPST",
    "MINT", "GBIL", "XHLF", "CLIP", "SHY", "VUSB", "STIP",
}

# ── 槓桿／波動率／期貨展期型 ETF ──────────────────────────────
# 價格含結構性耗損（每日重設複利 + contango 展期成本），
# 長期乖離必然為負，與方向無關 → 不產生乖離類警示。
# UVXY 的 -47%、TMF 的 -15%、UNG 的 -17% 多半來自耗損，不是趨勢。
DECAY_ETFS = {
    "UVXY", "VXX", "VIXY", "SVXY", "UVIX",
    "TMF", "TMV", "TYD", "TYO",
    "TQQQ", "SQQQ", "UPRO", "SPXU", "UDOW", "SDOW", "TNA", "TZA",
    "SOXL", "SOXS", "LABU", "LABD", "FAS", "FAZ", "YINN", "YANG",
    "UNG", "BOIL", "KOLD", "USO", "UCO", "SCO", "NUGT", "DUST",
    "JNUG", "JDST", "AGQ", "ZSL", "UGL", "GLL",
}


def ticker_kind(t):
    """回傳 'cash' / 'decay' / 'normal'，決定警示與表格呈現方式。"""
    t = (t or "").upper()
    if t in CASH_LIKE:
        return "cash"
    if t in DECAY_ETFS:
        return "decay"
    return "normal"

# ═════════════════════════════════════════════════════════════


def _rsi(close, period=14):
    """Wilder RSI。回傳最後一個值，資料不足回 None。"""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder 平滑（等同 EMA alpha=1/period）
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - 100 / (1 + rs)
    v = rsi.iloc[-1]
    return float(v) if pd.notna(v) else None


def _macd(close, fast=12, slow=26, signal=9):
    """MACD。回傳 (macd, signal, hist, 前一根 hist)，判斷交叉用。"""
    if len(close) < slow + signal:
        return None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    if len(hist) < 2:
        return None
    return {
        "macd": float(macd_line.iloc[-1]),
        "signal": float(signal_line.iloc[-1]),
        "hist": float(hist.iloc[-1]),
        "hist_prev": float(hist.iloc[-2]),
    }


def _stochastic(high, low, close, k_period=14, d_period=3, smooth=3):
    """慢速隨機指標。回傳 %K、%D 及前幾根，用於判斷「跌破20後回升」。"""
    if len(close) < k_period + d_period + smooth:
        return None
    lowest = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    rng = (highest - lowest).replace(0, float("nan"))
    fast_k = 100 * (close - lowest) / rng
    slow_k = fast_k.rolling(smooth).mean()        # 慢速 %K
    slow_d = slow_k.rolling(d_period).mean()      # %D
    k = slow_k.dropna()
    d = slow_d.dropna()
    if len(k) < 3 or len(d) < 2:
        return None
    return {
        "k": float(k.iloc[-1]),
        "k_prev": float(k.iloc[-2]),
        "k_prev2": float(k.iloc[-3]),
        "d": float(d.iloc[-1]),
        "d_prev": float(d.iloc[-2]),
        "k_min_recent": float(k.tail(5).min()),   # 近 5 根最低，判斷是否曾超賣
    }


def compute_technicals(df):
    """從 OHLC DataFrame 算出所有技術指標與訊號旗標。"""
    close = df["Close"].dropna()
    high = df["High"].dropna() if "High" in df else close
    low = df["Low"].dropna() if "Low" in df else close

    rsi14 = _rsi(close, 14)
    rsi63 = _rsi(close, 63)                        # 中期（約一季）
    macd = _macd(close)
    stoch = _stochastic(high, low, close)

    signals = []

    # ── Stochastic：跌破 20 後開始回升 ──
    if stoch:
        k, kp, kp2 = stoch["k"], stoch["k_prev"], stoch["k_prev2"]
        was_oversold = stoch["k_min_recent"] <= 20   # 近期曾進超賣
        turning_up = k > kp and kp <= kp2 + 0.01     # 由下彎轉上
        k_cross_d = k > stoch["d"] and kp <= stoch["d_prev"]   # %K 上穿 %D
        if was_oversold and k < 40 and (turning_up or k_cross_d):
            signals.append(("stoch_rebound",
                            f"Stochastic 自超賣回升（%K {k:.0f}）"))

    # ── MACD 交叉 ──
    if macd:
        h, hp = macd["hist"], macd["hist_prev"]
        if hp <= 0 < h:
            signals.append(("macd_gold", "MACD 黃金交叉（柱狀翻正）"))
        elif hp >= 0 > h:
            signals.append(("macd_death", "MACD 死亡交叉（柱狀翻負）"))

    # ── 季 RSI（63日）動能 ──
    if rsi63 is not None:
        if rsi63 < 30:
            signals.append(("rsi63_low", f"季RSI {rsi63:.0f} 中期超賣"))
        elif rsi63 > 70:
            signals.append(("rsi63_high", f"季RSI {rsi63:.0f} 中期超買"))

    return {
        "rsi14": rsi14,
        "rsi63": rsi63,
        "macd": macd,
        "stoch": stoch,
        "signals": signals,
    }


def fetch_market_data(tickers):
    """抓取報價與 200 日均線。相容 yfinance 0.2.x 與 1.5.x。"""
    out = {}
    tickers = list(tickers)
    try:
        data = yf.download(
            tickers, period="1y", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker",
            threads=True,
        )
    except Exception as e:
        print(f"  資料抓取失敗: {e}")
        return out

    if data is None or len(data) == 0:
        return out

    for t in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if t in data.columns.get_level_values(0):
                    df = data[t]
                else:
                    continue
            else:
                df = data
            close = df["Close"].dropna()
            if len(close) < 2:
                continue
            last, prev = float(close.iloc[-1]), float(close.iloc[-2])
            ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

            # 52 週高低：優先用「盤中」High/Low，資料缺漏才退回收盤價。
            # 舊版用 close.max()，那是「近一年最高收盤」，會低估實際跌幅
            # （例：SIVR 距高點 -44.6% 是低估值）。
            try:
                hi_s = df["High"].dropna() if "High" in df else close
                lo_s = df["Low"].dropna() if "Low" in df else close
                high_52w = float(hi_s.max()) if len(hi_s) else float(close.max())
                low_52w = float(lo_s.min()) if len(lo_s) else float(close.min())
            except Exception:
                high_52w, low_52w = float(close.max()), float(close.min())

            out[t] = {
                "price": last,
                "chg_pct": (last - prev) / prev * 100 if prev else 0.0,
                "ma200": ma200,
                "ma200_dev": ((last - ma200) / ma200 * 100) if ma200 else None,
                "high_52w": high_52w,
                "low_52w": low_52w,
                "spark": [float(x) for x in close.tail(60)],
            }
            try:
                out[t]["tech"] = compute_technicals(df)
            except Exception:
                out[t]["tech"] = None
        except Exception:
            continue
    return out


def _clean_pe(v):
    """濾掉 None、負值、字串與離譜極端值（yfinance 偶爾回傳異常數）。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0 or v > 2000:
        return None
    return v


def fetch_fundamentals(tickers):
    """
    抓取本益比並計算盈餘殖利率。

    ── 2026-08 修正 ──────────────────────────────────────────
    舊版：pe = info.get("forwardPE") or info.get("trailingPE")   ← 優先 forward
    造成三個問題：
      1. 重力線註解宣稱「未計入成長」，但 forward PE 已內含一年分析師成長預期
         → 所有股票看起來比實際便宜
      2. 有 forward 的用 forward、沒有的 fallback 到 trailing
         → 同一張圖拿蘋果比橘子（AVGO forward 20 倍 vs trailing 66 倍）
      3. 對循環股最危險：分析師預估在循環見頂時永遠最後才下修
         → 等於用最樂觀的分母，去衡量最接近頂點的資產

    現在：trailing 為主軸（重力線與盈餘殖利率一律用 trailing），
          forward 僅作對照保留，並標示每一檔實際使用的基準。
    ──────────────────────────────────────────────────────────
    """
    out = {}
    for t in tickers:
        trail = fwd = None
        try:
            info = yf.Ticker(t).info
            trail = _clean_pe(info.get("trailingPE"))
            fwd = _clean_pe(info.get("forwardPE"))
        except Exception:
            pass

        # 主軸 trailing；完全沒有 trailing 時才退回 forward，並明確標記
        if trail:
            pe, basis = trail, "T"
        elif fwd:
            pe, basis = fwd, "F"
        else:
            pe, basis = None, None

        out[t] = {
            "pe": pe,
            "pe_basis": basis,            # "T"=trailing, "F"=forward(替代)
            "pe_trailing": trail,
            "pe_forward": fwd,
            "earnings_yield": (100 / pe) if pe else None,
            "ey_forward": (100 / fwd) if fwd else None,
        }

    _audit_pe(out)
    return out


# ── 本益比合理性自動校驗 ──────────────────────────────────────
# 取代「第一次跑完請用眼睛核對」——把檢查寫成程式，每次都會跑。
PE_AUDIT = {
    "gap_ratio":   3.0,    # trailing / forward 相差達此倍數 → 兩者對未來看法嚴重分歧
    "absurd_high": 300.0,  # 高到這個程度多半是 E 趨近於零，倒數出來的殖利率沒有意義
    "absurd_low":    3.0,  # 低到這個程度多半是一次性利得灌大 E
}


def _audit_pe(funda):
    """
    對抓回來的本益比做四項自動檢查，結果寫回 funda[t]["audit"]，
    並在 console 印出需要人工確認的清單。

      divergent — trailing 與 forward 差距 ≥3 倍。分析師預期與已實現盈餘嚴重分歧，
                  循環股見頂/落底時的典型特徵，重力線位置最不可信。
      forward   — 沒有 trailing，只能用 forward 頂替（已內含成長預期）。
      extreme   — 數值離譜（>300 或 <3），倒數出來的盈餘殖利率意義薄弱。
      missing   — 兩種都沒抓到。
    """
    flagged = []
    for t, f in funda.items():
        tr, fw, pe = f.get("pe_trailing"), f.get("pe_forward"), f.get("pe")
        tags = []
        if pe is None:
            tags.append("missing")
        else:
            if tr and fw:
                ratio = max(tr / fw, fw / tr)
                if ratio >= PE_AUDIT["gap_ratio"]:
                    tags.append("divergent")
            if f.get("pe_basis") == "F":
                tags.append("forward")
            if pe >= PE_AUDIT["absurd_high"] or pe <= PE_AUDIT["absurd_low"]:
                tags.append("extreme")
        f["audit"] = tags
        if tags:
            flagged.append((t, tags, tr, fw))

    if not flagged:
        print("  本益比校驗：全部通過")
        return

    LABEL = {"divergent": "T/F 分歧", "forward": "僅 forward",
             "extreme": "數值極端", "missing": "無資料"}
    print(f"  本益比校驗：{len(flagged)} 檔需人工確認")
    for t, tags, tr, fw in flagged:
        tr_s = f"{tr:.1f}" if tr else "—"
        fw_s = f"{fw:.1f}" if fw else "—"
        print(f"    {t:<6} trailing {tr_s:>7} / forward {fw_s:>7}   "
              f"[{'、'.join(LABEL[x] for x in tags)}]")
    print("    ↑ 這些檔在重力線上的位置請自行判斷，勿直接採信")


def fetch_fred(series_id, key):
    if not key:
        return None
    import requests
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": key,
                    "file_type": "json", "sort_order": "desc", "limit": 1},
            timeout=10,
        )
        obs = r.json().get("observations", [])
        return {"date": obs[0]["date"], "value": float(obs[0]["value"])} if obs else None
    except Exception:
        return None


def check_alerts(macro, stocks, funda=None):
    """回傳 [(等級, 標題, 說明)]。等級: critical / watch / calm"""
    funda = funda or {}
    out = []
    g = lambda t: macro.get(t, {}).get("price")
    tnx, tyx, dxy, jpy, vix = g("^TNX"), g("^TYX"), g("DX-Y.NYB"), g("JPY=X"), g("^VIX")

    if tnx:
        if tnx >= ALERTS["tnx_high"]:
            out.append(("critical", f"10Y 殖利率 {tnx:.2f}%",
                        f"站上 {ALERTS['tnx_high']}%，企業投資決策開始受驚擾"))
        elif tnx <= ALERTS["tnx_low"]:
            out.append(("calm", f"10Y 殖利率 {tnx:.2f}%",
                        f"跌破 {ALERTS['tnx_low']}%，降息預期升溫"))
    if tyx and tyx >= ALERTS["tyx_high"]:
        out.append(("critical", f"30Y 殖利率 {tyx:.2f}%",
                    "長端供給壓力，高估值資產折現率警戒"))
    if dxy:
        if dxy >= ALERTS["dxy_high"]:
            out.append(("watch", f"美元指數 {dxy:.2f}", "突破區間上緣，美元轉強"))
        elif dxy <= ALERTS["dxy_low"]:
            out.append(("watch", f"美元指數 {dxy:.2f}", "跌破區間下緣，美元轉弱"))
    if jpy:
        if jpy >= ALERTS["jpy_high"]:
            out.append(("critical", f"美元/日圓 {jpy:.2f}", "進入日本央行干預區間"))
        elif jpy <= ALERTS["jpy_low"]:
            out.append(("critical", f"美元/日圓 {jpy:.2f}",
                        "套利交易平倉風險，留意全球流動性收縮"))
    if vix:
        if vix >= ALERTS["vix_high"]:
            out.append(("critical", f"VIX {vix:.2f}", "市場轉為緊張"))
        elif vix <= ALERTS["vix_low"]:
            out.append(("watch", f"VIX {vix:.2f}", "過度平靜，低波動配高槓桿是危險組合"))

    # 金銀比
    gold = g("GC=F")
    silver = g("SI=F")
    if gold and silver and silver > 0:
        gs = gold / silver
        if gs >= ALERTS["gold_silver_high"]:
            out.append(("watch", f"金銀比 {gs:.1f}",
                        f"≥{ALERTS['gold_silver_high']:.0f}，白銀相對低估或避險情緒極端"))
        elif gs <= ALERTS["gold_silver_low"]:
            out.append(("watch", f"金銀比 {gs:.1f}",
                        f"≤{ALERTS['gold_silver_low']:.0f}，白銀追漲或工業需求強勁"))

    for t, d in stocks.items():
        kind = ticker_kind(t)

        # 現金／超短債型：淨值不動，年線與技術指標全無意義，整檔跳過
        if kind == "cash":
            continue

        dev = d.get("ma200_dev")
        tech = d.get("tech") or {}

        # ── 循環頂點嫌疑：trailing PE 極低 + 乖離極大 ──
        f = funda.get(t, {})
        pe_t = f.get("pe_trailing")
        if (kind == "normal" and pe_t and dev is not None
                and pe_t <= ALERTS["cyclical_pe"]
                and dev >= ALERTS["cyclical_dev"]):
            out.append((
                "critical", f"⚑ {t} 循環頂點嫌疑",
                f"本益比僅 {pe_t:.1f} 倍卻高於年線 {dev:+.1f}%——"
                f"低本益比來自暴衝的 E，不是便宜的 P"
            ))

        # ── 乖離類警示 ──
        # 槓桿／展期耗損型跳過：長期乖離來自結構性耗損，不是方向訊號
        if kind != "decay" and dev is not None:
            if abs(dev) <= ALERTS["ma200_dev"]:
                out.append(("watch", f"{t} 貼近年線", f"乖離 {dev:+.1f}%，落在年線觀察區"))
            elif dev <= -5:
                out.append(("watch", f"{t} 跌破年線", f"乖離 {dev:+.1f}%"))
            elif dev >= ALERTS["ma200_stretch"]:
                out.append(("watch", f"{t} 乖離偏大", f"高於年線 {dev:+.1f}%"))

        # ── 技術訊號 ──
        # 耗損型也保留：這類商品本來就用於短期方向性交易，
        # MACD／Stochastic 的交叉仍有意義，被污染的只有「乖離」。
        for sig_type, msg in tech.get("signals", []):
            lvl = {
                "stoch_rebound": "calm",
                "macd_gold": "calm",
                "macd_death": "critical",
                "rsi63_low": "watch",
                "rsi63_high": "watch",
            }.get(sig_type, "watch")
            note = "（槓桿商品，僅供短線參考）" if kind == "decay" else ""
            out.append((lvl, f"{t}", msg + note))
    return out


# ═════════════════════════════════════════════════════════════
# HTML
# ═════════════════════════════════════════════════════════════

CSS = """
:root{
  --paper:#EDF0F3; --sheet:#FFFFFF; --ink:#15202B; --ink-2:#5A6B7C;
  --rule:#C9D3DC; --grid:rgba(21,32,43,.045);
  --up:#1B6B4A; --down:#A5301E; --warn:#B0771A; --line:#2B4C6F;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'IBM Plex Sans','Noto Sans TC',system-ui,sans-serif;
  background:var(--paper); color:var(--ink);
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),
                   linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:28px 28px;
  padding:28px 20px 64px; line-height:1.5;
}
.wrap{max-width:1120px;margin:0 auto}
.num{font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
.eyebrow{
  font-family:'IBM Plex Sans Condensed','Noto Sans TC',sans-serif;
  font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink-2);font-weight:600;
}

/* header */
header{
  display:flex;justify-content:space-between;align-items:flex-end;
  gap:16px;flex-wrap:wrap;
  border-bottom:2px solid var(--ink);padding-bottom:12px;margin-bottom:24px;
}
h1{
  font-family:'IBM Plex Sans Condensed','Noto Sans TC',sans-serif;
  font-size:30px;font-weight:600;letter-spacing:-.01em;line-height:1;
}
h1 span{color:var(--ink-2);font-weight:400}
.stamp{text-align:right}
.stamp .t{font-size:13px}

/* sections */
section{margin-bottom:30px}
.shead{display:flex;align-items:baseline;gap:12px;margin-bottom:12px}
.shead::after{content:"";flex:1;height:1px;background:var(--rule)}

/* macro cards */
.macro{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:1px;
  background:var(--rule);border:1px solid var(--rule)}
.cell{background:var(--sheet);padding:12px 14px}
.cell .lbl{font-size:11.5px;color:var(--ink-2);margin-bottom:5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell .val{font-size:21px;font-weight:500;letter-spacing:-.02em}
.cell .chg{font-size:12.5px;margin-top:2px}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--ink-2)}

/* gravity line */
.gravity{background:var(--sheet);border:1px solid var(--rule);padding:22px 22px 14px;position:relative}
.gwrap{position:relative;height:210px;margin:26px 0 6px}
.gaxis{position:absolute;left:0;right:0;height:1px;background:var(--rule)}
.gline{position:absolute;left:0;right:0;height:0;
  border-top:2px dashed var(--line);z-index:2}
.gline .tag{
  position:absolute;right:0;top:-9px;background:var(--line);color:#fff;
  font-size:10.5px;padding:2px 7px;letter-spacing:.04em;white-space:nowrap;
}
.gdot{position:absolute;transform:translate(-50%,50%);z-index:3;text-align:center}
.gdot i{display:block;width:9px;height:9px;border-radius:50%;margin:0 auto 4px}
.gdot b{font-size:10.5px;font-weight:600;display:block;white-space:nowrap}
.gdot em{font-size:9.5px;font-style:normal;color:var(--ink-2);display:block}
.gnote{font-size:12px;color:var(--ink-2);border-top:1px solid var(--rule);padding-top:10px}
.gzone{position:absolute;left:0;right:0;bottom:0;background:rgba(165,48,30,.045);z-index:1}

/* table */
table{width:100%;border-collapse:collapse;background:var(--sheet);
  border:1px solid var(--rule);font-size:13.5px}
th{font-family:'IBM Plex Sans Condensed','Noto Sans TC',sans-serif;
  font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-2);
  text-align:right;padding:9px 12px;border-bottom:1px solid var(--rule);font-weight:600}
th:first-child,th:nth-child(2){text-align:left}
td{padding:9px 12px;border-bottom:1px solid #EDF1F4;text-align:right}
td:first-child,td:nth-child(2){text-align:left}
tr:last-child td{border-bottom:0}
tr:hover td{background:#F7F9FB}
.tick{font-weight:600;letter-spacing:.02em}
.name{color:var(--ink-2);font-size:12.5px}
.devbar{display:inline-block;vertical-align:middle;height:4px;margin-left:7px;
  min-width:2px;border-radius:1px}
.pill{display:inline-block;padding:1px 6px;font-size:11px;border:1px solid;border-radius:2px}
.pill.at{border-color:var(--warn);color:var(--warn)}
.pill.na{border-color:var(--rule);color:var(--ink-2);opacity:.85}
.pill.cyc{border-color:var(--down);color:var(--down);font-weight:600}
.pill.aud{border-color:var(--warn);color:var(--warn);font-size:10px;padding:0 4px;margin-left:4px}
.tech-col{text-align:center}
sup.basis{font-size:9px;letter-spacing:.04em;color:var(--ink-2);margin-left:2px}
sup.basis.warnb{color:var(--warn);font-weight:600}

/* alerts */
.alerts{display:grid;gap:1px;background:var(--rule);border:1px solid var(--rule)}
.al{background:var(--sheet);padding:11px 14px;display:flex;gap:12px;align-items:baseline}
.al .bar{width:3px;align-self:stretch;flex:0 0 3px}
.al.critical .bar{background:var(--down)}
.al.watch .bar{background:var(--warn)}
.al.calm .bar{background:var(--up)}
.al .h{font-weight:600;font-size:13.5px;white-space:nowrap}
.al .d{font-size:12.5px;color:var(--ink-2)}
.empty{background:var(--sheet);border:1px solid var(--rule);padding:16px;
  font-size:13px;color:var(--ink-2)}

/* breadth */
.breadth{background:var(--sheet);border:1px solid var(--rule);padding:14px 16px;
  display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
.breadth .v{font-size:19px;font-weight:500}

footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--rule);
  font-size:11.5px;color:var(--ink-2);line-height:1.8}

@media (max-width:640px){
  body{padding:18px 12px 48px}
  h1{font-size:23px}
  .name,.pe-col,.tech-col{display:none}
  .gwrap{height:250px}
}
@media (prefers-reduced-motion:no-preference){
  .cell,.al{animation:fade .4s ease both}
}
@keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
"""


def _cls(v):
    return "up" if v > 0.05 else "down" if v < -0.05 else "flat"


def _fmt(v, unit):
    if unit == "%":
        return f"{v:.2f}%"
    if unit == "$":
        return f"{v:,.2f}"
    return f"{v:,.2f}"


def build_html(macro, stocks, funda, fred_data, refresh=0, pm_data=None, polymarket_data=None):
    pm_data = pm_data or {}
    polymarket_data = polymarket_data or {}
    tyx = macro.get("^TYX", {}).get("price")
    now = datetime.now()

    meta_refresh = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""

    # ── macro cells
    cells = []
    for t, (label, unit) in MACRO.items():
        d = macro.get(t)
        if not d:
            continue
        c = _cls(d["chg_pct"])
        cells.append(
            f'<div class="cell"><div class="lbl">{label}</div>'
            f'<div class="val num">{_fmt(d["price"], unit)}</div>'
            f'<div class="chg num {c}">{d["chg_pct"]:+.2f}%</div></div>'
        )
    macro_html = f'<div class="macro">{"".join(cells)}</div>' if cells else \
        '<div class="empty">未取得總經資料</div>'

    # ── gravity line（signature）
    pts = []
    n_fwd = 0
    for t, d in stocks.items():
        f = funda.get(t, {})
        ey = f.get("earnings_yield")
        if ey is not None:
            basis = f.get("pe_basis") or "T"
            if basis == "F":
                n_fwd += 1
            pts.append((t, ey, basis))
    pts.sort(key=lambda x: x[1])

    if pts and tyx:
        ys = [p[1] for p in pts] + [tyx]
        lo, hi = min(ys), max(ys)
        pad = max((hi - lo) * 0.22, 0.6)
        lo, hi = max(lo - pad, 0), hi + pad
        rng = hi - lo or 1

        def ypct(v):
            return (v - lo) / rng * 100

        line_y = ypct(tyx)
        dots = []
        n = len(pts)
        for i, (t, ey, basis) in enumerate(pts):
            x = (i + 0.5) / n * 100
            y = ypct(ey)
            below = ey < tyx
            col = "var(--down)" if below else "var(--up)"
            mark = "*" if basis == "F" else ""
            dots.append(
                f'<div class="gdot" style="left:{x:.2f}%;bottom:{y:.2f}%">'
                f'<i style="background:{col}"></i>'
                f'<b>{t}{mark}</b><em class="num">{ey:.1f}%</em></div>'
            )

        fwd_note = (f'　<b>*</b> 共 {n_fwd} 檔無 trailing 資料，改用 forward'
                    f'（已內含成長預期，會顯得較便宜）。' if n_fwd else "")
        gravity = f"""
<div class="gravity">
  <div class="eyebrow">重力線 · 盈餘殖利率 對 無風險利率</div>
  <div class="gwrap">
    <div class="gzone" style="height:{line_y:.2f}%"></div>
    <div class="gaxis" style="bottom:0"></div>
    <div class="gline" style="bottom:{line_y:.2f}%">
      <span class="tag num">30Y 公債 {tyx:.2f}%</span></div>
    {''.join(dots)}
  </div>
  <div class="gnote">線下方的持股，承擔股票風險卻拿不到公債的收益。<br>
  盈餘殖利率 = 1 ÷ <b>歷史（trailing）本益比</b>，以已實現盈餘計算，未計入未來成長。{fwd_note}<br>
  <b>反向陷阱：</b>線上方的極高盈餘殖利率若來自循環股（記憶體、能源）的頂點盈餘，
  代表的是 E 即將反轉，不是便宜。請對照警示區的「⚑ 循環頂點嫌疑」。</div>
</div>"""
    else:
        gravity = '<div class="empty">重力線需要 30 年期公債殖利率與本益比資料。' \
                  '若使用 --fast 會跳過本益比抓取。</div>'

    # ── holdings table
    rows = []
    for t, name in WATCHLIST.items():
        d = stocks.get(t)
        if not d:
            continue
        f = funda.get(t, {})
        chg = d["chg_pct"]
        dev = d.get("ma200_dev")

        kind = ticker_kind(t)

        if dev is None:
            dev_cell = '<span class="flat">—</span>'
        elif kind == "cash":
            dev_cell = ('<span class="flat">n/a</span>'
                        ' <span class="pill na" title="現金／超短債型，淨值近乎不動，'
                        '年線乖離無意義，已排除於警示外">現金型</span>')
        elif kind == "decay":
            dev_cell = (f'<span class="num flat">{dev:+.1f}%</span>'
                        ' <span class="pill na" title="槓桿／期貨展期商品，乖離含結構性耗損，'
                        '不代表方向，已排除於乖離警示外">耗損</span>')
        else:
            near = abs(dev) <= ALERTS["ma200_dev"]
            w = min(abs(dev) * 1.6, 56)
            col = "var(--up)" if dev > 0 else "var(--down)"
            tag = ' <span class="pill at">年線</span>' if near else ""
            dev_cell = (f'<span class="num {_cls(dev)}">{dev:+.1f}%</span>'
                        f'<span class="devbar" style="width:{w:.0f}px;background:{col}"></span>{tag}')

        # 本益比：主軸 trailing，右上角標基準，滑鼠移上顯示另一個基準
        pe = f.get("pe")
        basis = f.get("pe_basis")
        audit = f.get("audit") or []
        if pe:
            other = f.get("pe_forward") if basis == "T" else f.get("pe_trailing")
            other_lbl = "forward" if basis == "T" else "trailing"
            tip = f"{other_lbl} {other:.1f}" if other else f"無 {other_lbl} 資料"
            bcls = "basis" if basis == "T" else "basis warnb"
            audit_mark = ""
            if "divergent" in audit:
                tip += "｜T/F 差距 ≥3 倍，兩者對未來看法嚴重分歧"
                audit_mark = '<span class="pill aud" title="trailing 與 forward 差距 ≥3 倍">分歧</span>'
            elif "extreme" in audit:
                tip += "｜數值極端，倒數出的殖利率意義薄弱"
                audit_mark = '<span class="pill aud" title="本益比數值極端">極端</span>'
            pe_cell = (f'<span class="num" title="{tip}">{pe:.1f}</span>'
                       f'<sup class="{bcls}">{basis}</sup>{audit_mark}')
        else:
            pe_cell = '<span class="flat">—</span>'

        ey = f.get("earnings_yield")
        if ey is None:
            ey_cell = '<span class="flat">—</span>'
        else:
            below = tyx and ey < tyx
            pe_t = f.get("pe_trailing")
            cyc = (kind == "normal" and pe_t and dev is not None
                   and pe_t <= ALERTS["cyclical_pe"]
                   and dev >= ALERTS["cyclical_dev"])
            flag = (' <span class="pill cyc" title="低本益比 + 高乖離：'
                    '循環頂點嫌疑，高盈餘殖利率來自暴衝的 E">⚑</span>') if cyc else ""
            ey_cell = f'<span class="num {"down" if below else "up"}">{ey:.2f}%</span>{flag}'

        # ── 技術指標欄 ──
        tech = d.get("tech") or {}

        def _rsi_cell(v):
            if v is None:
                return '<span class="flat">—</span>'
            c = "down" if v > 70 else "up" if v < 30 else ""
            return f'<span class="num {c}">{v:.0f}</span>'

        r14_cell = _rsi_cell(tech.get("rsi14"))
        r63_cell = _rsi_cell(tech.get("rsi63"))

        macd = tech.get("macd")
        if macd:
            h = macd["hist"]
            macd_cell = (f'<span class="num {"up" if h > 0 else "down"}" '
                         f'title="柱狀 {h:+.2f}">{"▲" if h > 0 else "▼"}</span>')
        else:
            macd_cell = '<span class="flat">—</span>'

        stoch = tech.get("stoch")
        if stoch:
            k = stoch["k"]
            sc = "up" if k < 20 else "down" if k > 80 else ""
            stoch_cell = f'<span class="num {sc}" title="%D {stoch["d"]:.0f}">{k:.0f}</span>'
        else:
            stoch_cell = '<span class="flat">—</span>'

        rows.append(f"""<tr>
  <td class="tick">{t}</td><td class="name">{name}</td>
  <td class="num">{d["price"]:,.2f}</td>
  <td class="num {_cls(chg)}">{chg:+.2f}%</td>
  <td>{dev_cell}</td>
  <td class="tech-col">{r14_cell}</td>
  <td class="tech-col">{r63_cell}</td>
  <td class="tech-col">{stoch_cell}</td>
  <td class="tech-col">{macd_cell}</td>
  <td class="pe-col">{pe_cell}</td>
  <td>{ey_cell}</td></tr>""")

    table_html = f"""<table>
<thead><tr><th>代號</th><th>名稱</th><th>股價</th><th>日變動</th>
<th>年線乖離</th><th class="tech-col">RSI14</th><th class="tech-col">季RSI</th>
<th class="tech-col">Stoch</th><th class="tech-col">MACD</th>
<th class="pe-col">本益比 <sup class="basis">T</sup></th><th>盈餘殖利率</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>""" if rows else \
        '<div class="empty">未取得個股資料</div>'

    # ── precious metals
    pm_rows = []
    for t, name in PRECIOUS_METALS.items():
        d = pm_data.get(t)
        if not d:
            continue
        chg = d["chg_pct"]
        dev = d.get("ma200_dev")
        hi = d.get("high_52w")
        from_hi = ((d["price"] - hi) / hi * 100) if hi else None

        if dev is None:
            dev_cell = '<span class="flat">—</span>'
        else:
            w = min(abs(dev) * 1.6, 56)
            col = "var(--up)" if dev > 0 else "var(--down)"
            dev_cell = (f'<span class="num {_cls(dev)}">{dev:+.1f}%</span>'
                        f'<span class="devbar" style="width:{w:.0f}px;background:{col}"></span>')

        hi_cell = f'<span class="num">{hi:,.2f}</span>' if hi else '<span class="flat">—</span>'
        fh_cell = (f'<span class="num {_cls(from_hi)}">{from_hi:+.1f}%</span>'
                   if from_hi is not None else '<span class="flat">—</span>')

        pm_rows.append(f"""<tr>
  <td class="tick">{t}</td><td class="name">{name}</td>
  <td class="num">{d["price"]:,.2f}</td>
  <td class="num {_cls(chg)}">{chg:+.2f}%</td>
  <td>{dev_cell}</td>
  <td>{hi_cell}</td>
  <td>{fh_cell}</td></tr>""")

    # 金銀比
    gold_p = macro.get("GC=F", {}).get("price")
    silver_p = macro.get("SI=F", {}).get("price")
    gs_html = ""
    if gold_p and silver_p and silver_p > 0:
        gs = gold_p / silver_p
        if gs >= 80:
            gs_verdict = "白銀相對低估（歷史均值 ~60-65）"
            gs_cls = "down"
        elif gs >= 65:
            gs_verdict = "正常偏高區間"
            gs_cls = "flat"
        elif gs >= 50:
            gs_verdict = "正常偏低區間"
            gs_cls = "flat"
        else:
            gs_verdict = "白銀相對高估或工業需求極強"
            gs_cls = "up"
        gs_html = f"""<div style="margin-top:10px;padding:10px 14px;background:var(--sheet);
          border:1px solid var(--rule);display:flex;gap:18px;align-items:baseline;flex-wrap:wrap">
          <div><div class="eyebrow">金銀比</div>
          <div class="num" style="font-size:21px;font-weight:500">{gs:.1f}</div></div>
          <div style="font-size:13px;color:var(--ink-2)">{gs_verdict}。
          黃金 ${gold_p:,.0f} ÷ 白銀 ${silver_p:,.2f}</div></div>"""

    pm_html = ""
    if pm_rows:
        pm_html = f"""<section>
  <div class="shead"><span class="eyebrow">貴金屬持倉</span></div>
  <table>
  <thead><tr><th>代號</th><th>名稱</th><th>股價</th><th>日變動</th>
  <th>年線乖離</th><th>52W 高點 <sup class="basis">盤中</sup></th><th>距高點</th></tr></thead>
  <tbody>{''.join(pm_rows)}</tbody></table>
  {gs_html}
</section>"""

    # ── Polymarket
    poly_html = ""
    if polymarket_data:
        poly_items = []
        for category, markets in polymarket_data.items():
            mkt_rows = []
            for m in markets:
                q = m["question"]
                if len(q) > 65:
                    q = q[:62] + "…"
                prob = m.get("yes_prob")
                vol = m.get("volume", 0)
                if vol >= 1_000_000:
                    vol_s = f"${vol/1_000_000:.1f}M"
                elif vol >= 1_000:
                    vol_s = f"${vol/1_000:.0f}K"
                else:
                    vol_s = f"${vol:.0f}"
                if prob is not None:
                    p_cls = "up" if prob >= 60 else "down" if prob <= 30 else "flat"
                    prob_s = f'<span class="num {p_cls}">{prob:.0f}%</span>'
                else:
                    prob_s = '<span class="flat">—</span>'
                mkt_rows.append(
                    f'<tr><td style="text-align:left;font-size:12.5px">{q}</td>'
                    f'<td class="num">{prob_s}</td>'
                    f'<td class="num" style="color:var(--ink-2)">{vol_s}</td></tr>')
            if mkt_rows:
                poly_items.append(
                    f'<div style="margin-bottom:6px"><div class="eyebrow" style="margin:8px 0 4px">'
                    f'{category}</div><table style="font-size:13px"><thead><tr>'
                    f'<th style="text-align:left">賭盤</th><th>YES 機率</th><th>交易量</th></tr></thead>'
                    f'<tbody>{"".join(mkt_rows)}</tbody></table></div>')

        if poly_items:
            poly_html = f"""<section>
  <div class="shead"><span class="eyebrow">Polymarket 預測市場</span></div>
  <div style="background:var(--sheet);border:1px solid var(--rule);padding:14px">
  {''.join(poly_items)}
  <div style="font-size:11px;color:var(--ink-2);margin-top:10px;border-top:1px solid var(--rule);padding-top:8px">
  機率反映賭盤參與者共識，非事實預測。Polymarket 在美國無法交易，數據僅供研判。</div>
  </div></section>"""

    # ── alerts
    al = check_alerts(macro, stocks, funda)
    if al:
        order = {"critical": 0, "watch": 1, "calm": 2}
        al.sort(key=lambda x: order.get(x[0], 9))
        items = "".join(
            f'<div class="al {lv}"><span class="bar"></span>'
            f'<span class="h">{h}</span><span class="d">{d}</span></div>'
            for lv, h, d in al
        )
        alerts_html = f'<div class="alerts">{items}</div>'
    else:
        alerts_html = '<div class="empty">目前沒有指標觸及設定門檻。</div>'

    # ── breadth
    spy, rsp = macro.get("SPY"), macro.get("RSP")
    breadth_html = ""
    if spy and rsp:
        diff = rsp["chg_pct"] - spy["chg_pct"]
        verdict = "等權重領先，廣度健康" if diff > 0 else "權值股主導，廣度偏弱"
        breadth_html = f"""<section>
  <div class="shead"><span class="eyebrow">市場廣度</span></div>
  <div class="breadth">
    <div><div class="eyebrow">等權重減市值加權</div>
      <div class="v num {_cls(diff)}">{diff:+.2f}%</div></div>
    <div style="flex:1;min-width:220px;font-size:13px;color:var(--ink-2)">
      {verdict}。RSP {rsp["chg_pct"]:+.2f}% 對 SPY {spy["chg_pct"]:+.2f}%。
      等權重落後代表漲勢集中在少數權值股。</div>
  </div></section>"""

    # ── FRED
    fred_html = ""
    if fred_data and any(fred_data.values()):
        fc = "".join(
            f'<div class="cell"><div class="lbl">{k}</div>'
            f'<div class="val num">{v["value"]:,.2f}</div>'
            f'<div class="chg flat">{v["date"]}</div></div>'
            for k, v in fred_data.items() if v
        )
        fred_html = f"""<section>
  <div class="shead"><span class="eyebrow">總體經濟數據</span></div>
  <div class="macro">{fc}</div></section>"""

    refresh_note = f"　每 {refresh} 秒自動更新" if refresh else ""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{meta_refresh}
<title>市場監控儀表板</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Sans:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body><div class="wrap">

<header>
  <div>
    <div class="eyebrow" style="margin-bottom:5px">個人市場監控</div>
    <h1>儀表板 <span>/ Market Monitor</span></h1>
  </div>
  <div class="stamp">
    <div class="eyebrow">資料時間</div>
    <div class="t num">{now:%Y-%m-%d %H:%M:%S}</div>
    <div class="eyebrow" style="margin-top:3px">約 15 分鐘延遲{refresh_note}</div>
  </div>
</header>

<section>
  <div class="shead"><span class="eyebrow">門檻警示</span></div>
  {alerts_html}
</section>

<section>
  <div class="shead"><span class="eyebrow">總經與市場指標</span></div>
  {macro_html}
</section>

<section>
  <div class="shead"><span class="eyebrow">估值重力</span></div>
  {gravity}
</section>

<section>
  <div class="shead"><span class="eyebrow">觀察清單</span></div>
  {table_html}
</section>

{pm_html}

{breadth_html}
{fred_html}
{poly_html}

<footer>
  資料來源 yfinance，約 15 分鐘延遲，非即時報價。<br>
  本益比一律以 <b>trailing（歷史）</b>為主軸，標 <sup class="basis">T</sup>；
  無 trailing 才退回 forward，標 <sup class="basis warnb">F</sup>（已內含成長預期，會顯得較便宜）。
  滑鼠移到數字上可看另一個基準。<br>
  現金／超短債型 ETF 已排除於警示外；槓桿與期貨展期型 ETF 不產生乖離類警示（含結構性耗損），
  但保留 MACD／Stochastic 短線訊號。<br>
  每次抓取都會自動校驗本益比，需人工確認的檔位在 console 列出，表上標
  <span class="pill aud">分歧</span> / <span class="pill aud">極端</span>。<br>
  52 週高低點取自近一年<b>盤中</b> High／Low（非收盤價），已還原除權息。<br>
  警示為自訂觀察門檻，非買賣訊號。所有投資決策請自行判斷並自負風險。<br>
  門檻、ETF 分類集合與觀察清單可在 market_monitor_web.py 檔案上方的設定區修改。
</footer>

</div></body></html>"""


# ═════════════════════════════════════════════════════════════

def collect(with_funda=True, with_polymarket=False):
    global WATCHLIST
    WATCHLIST = load_watchlist()
    print("  抓取總經指標…")
    macro = fetch_market_data(list(MACRO))
    print("  抓取個股…")
    stocks = fetch_market_data(list(WATCHLIST))
    print("  抓取貴金屬持倉…")
    pm_data = fetch_market_data(list(PRECIOUS_METALS))
    funda = {}
    if with_funda:
        print("  抓取本益比（較慢，可用 --fast 跳過）…")
        funda = fetch_fundamentals(list(WATCHLIST))
    fred_data = {}
    if FRED_API_KEY:
        for name, sid in {"非農就業（千人）": "PAYEMS", "失業率 %": "UNRATE",
                          "CPI 指數": "CPIAUCSL", "勞動參與率 %": "CIVPART"}.items():
            fred_data[name] = fetch_fred(sid, FRED_API_KEY)
    polymarket_data = {}
    if with_polymarket:
        print("  抓取 Polymarket 賭盤…")
        try:
            from polymarket_monitor import fetch_all_relevant_markets
            result = fetch_all_relevant_markets(pages=3)
            if result:
                polymarket_data = result
                n = sum(len(v) for v in result.values())
                print(f"    命中 {n} 個賭盤，分佈於 {len(result)} 個分類")
            elif result is None:
                print("    Polymarket API 無法連線，跳過")
            else:
                print("    候選池中無符合關鍵字的賭盤")
        except ImportError:
            print("    polymarket_monitor.py 未找到，跳過")
        except Exception as e:
            print(f"    Polymarket 錯誤: {e}")
    return macro, stocks, funda, fred_data, pm_data, polymarket_data


def write_html(path, refresh=0, with_funda=True, with_polymarket=False):
    macro, stocks, funda, fred_data, pm_data, polymarket_data = collect(with_funda, with_polymarket)
    html = build_html(macro, stocks, funda, fred_data, refresh, pm_data, polymarket_data)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    n_pm = len(pm_data)
    n_poly = len(polymarket_data)
    print(f"  已更新 {path}  （總經 {len(macro)} / 個股 {len(stocks)} / 貴金屬 {n_pm} / Polymarket {n_poly} 類）")
    return path


def serve(port, interval, with_funda, with_polymarket=False):
    import http.server
    import socketserver
    import threading

    out = os.path.join(_HERE, "dashboard.html")
    write_html(out, refresh=interval, with_funda=with_funda, with_polymarket=with_polymarket)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=_HERE, **kw)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.path = "/dashboard.html"
            return super().do_GET()

        def log_message(self, *a):
            pass

    def refresher():
        while True:
            time.sleep(interval)
            try:
                write_html(out, refresh=interval, with_funda=with_funda,
                           with_polymarket=with_polymarket)
            except Exception as e:
                print(f"  更新失敗: {e}")

    threading.Thread(target=refresher, daemon=True).start()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        url = f"http://127.0.0.1:{port}/"
        print(f"\n  儀表板已啟動：{url}")
        print(f"  每 {interval} 秒重新抓取資料，按 Ctrl+C 結束\n")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止。")


def main():
    p = argparse.ArgumentParser(description="市場監控儀表板（網頁版）")
    p.add_argument("--serve", nargs="?", const=300, type=int, metavar="SEC",
                   help="啟動本機伺服器並自動更新，預設每 300 秒")
    p.add_argument("--port", type=int, default=7788, help="伺服器連接埠（預設 7788）")
    p.add_argument("--fast", action="store_true", help="跳過本益比抓取")
    p.add_argument("--polymarket", action="store_true",
                   help="同時顯示 Polymarket 預測市場賭盤（需 polymarket_monitor.py）")
    p.add_argument("--no-open", action="store_true", help="不自動開啟瀏覽器")
    p.add_argument("--out", default=None, help="輸出檔案路徑")
    args = p.parse_args()

    with_funda = not args.fast
    if args.fast:
        print("  --fast 模式：略過本益比，重力線圖將不會顯示")

    if args.serve:
        serve(args.port, args.serve, with_funda, args.polymarket)
    else:
        out = args.out or os.path.join(_HERE, "dashboard.html")
        write_html(out, refresh=0, with_funda=with_funda, with_polymarket=args.polymarket)
        if not args.no_open:
            webbrowser.open("file://" + os.path.abspath(out))


if __name__ == "__main__":
    main()
