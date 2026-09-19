#!/usr/bin/env python3
"""
market_monitor.py — 個人化市場監控儀表板

監控我們討論過的所有關鍵指標:
  1. 利率與折現率壓力 (10Y / 30Y 殖利率)
  2. 美元與日圓 (DXY, USD/JPY — 干預區間與套利平倉風險)
  3. 避險資產 (黃金、油價)
  4. 個股觀察清單 (含 200 日均線乖離、盈餘殖利率)
  5. 估值 vs 無風險利率 (盈餘殖利率 - 30Y 殖利率)
  6. 板塊廣度 (SPY vs RSP 等權重)

安裝:
    pip install yfinance pandas requests

執行:
    python market_monitor.py              # 執行一次
    python market_monitor.py --watch      # 每 5 分鐘更新
    python market_monitor.py --watch 60   # 每 60 秒更新
    python market_monitor.py --csv        # 同時寫入 CSV 記錄

注意:
  - 資料來源為直連 Yahoo v8 chart API（免 yfinance 報價端點，避免其 401 卡死），約有 15 分鐘延遲
  - 警示為「值得關注的門檻」,非買賣訊號
  - FRED 總經數據需免費 API key (見下方 FRED_API_KEY)
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Windows: 啟用 ANSI 顏色支援，否則會顯示成 ←[91m 之類的亂碼
if os.name == "nt":
    os.system("")

# ── SSL 憑證設定 ──────────────────────────────────────────────
# yfinance 1.5+ 使用 curl_cffi，在有公司代理／VPN／防毒 HTTPS 掃描的
# 環境下會出現 "unable to get local issuer certificate" 錯誤。
# 優先使用 fix_ssl.py 產生的 ca_bundle.pem，否則退回 certifi。
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
    import requests
except ImportError:
    sys.exit("請先安裝套件:  pip install yfinance pandas requests")


# ─────────────────────────────────────────────────────────────
# 設定區 — 依你的需求修改
# ─────────────────────────────────────────────────────────────

# FRED API key (免費申請: https://fred.stlouisfed.org/docs/api/api_key.html)
# 留空則跳過總經數據
FRED_API_KEY = ""

# 你的個股觀察清單（內建預設；若同資料夾有 watchlist.txt 會改用該檔）
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


def load_watchlist():
    """
    若同資料夾有 watchlist.txt，改用它，否則用上方內建 WATCHLIST。

    格式規則（一行一檔，簡單明確）：
        AAPL                 只有代號，名稱就用代號
        MSFT  微軟           代號後空白或 Tab 接名稱
        NVDA, NVIDIA         代號後逗號接名稱也可以
        # 井字號開頭是註解，整行忽略
        BRK.B                含點的代號沒問題

    從 stockanalysis 複製代號後，一行貼一個，存成 watchlist.txt 即可。
    （若一行貼了多個代號，會自動拆成多檔，但那一行就不能再指定名稱。）
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

                # 逗號有明確語意：代號, 名稱
                if "," in line:
                    code, _, name = line.partition(",")
                    code = code.strip().upper()
                    name = name.strip()
                    if code:
                        parsed[code] = name or code
                    continue

                # 沒有逗號：用空白切
                tokens = line.split()
                if not tokens:
                    continue

                if len(tokens) == 1:
                    t = tokens[0].upper()
                    parsed[t] = t
                elif all(_looks_like_ticker(x) for x in tokens):
                    # 整行都像代號 → 同一行多檔
                    for tok in tokens:
                        t = tok.upper()
                        parsed[t] = t
                else:
                    # 第一個是代號，其餘是名稱
                    t = tokens[0].upper()
                    parsed[t] = " ".join(tokens[1:])
    except Exception as e:
        print(f"  讀取 watchlist.txt 失敗，改用內建清單：{e}")
        return WATCHLIST

    if not parsed:
        print("  watchlist.txt 是空的，改用內建清單")
        return WATCHLIST

    print(f"  已從 watchlist.txt 載入 {len(parsed)} 檔")
    return parsed


