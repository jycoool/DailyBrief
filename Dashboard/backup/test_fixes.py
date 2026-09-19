"""
離線驗證 market_monitor_web.py（v5 合併版）

不需網路、不需 yfinance。技術指標用真實 pandas 計算並與獨立實作對照。
    python3 test_fixes.py
"""
import sys, types, math

if "yfinance" not in sys.modules:
    sys.modules["yfinance"] = types.ModuleType("yfinance")

import pandas as pd
import numpy as np
import market_monitor_web as M

PASS, FAIL = [], []


def check(desc, cond):
    (PASS if cond else FAIL).append(desc)
    print(f"    {'✓' if cond else '✗ 失敗'}  {desc}")


def head(t):
    print("\n" + "=" * 64)
    print(t)
    print("=" * 64)


# ══════════════════════════════════════════════════════════════
head("測試 1：技術指標數學正確性（對照獨立實作）")

rng = np.random.default_rng(42)
n = 300
close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0006, 0.018, n))))
high = close * (1 + abs(rng.normal(0, 0.008, n)))
low = close * (1 - abs(rng.normal(0, 0.008, n)))
df = pd.DataFrame({"Close": close, "High": high, "Low": low})

# --- RSI：與 Wilder 遞迴定義對照 ---
def rsi_reference(s, period=14):
    d = s.diff().dropna()
    gain, loss = d.clip(lower=0), (-d).clip(lower=0)
    ag = gain.iloc[:period].mean()
    al = loss.iloc[:period].mean()
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + gain.iloc[i]) / period
        al = (al * (period - 1) + loss.iloc[i]) / period
    return 100 - 100 / (1 + ag / al)

r_mine = M._rsi(close, 14)
r_ref = rsi_reference(close, 14)
print(f"  RSI14  程式 {r_mine:.4f}  vs  Wilder 遞迴 {r_ref:.4f}  "
      f"（差 {abs(r_mine - r_ref):.4f}）")
check("RSI14 收斂到 Wilder 定義（差 < 0.5）", abs(r_mine - r_ref) < 0.5)
check("RSI14 落在 0-100", 0 <= r_mine <= 100)
check("季RSI(63) 可計算", M._rsi(close, 63) is not None)
check("資料不足時 RSI 回 None", M._rsi(close.head(5), 14) is None)

# --- MACD：與直接 EMA 計算對照 ---
ef = close.ewm(span=12, adjust=False).mean()
es = close.ewm(span=26, adjust=False).mean()
ml = ef - es
sl = ml.ewm(span=9, adjust=False).mean()
macd = M._macd(close)
print(f"  MACD   程式 hist {macd['hist']:+.6f}  vs  參照 {(ml - sl).iloc[-1]:+.6f}")
check("MACD hist 與參照一致", abs(macd["hist"] - (ml - sl).iloc[-1]) < 1e-9)
check("MACD 有 hist_prev（交叉判斷需要）", "hist_prev" in macd)
check("MACD = 快線 - 慢線", abs(macd["macd"] - ml.iloc[-1]) < 1e-9)

# --- Stochastic：與定義對照 ---
st = M._stochastic(high, low, close)
lo14 = low.rolling(14).min()
hi14 = high.rolling(14).max()
fk = 100 * (close - lo14) / (hi14 - lo14)
sk_ref = fk.rolling(3).mean().dropna().iloc[-1]
print(f"  Stoch  程式 %K {st['k']:.4f}  vs  參照 {sk_ref:.4f}")
check("慢速 %K 與定義一致", abs(st["k"] - sk_ref) < 1e-9)
check("%K 落在 0-100", 0 <= st["k"] <= 100)
check("%D 為 %K 的 3 期均（值合理）", 0 <= st["d"] <= 100)

# --- 訊號旗標 ---
tech = M.compute_technicals(df)
check("compute_technicals 回傳四項指標",
      all(k in tech for k in ("rsi14", "rsi63", "macd", "stoch")))
check("signals 為 list", isinstance(tech["signals"], list))

# 人造死叉：確認 macd_death 會觸發
down = pd.Series(np.concatenate([np.linspace(100, 160, 200),
                                 np.linspace(160, 120, 60)]))
