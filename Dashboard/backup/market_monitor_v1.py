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
  - 資料來源為 yfinance,約有 15 分鐘延遲
  - 警示為「值得關注的門檻」,非買賣訊號
  - FRED 總經數據需免費 API key (見下方 FRED_API_KEY)
"""

import argparse
import os
import sys
import time
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


def fetch_market_data(tickers):
    """抓取報價與 200 日均線。回傳 dict。"""
    out = {}
    try:
        data = yf.download(
            list(tickers), period="1y", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker",
        )
    except Exception as e:
        print(color(f"資料抓取失敗: {e}", "red"))
        return out

    for t in tickers:
        try:
            df = data[t] if len(tickers) > 1 else data
            close = df["Close"].dropna()
            if len(close) < 2:
                continue
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
            out[t] = {
                "price": last,
                "chg_pct": (last - prev) / prev * 100,
                "ma200": ma200,
                "ma200_dev": ((last - ma200) / ma200 * 100) if ma200 else None,
                "high_52w": float(close.max()),
                "low_52w": float(close.min()),
            }
        except Exception:
            continue
    return out


def fetch_fundamentals(tickers):
    """抓取本益比,計算盈餘殖利率。速度較慢,失敗時靜默跳過。"""
    out = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            pe = info.get("forwardPE") or info.get("trailingPE")
            out[t] = {
                "pe": pe,
                "earnings_yield": (100 / pe) if pe and pe > 0 else None,
            }
        except Exception:
            out[t] = {"pe": None, "earnings_yield": None}
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
        if dev is None:
            continue
        if abs(dev) <= abs(ALERTS["ma200_dev"]):
            alerts.append(("blue", f"{t} 位於年線附近 (乖離 {dev:+.1f}%) — 年線觀察區"))
        elif dev <= -5:
            alerts.append(("yellow", f"{t} 跌破年線 {dev:+.1f}%"))
        elif dev >= ALERTS["ma200_stretch"]:
            alerts.append(("yellow", f"{t} 高於年線 {dev:+.1f}% — 乖離偏大"))
    return alerts


def render(macro, stocks, funda, tyx, fred_data, watchlist):
    print("\n" + "=" * 78)
    print(color(f"  市場監控儀表板   {datetime.now():%Y-%m-%d %H:%M:%S}", "bold"))
    print(color("  資料來源 yfinance,約 15 分鐘延遲", "gray"))
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
    hdr = f"  {'代號':<7}{'名稱':<14}{'股價':>10}{'日變動':>11}{'年線乖離':>11}{'本益比':>9}{'盈餘殖利率':>12}"
    print(hdr)
    print("  " + "-" * 72)
    for t, name in watchlist.items():
        d = stocks.get(t)
        if not d:
            continue
        f = funda.get(t, {})
        dev = d.get("ma200_dev")
        dev_s = color(f"{dev:+9.1f}%", "red" if dev and dev < 0 else "gray") if dev is not None else "      n/a"
        pe = f.get("pe")
        pe_s = f"{pe:>8.1f}" if pe else "     n/a"
        ey = f.get("earnings_yield")
        # 盈餘殖利率低於 30Y 公債 → 標紅 (承擔風險卻拿更少)
        if ey is not None:
            ey_s = color(f"{ey:>10.2f}%", "red" if tyx and ey < tyx else "green")
        else:
            ey_s = "       n/a"
        print(f"  {t:<7}{name:<14}{d['price']:>10.2f}   {pct_color(d['chg_pct'])}{dev_s}{pe_s}{ey_s}")

    if tyx:
        print(color(f"\n  參考:30年期公債殖利率 {tyx:.2f}% — 盈餘殖利率低於此值者標紅", "gray"))
        print(color("  (代表承擔股票風險,收益卻不如無風險公債)", "gray"))

    # 警示
    alerts = check_alerts(macro, stocks)
    print(color("\n【門檻警示】", "bold"))
    if alerts:
        for c, msg in alerts:
            print(f"  {color('●', c)} {msg}")
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