def _looks_like_ticker(s):
    """判斷字串是否像美股代號：1–6 個英數字，可含點或連字號（如 BRK.B、RDS-A）。"""
    s = s.strip()
    if not (1 <= len(s) <= 6):
        return False
    core = s.replace(".", "").replace("-", "")
    return core.isascii() and core.isalnum() and any(c.isalpha() for c in core)

# 總經與市場指標
MACRO = {
    "^TNX":     "10年期公債殖利率",
    "^TYX":     "30年期公債殖利率",
    "DX-Y.NYB": "美元指數 DXY",
    "JPY=X":    "USD/JPY",
    "GC=F":     "黃金",
    "CL=F":     "西德州原油",
    "^VIX":     "VIX 波動率",
    "^SOX":     "費城半導體",
    "QQQ":      "那斯達克100",
    "SPY":      "標普500",
    "RSP":      "標普500等權重",
}

# 警示門檻 — 來自我們討論過的關鍵水位
ALERTS = {
    "tnx_high":      5.00,   # 10Y 站上 5% → 企業投資決策開始受驚擾
    "tyx_high":      5.27,   # 30Y 突破近 20 年高點
    "tnx_low":       4.00,   # 10Y 跌破 4% → 降息預期明確
    "dxy_high":     102.0,   # 美元指數區間上緣
    "dxy_low":       96.0,   # 美元指數區間下緣
    "jpy_high":     160.0,   # 日圓干預區間
    "jpy_low":      155.0,   # 套利平倉風險區 (流動性收縮)
    "vix_high":      25.0,   # 波動率轉為緊張
    "vix_low":       14.0,   # 過度平靜 (Dimon 的槓桿警告)
    "ma200_dev":     -2.0,   # 個股跌破年線 2% 以內 → 年線觀察區
    "ma200_stretch": 25.0,   # 個股高於年線 25% → 乖離過大
}

# ─────────────────────────────────────────────────────────────

C = {
    "red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m",
    "blue": "\033[94m", "gray": "\033[90m", "bold": "\033[1m",
    "end": "\033[0m",
}


def color(text, c):
    return f"{C.get(c, '')}{text}{C['end']}"


def pct_color(v):
    if v is None:
        return color("  n/a", "gray")
    s = f"{v:+6.2f}%"
    return color(s, "green" if v > 0 else "red" if v < 0 else "gray")


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
    """
    慢速隨機指標。回傳 dict 含 %K、%D，及前幾根用於判斷「跌破20後回升」。
    """
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


