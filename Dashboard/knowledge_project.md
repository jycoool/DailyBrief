# 程式專案狀態與技術細節
最後更新：2026 年 8 月 29 日

---

## 環境
- 使用者電腦：Windows，資料夾 c:\Dashboard
- Python：同時有 Anaconda（python → C:\Users\jycoo\anaconda3）和 pythoncore-3.14
- `python` 指向 Anaconda；安裝套件用 `python -m pip`
- SSL 問題已用 fix_ssl.py 解決（合併 certifi + Windows 憑證庫 → ca_bundle.pem）
- yfinance 約 15 分鐘延遲；Yahoo Finance 付費訂閱不改善程式資料（走公開端點，不帶帳號憑證）
- stockanalysis.com 官方無 API（官方說明：無 API、無 MCP、資料僅顯示授權），watchlist 用 watchlist.txt 檔案同步

---

## 程式清單（最新版在 c:\Dashboard）

### 美股版
| 檔案 | 用途 | Port | 行數 |
|---|---|---|---|
| market_monitor.py | 終端機版 | — | ~635 |
| market_monitor_web.py | 網頁版（含重力線圖） | 7788 | ~884 |
| rotation_monitor.py | 輪動監控（RS 比值輪動燈） | — | ~496 |
| fix_ssl.py | SSL 憑證修復 | — | ~102 |
| watchlist.txt.example | 觀察清單範本 | — | ~21 |

### 台股版
| 檔案 | 用途 | Port | 行數 |
|---|---|---|---|
| twstock_monitor.py | 基礎版（三大法人為核心） | 7799 | ~600 |
| twstock_pro.py | 基本面擴充版（密集表格） | 7801 | ~849 |
| check_twse.py | 證交所 API 診斷（單一端點） | — | ~96 |
| check_twse_all.py | 證交所 API 診斷（全端點） | — | ~94 |

---

## 技術指標系統（market_monitor.py / web 版共用）

### 表格欄位
| 欄位 | 計算 | 著色 |
|---|---|---|
| RSI14 | Wilder RSI, 14 日 | >70 紅(超買) <30 綠(超賣) |
| 季RSI | Wilder RSI, 63 日（約一季） | 同上 |
| Stoch | 慢速隨機 %K (14,3,3) | <20 綠(超賣) >80 紅(超買) |
| MACD | (12,26,9) 柱狀方向 | ▲正(綠) ▼負(紅) |
| 相對量 | 當日量 ÷ 20 日均量 | ≥3x 紅(爆量) ≥2x 黃(放量) |
| 盈餘殖利率 | 1÷本益比 | 低於 30Y 公債標紅 |

### 單一訊號（在門檻警示區）
- Stochastic 自超賣回升：近 5 根 %K 曾 ≤20，現在 %K<40 且翻揚或上穿 %D
- MACD 黃金/死亡交叉：柱狀由負轉正/由正轉負
- 季 RSI 超買(>70)/超賣(<30)
- 爆量上漲/下跌/整理：≥3 倍量，配合漲跌方向
- 放量：2-3 倍量

### ★ 複合訊號（多條件同時成立，排最前面，觸發時抑制單一訊號）
1. **★資金進場**（綠）：爆量上漲(≥3x+漲>1%) AND Stochastic 自超賣回升 AND 年線乖離 ±2% 以內
2. **★資金出逃**（紅）：爆量下跌(≥3x+跌>1%) AND 跌破年線(dev≤-5%) AND MACD 死亡交叉
3. **★變盤前兆**（黃）：爆量整理(≥3x+價平±1%)

### 重力線圖（僅 web 版）
把每檔持股的盈餘殖利率標在 30Y 公債殖利率的虛線上下。
線下方紅色警戒區 = 承擔股票風險卻拿不到公債收益率。
注意：盈餘殖利率未計入成長，線下不等於「爛股票」，而是「估值最依賴成長兌現」。

---

## 輪動監控（rotation_monitor.py，2026/08 新增）
獨立模組，與 market_monitor 共用 SSL bundle 與 watchlist.txt。輪動只顯形於比值，
本模組把 9 組配對相除成相對強弱線（RS = 分子/分母），量化領導權換手。分析面對應框架十。

### 追蹤的 9 組配對
SMH/SPY（晶片vs大盤·主心臟）、MU/SMH（記憶體vs半導體·金絲雀）、RSP/SPY（等權vs市值權·集中度）、
XLU/SPY（電力vs大盤）、SMH/XLU（算力vs電力）、QQQ/IWD（成長vs價值）、IWM/SPY（小型vs大盤）、
XLY/XLP（非必需vs必需·風險胃納）、SMH/QQQ（晶片vs科技整體）

### 每配對計算
- 50 日均線多空位置、20 日動能(%)、線性回歸斜率
- 翻號：近 5 日內 RS 線穿越長均線（= 領導權換號，最該注意）
- 連續同側天數（持續性）、60 日新高/新低
- 訊號燈：🟢領先↑ / 🔴落後↓ / 🟡剛翻多空 / ⚪盤整
- 避險腿 TMF/GLDM/SGOV/^VIX：看自身 20 日動能（科技下跌日同步走強＝資金換防禦）