d2 = pd.DataFrame({"Close": down, "High": down * 1.01, "Low": down * 0.99})
sigs = [s[0] for s in M.compute_technicals(d2)["signals"]]
check("人造下跌段觸發 MACD 死亡交叉或中期訊號",
      any(s in ("macd_death", "rsi63_low") for s in sigs))

# ══════════════════════════════════════════════════════════════
head("測試 2：52 週高低點改用盤中 High/Low")

hi_intraday = float(high.max())
hi_close = float(close.max())
print(f"  盤中最高 {hi_intraday:.2f}   收盤最高 {hi_close:.2f}   "
      f"差 {(hi_intraday / hi_close - 1) * 100:+.2f}%")
check("盤中高點 ≥ 收盤高點（舊版低估的部分）", hi_intraday >= hi_close)

# ══════════════════════════════════════════════════════════════
head("測試 3：本益比基準 trailing 為主 + 自動校驗")

fwd_pe = {"SIL": 18.7, "SPY": 26.2, "QQQ": 31.3, "CQQQ": 19.6, "GEV": 42.2,
          "CNYA": 17.6, "BWXT": 33.0, "AAPL": 32.2, "GOOGL": 23.5, "MSFT": 21.0,
          "TSLA": 157.0, "TSM": 19.6, "INTC": 50.2, "NVDA": 17.6, "AVGO": 20.1,
          "MU": 6.3, "SKHY": 5.2, "MRVL": 35.6, "ET": 12.0, "SNOW": 121.9}
trail_pe = {"SIL": 22.4, "SPY": 29.8, "QQQ": 38.1, "CQQQ": 21.0, "GEV": 61.5,
            "CNYA": 18.9, "BWXT": 41.2, "AAPL": 36.4, "GOOGL": 27.9, "MSFT": 26.3,
            "TSLA": 214.0, "TSM": 24.1, "INTC": None, "NVDA": 29.4, "AVGO": 65.9,
            "MU": 8.1, "SKHY": 2.6, "MRVL": 48.0, "ET": 13.6, "SNOW": None}

TICKERS = ["UVXY", "UNG", "TMF", "GLDM", "SIL", "SPY", "SGOV", "QQQ", "CQQQ",
           "GEV", "CNYA", "BWXT", "AAPL", "GOOGL", "MSFT", "TSLA", "SPCX",
           "TSM", "INTC", "NVDA", "AVGO", "MU", "SKHY", "MRVL", "ET", "SNOW"]
M.WATCHLIST = {t: t for t in TICKERS}

funda = {}
for t in TICKERS:
    tr = M._clean_pe(trail_pe.get(t))
    fw = M._clean_pe(fwd_pe.get(t))
    pe, b = (tr, "T") if tr else ((fw, "F") if fw else (None, None))
    funda[t] = {"pe": pe, "pe_basis": b, "pe_trailing": tr, "pe_forward": fw,
                "earnings_yield": (100 / pe) if pe else None,
                "ey_forward": (100 / fw) if fw else None}

print("\n  ── 基準轉換 ──")
for t in ("AVGO", "NVDA", "MU", "SKHY", "AAPL"):
    old = fwd_pe[t]
    f = funda[t]
    print(f"    {t:6s} 舊 {old:>6.1f} / EY {100/old:5.2f}%"
          f"   →   新 {f['pe']:>6.1f}{f['pe_basis']} / EY {f['earnings_yield']:5.2f}%")

print("\n  ── 自動校驗（原本要你用眼睛做的事）──")
M._audit_pe(funda)

check("AVGO 標記 T/F 分歧（65.9 vs 20.1 = 3.3 倍）",
      "divergent" in funda["AVGO"]["audit"])
check("SKHY 標記數值極端（trailing 2.6）",
      "extreme" in funda["SKHY"]["audit"])
check("INTC 標記僅 forward", "forward" in funda["INTC"]["audit"])
check("SPCX 標記無資料", "missing" in funda["SPCX"]["audit"])
check("AAPL 無旗標（36.4 vs 32.2 一致）", funda["AAPL"]["audit"] == [])
check("MU 無分歧旗標（8.1 vs 6.3 只差 1.3 倍）",
      "divergent" not in funda["MU"]["audit"])