def _volume(df, window=20):
    """
    成交量異常分析。回傳 dict:
      ratio     — 當日量 / 過去 window 日均量（不含當日）
      rvol      — 相對成交量百分比（ratio × 100，習慣講法）
      avg       — 均量
      today     — 當日量
      price_chg — 當日漲跌%（用於判斷爆量的方向）
    資料不足或無 Volume 欄時回 None。
    """
    if "Volume" not in df:
        return None
    vol = df["Volume"].dropna()
    close = df["Close"].dropna()
    if len(vol) < window + 1 or len(close) < 2:
        return None
    today = float(vol.iloc[-1])
    # 過去 window 日均量（排除當日，避免自我稀釋）
    avg = float(vol.iloc[-(window + 1):-1].mean())
    if avg <= 0:
        return None
    ratio = today / avg
    price_chg = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    return {
        "ratio": ratio,
        "rvol": ratio * 100,
        "avg": avg,
        "today": today,
        "price_chg": price_chg,
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
    vol = _volume(df)

    signals = []

    # ── 成交量異常（注意力指標）──
    # 量本身沒方向，配合當日漲跌判斷「資金流入」還是「流出」
    if vol:
        r = vol["ratio"]
        pc = vol["price_chg"]
        if r >= 3.0:
            # 爆量（3 倍以上）
            if pc > 1:
                signals.append(("vol_surge_up",
                                f"爆量上漲 {r:.1f}倍量 +{pc:.1f}% — 資金大幅流入"))
            elif pc < -1:
                signals.append(("vol_surge_down",
                                f"爆量下跌 {r:.1f}倍量 {pc:.1f}% — 資金大幅流出"))
            else:
                signals.append(("vol_surge_flat",
                                f"爆量整理 {r:.1f}倍量 — 多空激烈換手"))
        elif r >= 2.0:
            # 放量（2–3 倍）
            direction = "偏多" if pc > 0 else "偏空" if pc < 0 else "持平"
            signals.append(("vol_high",
                            f"放量 {r:.1f}倍 {pc:+.1f}%（{direction}）"))

    # ── Stochastic：跌破 20 後開始回升 ──
    if stoch:
        k, kp, kp2 = stoch["k"], stoch["k_prev"], stoch["k_prev2"]
        was_oversold = stoch["k_min_recent"] <= 20   # 近期曾進超賣
        turning_up = k > kp and kp <= kp2 + 0.01      # 由下彎轉上（前一根是近期低點）
        k_cross_d = k > stoch["d"] and kp <= stoch["d_prev"]  # %K 上穿 %D
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

    # ── 原子旗標（供 check_alerts 組合成複合訊號）──
    flags = {
        "vol_surge_up": False,    # 爆量上漲
        "vol_surge_down": False,  # 爆量下跌
        "vol_surge_flat": False,  # 爆量整理
        "stoch_rebound": False,   # Stochastic 自超賣回升
        "macd_death": False,      # MACD 死亡交叉
        "macd_gold": False,       # MACD 黃金交叉
    }
    if vol:
        r, pc = vol["ratio"], vol["price_chg"]
        if r >= 3.0:
            if pc > 1:
                flags["vol_surge_up"] = True
            elif pc < -1:
                flags["vol_surge_down"] = True
            else:
                flags["vol_surge_flat"] = True
    if stoch:
        k, kp, kp2 = stoch["k"], stoch["k_prev"], stoch["k_prev2"]
        if stoch["k_min_recent"] <= 20 and k < 40 and (
                (k > kp and kp <= kp2 + 0.01) or
                (k > stoch["d"] and kp <= stoch["d_prev"])):
            flags["stoch_rebound"] = True
    if macd:
        h, hp = macd["hist"], macd["hist_prev"]
        if hp <= 0 < h:
            flags["macd_gold"] = True
        elif hp >= 0 > h:
            flags["macd_death"] = True

    return {
        "rsi14": rsi14,
        "rsi63": rsi63,
        "macd": macd,
        "stoch": stoch,
        "vol": vol,
        "flags": flags,
        "signals": signals,
    }


# ── Yahoo Finance 資料層（直連 chart API，取代會卡住的 yfinance）────────────────
# 背景：2026-09 起 Yahoo 的 v7/finance/quote 與 v10/quoteSummary 對未授權請求回
# 401（需要 crumb cookie），yfinance 靠這兩個端點，於是在沙盒/一般環境下掛住 150s+
# 才回空。但 v8/finance/chart 端點仍可匿名取值（回 OHLCV + regularMarketPrice），
# 所以這裡改直連 chart，並加 fail-fast 逾時，不再讓引擎乾等。
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}
HTTP_TIMEOUT = float(os.environ.get("YAHOO_TIMEOUT", "10"))  # 每次 request 秒數
MAX_WORKERS = int(os.environ.get("YAHOO_WORKERS", "8"))       # 併發抓取數
FUNDA_TIMEOUT = float(os.environ.get("YAHOO_FUNDA_TIMEOUT", "3"))  # 每檔本益比逾時秒數


