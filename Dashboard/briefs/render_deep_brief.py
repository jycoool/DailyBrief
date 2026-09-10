#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 9/4 深挖版初判渲染成獨立 HTML（自帶樣式，可離線開啟）。"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

MD = r"""# 每日市場初判（盤後）深度分析 — 2026-09-04

## 一、今天盤面真正發生的事：記憶體獨自噴發、其餘全線失血

先看三個互相矛盾的數字，這是整篇分析的起點：

| 指標 | 9/4 | 9/3 | 方向 |
|---|---|---|---|
| 費城半導體 | +3.37% | — | 大漲 |
| SPY | -0.39% | +1.02% | 轉跌 |
| 標普等權 RSP | -0.48% | — | 跌得比 SPY 深 |
| 30Y 殖利率 | 5.25% | 5.24% | +0.06%（微升回） |
| SKHY / MRVL / MU | +8.14% / +7.05% / +6.10% | 平淡 | 記憶體噴發 |

> **這組數字的意義**：昨天（9/3）是「風險偏好回暖」的全面噴發日。今天這個回暖**沒有延續成大盤續漲**，反而收斂成「只有記憶體一條線在走」的極端結構——費半獨漲 3.37%，大盤、等權重、防禦股、所有高乖離敘事股全部失血。

`RSP - SPY = -0.09%` 的廣度訊號連續第二日為「權值股主導（偏弱）」。**這是框架十最關鍵的一張考卷**：回暖若真有基本面支撐，廣度應同步擴大；實際是漲勢進一步收斂到記憶體單一主題。

---

## 二、訊號一：記憶體鏈逆勢噴發 —「結構續航」還是「末日噴出」？

### 為什麼漲

三檔同步爆量上漲，SKHY（海力士）領頭、MRVL 跟隨、MU 墊後——這是**以 HBM 為主線的記憶體輪動**，不是個股異動。

### 接回框架：命中「循環見頂觀察」的關鍵對決點（框架五）

兩條互相對立的判斷在並行：

- **結構派（多方）**：HBM 供不應求、SKHY 拿 2/3 HBM4 份額、全線 2026 售罄。
- **循環派（空方）**：76% 營業利益率是歷史極值、DRAM 第二階導數已見頂（Q2 +60% → Q3 13-18%）、盈餘殖利率 15-19% 是「週期高點陷阱」。

> 今天的噴發**沒有帶來能分辨兩者的新證據**。我們盯的證偽條件（框架八）是「跌價時毛利率能守在哪裡」——而價格根本還沒開始跌。今天的暴漲，本質是**在證偽條件還無法回答之前，市場先押注結構派一票**。

這正是框架五的精髓：**CEO 說「稀缺」不等於中立觀點**。

### 技術面確信度標記（這是我的猜測成分）

`MU 年線乖離 +67.7%`、`MRVL +47.4%`、`SKHY n/a`——乖離已極端。

**判斷：這波噴發更像「資金在狹窄行情裡找最後一個還有動能的角落」，而不是新一輪結構性重估的起點（確信度：中低）。** 理由：若是結構重估，會伴隨大盤廣度擴張與成交量全面放大；今天廣度收縮、量能只集中在記憶體三檔。

### 對持股的意涵

若持有 MU/SKHY，今天的上漲不是「你判斷對了」，而是「把獲利了結時機往前推」——高乖離 + 高盈餘殖利率（市場已 price-in 下行風險的折價）一旦疊加，回吐極快（8/18 那波 MU 從 +68% 乖離一天回吐就是前例）。

---

## 三、訊號二：高乖離敘事股集體回吐（TSLA -5.92% / SNOW -5.41% / LULU -17.38%）

### 為什麼跌 + 接回框架

knowledge 的 9/3 記錄裡，我們**自己寫下過 SNOW 的證偽條件**：

> 「若 SNOW 未來 5 個交易日內能守住昨天漲幅一半以上，代表有基本面支撐，否則視為超買反彈。」

**今天（9/4）就是這個證偽條件的第一天測試，SNOW -5.41%**——方向是「回吐」，不是「守成」。

用框架六（贏家正面清單）的「收入證明」標準檢驗：

- **TSLA**：盈餘殖利率 0.61%，watchlist 最極端值。純敘事驅動（Cybercab 9/3 剛辦完），無「本季數字」支撐。
- **SNOW**：盈餘殖利率 0.88%，年線乖離 +54%。9/3 的 +16.6% 是 5 倍量敘事催化劑，不是收入證明。
- **LULU**：-17.38% 爆量 8.7x，三重確認（爆量下跌＋跌破年線 -34.1%＋MACD 死叉）——是「基本面失望」的獨立利空，性質與前兩檔不同。

### 關鍵推理：9/3 的「risk-on 反轉」正在被證偽

**判斷（確信度：中）：9/3 的高 Beta 噴發，比較接近「壓力釋放的反彈」而非「趨勢反轉的起點」。** 若是趨勢反轉，防禦性資金應持續流出、高 Beta 持續走強；今天黃金 -0.32%、DXY +0.16% 反彈、USD/JPY -1.70% 日圓動能還在——**這是「risk-on 只開半天，第二天就熄火」的典型**。

---

## 四、訊號三：30Y 殖利率 5.25% 微升回 — 折現率的重力沒有消失

### 為什麼重要 + 接回框架

昨天 30Y 從 5.27% 回落到 5.24% 是「方向性首降」，今天 **+0.06% 到 5.25%，方向又翻回來**。

這是框架三（折現率／重力線）最實際的現場：成長股價值主體落在 15-30 年後的獲利，30Y 殖利率往上走一丁點，那些「盈餘殖利率 0.6-1%」的敘事股重力壓就立刻加重。**今天成長股集體回吐與 30Y 微升是同一件事的兩面。**

### 更深一層：殖利率驅動力還在「供給/term premium」這一邊

今天 10Y +0.46% 到 4.78%、30Y +0.06% 到 5.25%，在 CPI/PPI 才剛雙降溫的背景下，這種「殖利率不下來」**更偏向供給端驅動**——意味著貝森特的工具箱只能拖延、不能解決，核心困境原封不動。

> **對持股的意涵**：只要 30Y 死守 5.2%+ 不下來，任何「成長股估值重估」都只能是曇花一現。真正的轉機要等 9/16 FOMC 後看 30Y 是否跌破 5.0%（框架八證偽條件）。

---

## 五、重力線引爆圖（30Y 5.25% 基準）

**線下最危險（盈餘殖利率 << 5.25%，估值全靠未來兌現）：**

| 標的 | 盈餘殖利率 | 今天的訊號 |
|---|---|---|
| TSLA | 0.61% | -5.92%，敘事回吐 |
| SNOW | 0.88% | -5.41%，證偽條件首日測試 |
| SPCX | 1.08% | -1.20% |
| INTC | 2.13% | +4.51%（增發稀釋股卻漲，最反常） |

**線上「數字看似安全」實為陷阱（框架五）：**

| 標的 | 盈餘殖利率 | 為何是陷阱 |
|---|---|---|
| SKHY | 19.25% | 週期高點的本益比壓縮，非真便宜 |
| MU | 15.25% | 同上，DRAM 第二階導數已見頂 |
| T | 9.98% | 唯一「真價值」但成長近零 |

> 結論：watchlist 裡目前**不存在**「成長在眼前、殖利率又高」的標的（9/3 就寫下此結論，今天再次印證）。

---

## 六、驗證記錄更新（誠實對照舊判斷）

| 舊判斷 | 今天的訊號 | 結論 |
|---|---|---|
| SNOW +16.6% 是敘事驅動（9/3） | 次日 -5.41%，回吐符合 | ⏳ 證偽條件第一天，偏「超買反彈」 |
| 9/3「升息敘事退潮」是趨勢反轉 | 9/4 高 Beta 熄火、30Y 翻回 | ⚠️ 傾向「死貓跳」 |
| 記憶體「循環見頂」觀察中 | MU/SKHY 噴發但廣度收縮 | ⏳ 證偽條件仍無法測試 |
| 30Y 供給驅動難以回落 | CPI/PPI 降溫仍不下 | ✅ 供給端判斷延續驗證 |

---

## 七、下週最該盯的三件事（時間框架標記，框架九）

1. **9/9 iPhone 18 發表會（5 天）** — AAPL 今天 -2.51%。定價若落在 Pro $1,199-1,299（+$100-200）是「敘事兌現」，超漲到 +$250-300 是「利空」。框架一「付錢方能否變現」在消費端的第一次大考。

2. **9/16 FOMC + 點陣圖（12 天）** —「鷹派維持不變」機率已收斂。最關鍵證偽是 **30Y 是否一週內跌破 5.0%**，決定「升息敘事退潮」真假。

3. **記憶體三檔未來 3 日的持守力** — 若從 +6~8% 高點回吐超過一半，印證「末日噴出」；守住則「結構續航」多一分證據。

---

## 八、一句話總結

今天的盤面把「AI 狹窄行情」演到極致——記憶體單一主線獨噴、其餘全線失血、30Y 殖利率頑固不下；三者疊加的結論是：9/3 的「升息敘事退潮」比較像壓力釋放的反彈而非趨勢反轉，真正的多空判決，留給五天後的 iPhone 定價和十二天後的 FOMC。

---

*非投資建議，僅為個人研究與分析紀錄。*
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日市場初判_盤後_2026-09-04_深度分析</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#EDF0F3; --sheet:#FFFFFF; --ink:#15202B; --ink-2:#5A6B7C;
  --rule:#C9D3DC; --grid:rgba(21,32,43,.045); --accent:#7C5CD6;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Noto Sans TC',system-ui,sans-serif;background:var(--paper);color:var(--ink);line-height:1.8;background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);background-size:28px 28px;padding:32px 20px 80px}
.wrap{max-width:880px;margin:0 auto;background:var(--sheet);border:1px solid var(--rule);padding:44px 52px 56px}
.meta{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2);padding-bottom:14px;border-bottom:2px solid var(--ink);margin-bottom:24px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
h1{font-size:26px;font-weight:700;letter-spacing:-.01em;margin:30px 0 14px;line-height:1.3}
h1:first-of-type{margin-top:0}
h2{font-size:20px;font-weight:700;margin:34px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--rule)}
h2:not(:first-of-type){border-top:3px solid var(--ink);padding-top:18px;border-bottom:none}
h3{font-size:16px;font-weight:600;margin:22px 0 8px;color:#2B4C6F}
p{margin:12px 0}
ul,ol{margin:12px 0 12px 24px}
li{margin:6px 0}
code{font-family:'IBM Plex Mono',monospace;font-size:.88em;background:#EDF1F4;padding:1px 5px;border-radius:3px}
blockquote{border-left:3px solid var(--accent);padding:6px 0 6px 16px;margin:16px 0;color:var(--ink);background:#F7F4FE;border-radius:0 4px 4px 0}
table{width:100%;border-collapse:collapse;margin:16px 0;font-size:14px}
th{background:#F2F5F8;text-align:left;padding:9px 12px;border-bottom:2px solid var(--rule);font-weight:600;font-size:12.5px}
td{padding:9px 12px;border-bottom:1px solid #EDF1F4}
td:nth-child(n+3){font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums}
tr:hover td{background:#F7F9FB}
hr{border:0;border-top:1px solid var(--rule);margin:30px 0}
.footnote{margin-top:30px;padding-top:14px;border-top:1px solid var(--rule);color:var(--ink-2);font-size:13px}
@media(max-width:640px){.wrap{padding:26px 20px 34px}body{padding:16px 10px 40px}table{font-size:12.5px}td{padding:7px 8px}}
</style>
</head><body>
<div class="wrap">
  <div class="meta"><span>每日初判_盤後_2026-09-04.md</span><span>深度分析（精修版）</span></div>
  <div id="content">載入中…</div>
  <div class="footnote">非投資建議，僅為個人研究與分析紀錄。</div>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
const md = __MD__;
document.getElementById('content').innerHTML =
  (window.marked ? marked.parse(md)
                 : '<pre>' + md.replace(/[&<>]/g, c =>
                     ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</pre>');
</script>
</body></html>"""


def main():
    out = os.path.join(_HERE, "每日初判_盤後_2026-09-04_深度分析.html")
    html = TEMPLATE.replace("__MD__", json.dumps(MD, ensure_ascii=False))
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已產生：{out}")
    print(f"大小：{os.path.getsize(out)} bytes")


if __name__ == "__main__":
    main()