tyx = 5.26
old_below = sum(1 for t in TICKERS
                if (fwd_pe.get(t) or trail_pe.get(t))
                and 100 / (fwd_pe.get(t) or trail_pe.get(t)) < tyx)
new_below = sum(1 for f in funda.values()
                if f["earnings_yield"] and f["earnings_yield"] < tyx)
n_have = sum(1 for f in funda.values() if f["earnings_yield"])
print(f"\n  重力線下方：舊 {old_below}/{n_have}  →  新 {new_below}/{n_have}")

# ══════════════════════════════════════════════════════════════
head("測試 4：警示過濾（現金型 / 耗損型 / 循環頂點）")

macro = {
    "^TNX": {"price": 4.70, "chg_pct": 1.19},
    "^TYX": {"price": 5.26, "chg_pct": 1.00},
    "DX-Y.NYB": {"price": 99.67, "chg_pct": -0.29},
    "JPY=X": {"price": 159.27, "chg_pct": -0.10},
    "GC=F": {"price": 4437.30, "chg_pct": 1.69},
    "SI=F": {"price": 65.11, "chg_pct": 0.36},
    "CL=F": {"price": 82.40, "chg_pct": 1.42},
    "^VIX": {"price": 14.25, "chg_pct": -2.60},
    "^SOX": {"price": 12417.05, "chg_pct": -0.31},
    "^GSPC": {"price": 6812.44, "chg_pct": -0.17},
    "^NDX": {"price": 25104.9, "chg_pct": -0.13},
    "QQQ": {"price": 731.07, "chg_pct": -0.14},
    "SPY": {"price": 776.34, "chg_pct": -0.20},
    "RSP": {"price": 222.77, "chg_pct": 0.02},
}

raw = {  # 2026-08-14 收盤：price, chg, ma200_dev
    "UVXY": (20.10, -2.05, -47.1), "UNG": (9.92, -0.50, -17.3),
    "TMF": (30.59, -2.08, -15.4), "GLDM": (86.58, 0.62, -2.5),
    "SIL": (89.55, 0.98, 3.0), "SPY": (776.34, -0.20, 10.5),
    "SGOV": (100.56, 0.03, 1.5), "QQQ": (731.07, -0.14, 12.6),
    "CQQQ": (50.88, -0.33, -2.5), "GEV": (1063.25, 1.32, 24.1),
    "CNYA": (36.09, -0.41, 1.8), "BWXT": (173.22, 1.68, -11.4),
    "AAPL": (305.93, 0.22, 9.3), "GOOGL": (345.90, -0.13, 4.5),
    "MSFT": (495.40, -0.30, 14.9), "TSLA": (342.27, 0.68, -15.7),
    "SPCX": (140.00, -0.91, None), "TSM": (426.35, -0.96, 17.7),
    "INTC": (102.50, -1.97, 46.1), "NVDA": (225.16, -0.06, 15.6),
    "AVGO": (392.99, -5.94, 6.7), "MU": (971.66, 2.30, 75.9),
    "SKHY": (166.33, 0.40, None), "MRVL": (222.02, -0.07, 57.0),
    "ET": (21.05, 1.40, 17.3), "SNOW": (328.92, -2.51, 53.4),
}
stocks = {}
for t, (p, c, dv) in raw.items():
    stocks[t] = {"price": p, "chg_pct": c, "ma200_dev": dv,
                 "high_52w": p * 1.25, "low_52w": p * 0.7, "tech": {}}

# 給三檔真實技術資料，驗證訊號會出現
stocks["TMF"]["tech"] = M.compute_technicals(d2)      # 人造下跌段
stocks["AAPL"]["tech"] = M.compute_technicals(df)
stocks["SGOV"]["tech"] = M.compute_technicals(d2)     # 應被整檔過濾

al = M.check_alerts(macro, stocks, funda)
for lv, h, dd in sorted(al, key=lambda x: (0 if x[1][:1] in "★⚑" else 1, x[0])):
    print(f"  [{lv:8s}] {h} — {dd}")