def _yahoo_chart(sym, rng="1y", interval="1d"):
    """直連 Yahoo v8 chart 端點，回傳 (DataFrame, meta) 或 (None, 錯誤字串)。

    DataFrame 含 Open/High/Low/Close/Volume，index 為時區感知 timestamp，
    與 compute_technicals 期望一致。meta 為原始 dict（含 regularMarketPrice）。
    失敗一律在 HTTP_TIMEOUT 內回傳，不 block。429/5xx 時快速重試一次，
    降低併發抓取時 Yahoo 偶發 rate-limit 造成的漏檔。
    """
    url = _YAHOO_CHART_URL.format(sym=sym)
    last_err = "no attempt"
    for attempt in (0, 1):
        try:
            r = requests.get(url, params={"range": rng, "interval": interval},
                             headers=_YAHOO_HEADERS, timeout=HTTP_TIMEOUT)
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last_err = f"HTTP {r.status_code}"
            time.sleep(0.3 * (attempt + 1))
            continue
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        try:
            j = r.json()
            c = (j.get("chart", {}).get("result") or [{}])[0]
            ts = c.get("timestamp") or []
            if not ts:
                return None, (j.get("chart", {}).get("error") or {}).get("description", "no data")
            q = (c.get("indicators", {}).get("quote") or [{}])[0] or {}
            adj = c.get("indicators", {}).get("adjclose") or [{}]
            adjclose = (adj[0].get("adjclose") if adj and adj[0] else None)
            meta = c.get("meta", {})
            idx = pd.to_datetime(ts, unit="s", utc=True)
            df = pd.DataFrame({
                "Open": q.get("open"), "High": q.get("high"),
                "Low": q.get("low"), "Close": adjclose or q.get("close"),
                "Volume": q.get("volume"),
            }, index=idx)
            df = df.replace(0, pd.NA).dropna(how="all")
            return df, meta
        except Exception as e:
            return None, f"parse {type(e).__name__}: {str(e)[:80]}"
    return None, last_err


def _yahoo_fundamental(sym):
    """抓單檔本益比（盈餘殖利率）。Yahoo quote/quoteSummary 端點已需 crumb（401），
    yfinance .info 也因此會掛住。這裡用背景執行緒 + 硬逾時包住它，確保單檔絕不
    超過 FUNDA_TIMEOUT 回傳 None。回傳 float（本益比）或 None。
    """
    from concurrent.futures import ThreadPoolExecutor
    result = {}

    def _work():
        try:
            info = yf.Ticker(sym).info
            pe = info.get("forwardPE") or info.get("trailingPE")
            result["pe"] = float(pe) if pe and pe > 0 else None
        except Exception:
            result["pe"] = None

    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_work)
    try:
        fut.result(timeout=FUNDA_TIMEOUT)
    except Exception:
        # 逾時或 yfinance 卡死：放棄這檔，不等它
        ex.shutdown(wait=False)
        return None
    finally:
        ex.shutdown(wait=False)
    return result.get("pe")


def fetch_fundamentals(tickers):
    """抓取本益比,計算盈餘殖利率。改 fail-fast：Yahoo quote/quoteSummary 已 401，
    yfinance .info 亦會卡死，故每檔用 FUNDA_TIMEOUT 硬逾時 + 併發，迅速回 n/a，
    不再像舊版一樣逐檔乾等 150s+。
    """
    out = {}
    tickers = list(tickers)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_yahoo_fundamental, t): t for t in tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                pe = fut.result()
            except Exception:
                pe = None
            out[t] = {
                "pe": pe,
                "earnings_yield": (100 / pe) if pe and pe > 0 else None,
            }
    return out