### 判讀紀律（三步確認一次真輪動）
①翻號 → ②連續同側 ≥3 日（濾單日雜訊）→ ③廣度同向（變廣配 risk-on／變窄配 risk-off）。
三者齊備才動作。翻號只是候選訊號，不是行動訊號。

### 參數（檔案頂端可調）
MA_SHORT=20、MA_LONG=50、MOM_LB=20、MOM_THRESH=2.0、FLIP_LB=5、NH_LB=60

### 執行
`python rotation_monitor.py [--console] [--no-open] [--lookback N]`
產生 rotation.html 並開啟；--console 只印文字。收盤跑一次即可。

### 已知限制
- 廣度是 watchlist 16 檔代理（站上 50/200 日比例），非全市場真廣度
- 沙盒無法連 Yahoo 測資料路徑；計算/渲染邏輯已用合成資料驗證通過

---

## 證交所 API 端點（已驗證）

### openapi.twse.com.tw（每交易日快照，盤中/非交易時段回空白）

**估值**
`https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL`
欄位：Date, Code, Name, PEratio, DividendYield, PBratio（1082 筆）

**月營收**
`https://openapi.twse.com.tw/v1/opendata/t187ap05_L`
欄位：公司代號, 營業收入-當月營收, 營業收入-上月比較增減(%), 營業收入-去年同月增減(%), 資料年月（1082 筆）

**損益表（六個行業別，全抓才涵蓋所有上市）**
`https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{ci|basi|bd|fh|ins|mim}`
- ci=一般業(271筆), basi=金融業, bd=證券期貨業, fh=金控業, ins=保險業, mim=異業
- 欄位（注意全形括號）：營業收入, 營業毛利（毛損）淨額, 本期淨利（淨損）, 基本每股盈餘（元）, 年度, 季別
- 毛利率 = 營業毛利÷營業收入；稅後純益率 = 本期淨利÷營業收入
- 金融股無「營業毛利」→ 毛利率顯示 —

### www.twse.com.tw（官網 API，可指定日期，自動回溯）

**三大法人**
`https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=json`
收盤後更新。單位「股數」，程式換算成「張」(÷1000)。
外資 = 外陸資(不含外資自營商) + 外資自營商

**除權息**
`https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate=&endDate=&response=json`

### 踩坑紀錄
- openapi 只有前一交易日，盤中回 200 但空白/HTML（不是故障）
- t187ap04_L 是「重大訊息」不是除權息（第一筆是公司更名公告）
- 欄位全形括號 `營業毛利（毛損）`，程式用 _norm()/_find() 模糊匹配
- 上櫃(.TWO)走 TPEx 另一組端點，目前版本以上市為主
- Yahoo 代號：INTEL→INTC, BRK.B→BRK-B, 上櫃用 .TWO（原相3227/威剛3260/頎邦6147/鈊象3293）
- 民國年：1150807 → 2026/08/07（前三碼+1911）

---

## watchlist.txt 格式
```
# 井字號是註解
AAPL
NVDA
MSFT, 微軟
TSM, 台積電 ADR
BRK-B
```
一行一檔，代號在前。逗號後是名稱（可省略）。沒有逗號時，同一行多個代號自動拆開。
market_monitor.py 和 web 版都支援。目前 --serve 模式只在啟動時讀一次（待改善）。

---

## Google Drive 備份（2nd Brain）
- Market Monitor Project/ ID: 1HXb70zhlACpE7riNB9n9oZInZt2gq4yP
- 01_程式碼/ ID: 1ebAs_MieufNv1EpJ3bspxR4xN5kTDYb6
- 台股版/ ID: 1tmgGveJJ5Vgb-DvXJkgfcW8zDgoCMOyG
- 02_研究報告/ ID: 1ri3EyDjtFdIpObA0k_mSSOk-mVhxxCeO
- 03_預測記錄與驗證/ ID: 1phkUkzYgErZtb4rcw7KkV3s79Z5y7Nyl
- Drive 連接器無法「覆蓋」現有檔案，只能新建。textContent 上傳可靠，base64Content 會損壞
- 大檔建議用戶手動拖曳上傳

---

## 待辦
### 程式面
- [ ] 上櫃股(TPEx)官方基本面端點
- [ ] --serve 模式每次更新重讀 watchlist.txt
- [ ] ROE（需解析資產負債表）
- [ ] 連買/連賣天數（累積多日法人資料）
- [ ] 券商 API（IBKR/Schwab）取代 yfinance 延遲
- [ ] 網頁版資料日期標示改進
- [ ] SGOV 等現金型 ETF 技術訊號過濾
- [ ] Drive 同步：web 版和 pro 版最新程式碼尚未上傳
- [ ] rotation_monitor：接全 S&P 成分股算真廣度（取代 watchlist 代理）
- [ ] rotation_monitor：台股外資流輪動（電子 vs 金融/傳產，接 twstock_monitor 三大法人）
- [ ] rotation_monitor：--serve 常駐模式
- [ ] rotation_monitor.py 上傳 Drive 備份

### 分析面
- [ ] 8/21 Apple 回覆參議員 CXMT 最後期限
- [ ] 9/9 iPhone 18 發表會定價驗證
- [ ] 9/16 FOMC + 點陣圖
- [ ] TMF 後續追蹤
- [ ] AMD Taalas「推論架構轉變」長期主題
- [ ] 「預測記錄與驗證表」更新