titles = " ".join(h for _, h, _ in al)
msgs = " ".join(dd for _, _, dd in al)
print()
check("SGOV 完全不出現（現金型整檔過濾，含技術訊號）", "SGOV" not in titles)
check("UVXY 無乖離警示", "UVXY 跌破年線" not in titles)
check("TMF 無乖離警示", "TMF 跌破年線" not in titles)
check("UNG 無乖離警示", "UNG 跌破年線" not in titles)
check("TMF 保留技術訊號（槓桿商品短線仍有意義）", "TMF" in titles)
check("TMF 技術訊號附槓桿提醒", "槓桿商品" in msgs)
check("MU 觸發循環頂點嫌疑", "⚑ MU" in titles)
check("INTC 保留乖離偏大", "INTC 乖離偏大" in titles)
check("BWXT 觸發估值陷阱（跌破年線+殖利率倒掛升級為複合訊號）",
      "★ BWXT 估值陷阱" in titles)
check("TSLA 觸發估值陷阱（跌破年線+殖利率倒掛升級為複合訊號）",
      "★ TSLA 估值陷阱" in titles)
check("VIX 14.25 現在會觸發（門檻已調到 15.0）", "VIX 14.25" in titles)
check("JPY 159.27 未誤觸干預警示（門檻 160）", "美元/日圓" not in titles)

# ══════════════════════════════════════════════════════════════
head("測試 5：HTML 產出完整性")

pm_data = {
    "GLDM": {"price": 86.58, "chg_pct": 0.62, "ma200_dev": -2.5,
             "high_52w": 108.90, "low_52w": 61.0},
    "SIVR": {"price": 61.47, "chg_pct": 0.49, "ma200_dev": -8.8,
             "high_52w": 113.20, "low_52w": 40.0},
}
polymarket_data = {
    "聯準會 / 利率": [
        {"question": "Will there be no change in Fed interest rates after September FOMC?",
         "yes_prob": 74, "volume": 7_800_000},
        {"question": "Fed rate hike in 2026?", "yes_prob": 46, "volume": 7_500_000},
    ],
}
html = M.build_html(macro, stocks, funda, {}, refresh=0,
                    pm_data=pm_data, polymarket_data=polymarket_data)
open("dashboard_test.html", "w", encoding="utf-8").write(html)

check("技術指標欄位回到表頭", all(x in html for x in ("RSI14", "季RSI", "Stoch", "MACD")))
check("技術欄有實際數值渲染", 'class="tech-col"' in html)
check("MACD 方向符號有渲染", "▲" in html or "▼" in html)
check("本益比欄存在且標示基準", "本益比 <sup" in html)
check("PE 校驗旗標有渲染", "pill aud" in html)
check("QQQ 標明為 ETF", "QQQ · 那指100 ETF" in html)
check("SPY 標明為 ETF", "SPY · 標普500 ETF" in html)
check("新增標普500 指數點位", "標普500 指數" in html)
check("新增那斯達克100 指數點位", "那斯達克100 指數" in html)
check("黃金/白銀標明為期貨", "白銀 期貨" in html)
check("重力線註解已改為 trailing", "歷史（trailing）本益比" in html)
check("重力線含循環股反向陷阱說明", "反向陷阱" in html)
check("舊的錯誤註解已移除", "本益比倒數，未計入成長" not in html)
check("SGOV 顯示現金型標籤", "現金型" in html)
check("UVXY/TMF 顯示耗損標籤", "耗損" in html)
check("MU 顯示循環旗標", "pill cyc" in html)
check("貴金屬區塊正常", "貴金屬持倉" in html and "SIVR" in html)
check("52W 高點標明為盤中", "盤中" in html)
check("金銀比正常計算（68.2）", "金銀比" in html and "68.2" in html)
check("Polymarket 區塊正常", "Polymarket 預測市場" in html)
check("市場廣度區塊正常", "市場廣度" in html)
check("手機版隱藏技術欄避免爆版", ".name,.pe-col,.tech-col{display:none}" in html)

print(f"\n  HTML {len(html):,} bytes → dashboard_test.html")

head(f"結果：{len(PASS)} 通過 / {len(FAIL)} 失敗")
for f in FAIL:
    print(f"  ✗ {f}")
print("全部通過 ✓" if not FAIL else "有項目失敗 ✗")
sys.exit(1 if FAIL else 0)