def _fetch_one(t):
    """抓單檔：回傳 (ticker, rec) 或 (ticker, None)。rec 欄位同舊版 fetch_market_data。"""
    try:
        df, meta = _yahoo_chart(t)
        if df is None or len(df) < 2:
            return t, None
        close = df["Close"].dropna()
        if len(close) < 2:
            return t, None

        # 最新價：優先用 chart 即時價；盤後/延遲則退回最後一根 close
        live = meta.get("regularMarketPrice")
        last_close = float(close.iloc[-1])
        if live and float(live) > 0:
            last = float(live)
        else:
            last = last_close
            live = None

        # 前一交易日收盤：用歷史 close 倒數第二根（chart previousClose 不可靠）
        prev = float(close.iloc[-2])

        ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

        # 52 週高低：優先用盤中 High/Low（含最新一根的即時價）
        hi_s = df["High"].dropna() if "High" in df else close
        lo_s = df["Low"].dropna() if "Low" in df else close
        high_52w = float(hi_s.max()) if len(hi_s) else last
        low_52w = float(lo_s.min()) if len(lo_s) else last

        rec = {
            "price": last,
            "chg_pct": (last - prev) / prev * 100 if prev else 0.0,
            "ma200": ma200,
            "ma200_dev": ((last - ma200) / ma200 * 100) if ma200 else None,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "asof": df.index[-1],
        }
        try:
            rec["tech"] = compute_technicals(df)
        except Exception:
            rec["tech"] = None
        return t, rec
    except Exception:
        return t, None


def fetch_market_data(tickers):
    """抓取報價與 200 日均線。改直連 Yahoo v8 chart 端點（併發 + fail-fast）。

    tickers 為可迭代代號。失敗的標的會被靜默跳過（回傳 dict 不含它）。
    全部標的都會在 HTTP_TIMEOUT 內回傳，不再出現 150s+ 的 yfinance 卡死。
    """
    out = {}
    tickers = list(tickers)
    if not tickers:
        return out
    # 快速同步收集，避免併發時 stdout 交錯無法排錯
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futs):
            t, rec = fut.result()
            if rec is not None:
                out[t] = rec
    return out


def fetch_fundamentals(tickers):
    """抓取本益比,計算盈餘殖利率。改 fail-fast：quoteSummary 已 401，退回
    yfinance .info（若可用），每檔不管結果如何都在短時間內回傳，不再卡死。
    """
    out = {}
    for t in tickers:
        pe = _yahoo_fundamental(t)
        out[t] = {
            "pe": pe,
            "earnings_yield": (100 / pe) if pe and pe > 0 else None,
        }
    return out


def fetch_fred(series_id, key):
    """抓取 FRED 總經序列最新值。"""
    if not key:
        return None
    import requests
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id, "api_key": key, "file_type": "json",
        "sort_order": "desc", "limit": 2,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        obs = r.json().get("observations", [])
        if not obs:
            return None
        return {"date": obs[0]["date"], "value": float(obs[0]["value"])}
    except Exception:
        return None


def check_alerts(macro, stocks):
    """比對門檻,回傳警示清單。"""
    alerts = []

    def val(t):
        return macro.get(t, {}).get("price")

    tnx, tyx = val("^TNX"), val("^TYX")
    dxy, jpy = val("DX-Y.NYB"), val("JPY=X")
    vix = val("^VIX")

    if tnx:
        if tnx >= ALERTS["tnx_high"]:
            alerts.append(("red", f"10Y 殖利率 {tnx:.2f}% ≥ {ALERTS['tnx_high']}% — 企業投資決策壓力區"))
        elif tnx <= ALERTS["tnx_low"]:
            alerts.append(("green", f"10Y 殖利率 {tnx:.2f}% ≤ {ALERTS['tnx_low']}% — 降息預期升溫"))
    if tyx and tyx >= ALERTS["tyx_high"]:
        alerts.append(("red", f"30Y 殖利率 {tyx:.2f}% ≥ {ALERTS['tyx_high']}% — 長端供給壓力,折現率警戒"))
    if dxy:
        if dxy >= ALERTS["dxy_high"]:
            alerts.append(("yellow", f"DXY {dxy:.2f} — 突破區間上緣,美元轉強"))
        elif dxy <= ALERTS["dxy_low"]:
            alerts.append(("yellow", f"DXY {dxy:.2f} — 跌破區間下緣,美元轉弱"))
    if jpy:
        if jpy >= ALERTS["jpy_high"]:
            alerts.append(("red", f"USD/JPY {jpy:.2f} — 進入干預區間"))
        elif jpy <= ALERTS["jpy_low"]:
            alerts.append(("red", f"USD/JPY {jpy:.2f} — 套利平倉風險,注意全球流動性收縮"))
    if vix:
        if vix >= ALERTS["vix_high"]:
            alerts.append(("red", f"VIX {vix:.2f} — 市場轉為緊張"))
        elif vix <= ALERTS["vix_low"]:
            alerts.append(("yellow", f"VIX {vix:.2f} — 過度平靜,留意隱藏槓桿"))

    for t, d in stocks.items():
        dev = d.get("ma200_dev")
        tech = d.get("tech") or {}
        flags = tech.get("flags") or {}

        # ── 複合訊號（多條件同時成立，可靠度較高，優先顯示）──
        near_ma = dev is not None and abs(dev) <= abs(ALERTS["ma200_dev"])
        below_ma = dev is not None and dev <= -5
        composite = None

        # 1) 資金進場：爆量上漲 + Stochastic 自超賣回升 + 貼近年線
        if flags.get("vol_surge_up") and flags.get("stoch_rebound") and near_ma:
            composite = ("green",
                         f"★ {t} 資金進場訊號 — 爆量上漲＋超賣回升＋貼近年線（三重確認）")
        # 2) 資金出逃：爆量下跌 + 跌破年線 + MACD 死亡交叉
        elif flags.get("vol_surge_down") and below_ma and flags.get("macd_death"):
            composite = ("red",
                         f"★ {t} 資金出逃警戒 — 爆量下跌＋跌破年線＋MACD死叉（三重確認）")
        # 3) 變盤前兆：爆量整理（量爆但價平）
        elif flags.get("vol_surge_flat"):
            composite = ("yellow",
                         f"★ {t} 變盤前兆 — 爆量但價平，多空激烈換手")

        if composite:
            alerts.append(composite)
            # 複合訊號已觸發，抑制該檔的年線與單一技術訊號，避免洗版
            continue

        # ── 沒觸發複合訊號，才顯示個別訊號 ──
        if dev is not None:
            if near_ma:
                alerts.append(("blue", f"{t} 位於年線附近 (乖離 {dev:+.1f}%) — 年線觀察區"))
            elif dev <= -5:
                alerts.append(("yellow", f"{t} 跌破年線 {dev:+.1f}%"))
            elif dev >= ALERTS["ma200_stretch"]:
                alerts.append(("yellow", f"{t} 高於年線 {dev:+.1f}% — 乖離偏大"))

        for sig_type, msg in tech.get("signals", []):
            col = {
                "stoch_rebound": "green",
                "macd_gold": "green",
                "macd_death": "red",
                "rsi63_low": "blue",
                "rsi63_high": "yellow",
                "vol_surge_up": "green",
                "vol_surge_down": "red",
                "vol_surge_flat": "yellow",
                "vol_high": "blue",
            }.get(sig_type, "gray")
            alerts.append((col, f"{t} {msg}"))
    return alerts


def render(macro, stocks, funda, tyx, fred_data, watchlist):
    print("\n" + "=" * 78)
    print(color(f"  市場監控儀表板   {datetime.now():%Y-%m-%d %H:%M:%S}", "bold"))
    print(color("  資料來源 Yahoo v8 chart API（直連）,約 15 分鐘延遲", "gray"))
    print("=" * 78)

    # 總經
    print(color("\n【總經與市場指標】", "bold"))
    print(f"  {'指標':<22}{'數值':>12}{'日變動':>12}")
    print("  " + "-" * 46)
    for t, name in MACRO.items():
        d = macro.get(t)
        if not d:
            continue
        print(f"  {name:<20}{d['price']:>12.2f}   {pct_color(d['chg_pct'])}")

    # 廣度
    spy, rsp = macro.get("SPY"), macro.get("RSP")
    if spy and rsp:
        diff = rsp["chg_pct"] - spy["chg_pct"]
        tag = "廣度良好 (等權重領先)" if diff > 0 else "權值股主導 (廣度偏弱)"
        print(f"\n  {color('廣度訊號:', 'bold')} RSP - SPY = {diff:+.2f}%  → {tag}")

    # 總經數據 (FRED)
    if fred_data:
        print(color("\n【總體經濟數據 (FRED)】", "bold"))
        for name, d in fred_data.items():
            if d:
                print(f"  {name:<28}{d['value']:>12,.2f}   ({d['date']})")

    # 個股
    print(color("\n【個股觀察清單】", "bold"))
    hdr = (f"  {'代號':<7}{'名稱':<12}{'股價':>9}{'日變動':>10}{'年線乖離':>10}"
           f"{'RSI14':>7}{'季RSI':>7}{'Stoch':>7}{'MACD':>7}{'相對量':>8}{'盈餘殖利率':>11}")
    print(hdr)
    print("  " + "-" * 80)
    for t, name in watchlist.items():
        d = stocks.get(t)
        if not d:
            continue
        f = funda.get(t, {})
        dev = d.get("ma200_dev")
        dev_s = color(f"{dev:+8.1f}%", "red" if dev and dev < 0 else "gray") if dev is not None else "     n/a"

        tech = d.get("tech") or {}
        r14 = tech.get("rsi14")
        r63 = tech.get("rsi63")
        # RSI 著色：>70 紅(超買)、<30 綠(超賣)
        def rsi_str(v):
            if v is None:
                return "    n/a"
            c = "red" if v > 70 else "green" if v < 30 else "gray"
            return color(f"{v:>6.0f}", c)
        r14_s, r63_s = rsi_str(r14), rsi_str(r63)

        macd = tech.get("macd")
        if macd:
            h = macd["hist"]
            macd_s = color(f"{'▲' if h > 0 else '▼':>6}", "green" if h > 0 else "red")
        else:
            macd_s = "    n/a"

        # Stochastic %K：<20 綠(超賣)、>80 紅(超買)
        stoch = tech.get("stoch")
        if stoch:
            k = stoch["k"]
            sc = "green" if k < 20 else "red" if k > 80 else "gray"
            stoch_s = color(f"{k:>6.0f}", sc)
        else:
            stoch_s = "    n/a"

        # 相對量 RVOL：≥3 倍紅(爆量)、≥2 倍黃(放量)、<0.5 灰(量縮)
        vol = tech.get("vol")
        if vol:
            r = vol["ratio"]
            vc = "red" if r >= 3 else "yellow" if r >= 2 else "gray"
            vol_s = color(f"{r:>6.1f}x", vc)
        else:
            vol_s = "     n/a"

        ey = f.get("earnings_yield")
        if ey is not None:
            ey_s = color(f"{ey:>9.2f}%", "red" if tyx and ey < tyx else "green")
        else:
            ey_s = "      n/a"
        print(f"  {t:<7}{name:<12}{d['price']:>9.2f}   {pct_color(d['chg_pct'])}{dev_s}"
              f"{r14_s}{r63_s}{stoch_s}{macd_s}{vol_s}{ey_s}")

    if tyx:
        print(color(f"\n  參考:30年期公債殖利率 {tyx:.2f}% — 盈餘殖利率低於此值者標紅", "gray"))
        print(color("  (代表承擔股票風險,收益卻不如無風險公債)", "gray"))

    # 警示
    alerts = check_alerts(macro, stocks)
    print(color("\n【門檻警示】", "bold"))
    if alerts:
        # 複合訊號（★ 開頭）排最前面優先顯示
        alerts.sort(key=lambda x: 0 if x[1].startswith("★") else 1)
        for c, msg in alerts:
            marker = "★" if msg.startswith("★") else "●"
            display = msg[1:].strip() if msg.startswith("★") else msg
            print(f"  {color(marker, c)} {color(display, c) if marker == '★' else display}")
    else:
        print(color("  無指標觸及設定門檻", "gray"))

    print(color("\n  ※ 警示為觀察門檻,非買賣訊號。投資決策請自行判斷。", "gray"))
    print("=" * 78 + "\n")


def write_csv(macro, stocks, path="market_log.csv"):
    row = {"timestamp": datetime.now().isoformat()}
    for t, d in macro.items():
        row[t] = round(d["price"], 4)
    for t, d in stocks.items():
        row[t] = round(d["price"], 4)
        if d.get("ma200_dev") is not None:
            row[f"{t}_ma200dev"] = round(d["ma200_dev"], 2)
    df = pd.DataFrame([row])
    try:
        import os
        df.to_csv(path, mode="a", header=not os.path.exists(path), index=False)
    except Exception as e:
        print(color(f"CSV 寫入失敗: {e}", "red"))


def run_once(use_csv=False, with_funda=True):
    watchlist = load_watchlist()
    macro = fetch_market_data(list(MACRO))
    stocks = fetch_market_data(list(watchlist))
    funda = fetch_fundamentals(list(watchlist)) if with_funda else {}

    tyx = macro.get("^TYX", {}).get("price")

    fred_data = {}
    if FRED_API_KEY:
        for name, sid in {
            "非農就業 (千人)": "PAYEMS",
            "失業率 (%)": "UNRATE",
            "CPI 年增率 (%)": "CPIAUCSL",
            "勞動參與率 (%)": "CIVPART",
        }.items():
            fred_data[name] = fetch_fred(sid, FRED_API_KEY)

    render(macro, stocks, funda, tyx, fred_data, watchlist)
    if use_csv:
        write_csv(macro, stocks)


def main():
    p = argparse.ArgumentParser(description="市場監控儀表板")
    p.add_argument("--watch", nargs="?", const=300, type=int, metavar="SEC",
                   help="持續監控模式,預設每 300 秒更新")
    p.add_argument("--csv", action="store_true", help="同時寫入 market_log.csv")
    p.add_argument("--fast", action="store_true", help="跳過本益比抓取 (較快)")
    p.add_argument("--insecure", action="store_true",
                   help="停用 SSL 驗證 (僅供診斷，不建議長期使用)")
    args = p.parse_args()

    if args.insecure:
        print(color("[警告] 已停用 SSL 憑證驗證。這只應用於診斷，", "yellow"))
        print(color("       確認問題後請執行 fix_ssl.py 取得正確解法。", "yellow"))
        os.environ.pop("CURL_CA_BUNDLE", None)
        os.environ.pop("SSL_CERT_FILE", None)
        os.environ.pop("REQUESTS_CA_BUNDLE", None)
        try:
            import curl_cffi.requests as cr
            _orig = cr.Session.__init__

            def _patched(self, *a, **kw):
                kw["verify"] = False
                return _orig(self, *a, **kw)
            cr.Session.__init__ = _patched
        except Exception as e:
            print(color(f"       無法套用 insecure 模式: {e}", "red"))

    if _CA and not args.insecure:
        src = "ca_bundle.pem (fix_ssl.py 產生)" if _CA == _BUNDLE else "certifi 預設憑證庫"
        print(color(f"憑證來源: {src}", "gray"))

    if args.watch:
        print(color(f"監控模式啟動,每 {args.watch} 秒更新。按 Ctrl+C 結束。", "blue"))
        try:
            while True:
                run_once(args.csv, not args.fast)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print(color("\n監控結束。", "blue"))
    else:
        run_once(args.csv, not args.fast)


if __name__ == "__main__":
    main()
