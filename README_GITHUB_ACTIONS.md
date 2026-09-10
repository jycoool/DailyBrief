# 用 GitHub Actions 不用 24 小時開機：每日初判 + 市場儀表板

> 不想 24 小時開機？用 GitHub Actions 的免費排程即可。這份文件是自包含的設定步驟。

## 兩個自動化（各有自己的 workflow）

| workflow 檔 | 做的事 | 多久跑 |
|---|---|---|
| `.github/workflows/daily_brief.yml` | 盤前/盤後產生「每日初判」→ 存檔 + 上傳 Google Drive + 反哺知識庫 | 每週一到五 2 次（盤前/盤後） |
| `.github/workflows/market_monitor_pages.yml` | 抓市場資料 → 重產 `dashboard.html` → commit 回 repo → GitHub Pages 呈現 | 每 30 分鐘（美股開盤時段） |

## 一句話原理

GitHub Actions 會**免費**在排程時間起一台 ubuntu 虛擬機，`pip install` 依賴 → 跑對應的 Python 腳本
（抓快照 → 生成 → 存檔 → 上傳 Drive / commit HTML），產出能透過 artifact 或 GitHub Pages 取回。
你**不需要本機常駐**。

---

## 設定步驟（照順序，全部只需做一次）

### ① 建立 GitHub repo 並 push

```bash
cd /workspaces/agent-sandbox
git init
git add .
git commit -m "初判引擎 + GitHub Actions 排程"
# 到 github.com 先「New repository」建立空 repo（不要勾 Initialize），記下 URL
git remote add origin https://github.com/你的帳號/你的repo.git
git branch -M main
git push -u origin main
```

> 你目前在沙盒環境，`git remote` 尚未設定（這是正常的）。push 後，`.github/workflows/daily_brief.yml` 就會被 GitHub 讀到。

### ② 設定 GitHub Secrets（重要：機密放這，不進程式碼）

到你的 repo → **Settings → Secrets and variables → Actions → New repository secret**，
逐個新增下面這些（名稱「必須」完全一致）：

| Secret 名稱 | 值 |
|---|---|
| `ANTHROPIC_API_KEY` | 你的 Claude API key（**或**用 NVIDIA 那個） |
| `NVIDIA_API_KEY` | 你的 NVIDIA NIM key（**選填**，程式優先走 NVIDIA） |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | 你 `.env` 裡那串 `1//...` |
| `GOOGLE_DRIVE_CLIENT_ID` | `....apps.googleusercontent.com` 那串 |
| `GOOGLE_DRIVE_CLIENT_SECRET` | `GOCSPX-...` 那串 |

> 直接從你本機的 `Dashboard/.env` 把這三行 Google 值貼進對應的 secret 即可。
> ⚠️ 絕對不要 `git add .env` 或把值寫進 workflow 檔——`.gitignore` 已擋，但還是要小心。

### ③ 手動觸發測試一次

repo → **Actions** → 左側 `daily_brief` → **Run workflow** → Run。
跑完點進去看 log：若印出 `[Drive] 已備份 ... → file_id=...` 就代表全通了。

---

## 排程時間說明（重要）

GitHub 的 cron 是 **UTC**，且**不會自動處理美國日光節約**：

| 模式 | 美東（夏令 EDT） | UTC（夏令） | workflow 現值 |
|---|---|---|---|
| 盤前 | 08:30 ET | 12:30 UTC | `30 12 * * 1-5` |
| 盤後 | 16:30 ET | 20:30 UTC | `30 20 * * 1-5` |

- `1-5` = 週一到週五（美股交易日，週末不跑）。
- **換季時**（3 月第二個週日轉夏令、11 月第一個週日轉冬令）要把小時 ±1：
  - 冬令（EST）：盤前 `30 13 * * 1-5`、盤後 `30 21 * * 1-5`。

> 進階做法：可以在 step 裡用 `TZ=America/New_York` 跑 bash `date` 判斷，讓時區自動正確，
> 避免手動換季。需要我加的話說一聲。

---

## 每次執行會產生什麼

1. `Dashboard/briefs/每日初判_盤後_YYYY-MM-DD_HHMM.md`（或盤前）— 初判文稿
2. `Dashboard/briefs/brief_latest.html` — 預覽 HTML
3. **Google Drive「每日初判」資料夾** — 同名 md 雲端備份（每日新增）
4. `Dashboard/knowledge_slim.md` — 若報告含新觀點則追加（反哺知識庫）
5. **GitHub artifact**（`brief-*-<run_id>`）— 上傳的產出，保留 14 天，Drive 失敗也能撈回

---

## 常見問題

- **免費額度**：GitHub 公有 repo 免費；私有 repo 每月 2000 分鐘（跑一次約 3~5 分鐘，綽綽有餘）。想保密就用私有 repo。
- **Drive 沒上傳成功**：檢查三項 `GOOGLE_DRIVE_*` secret 是否齊全、refresh token 是否 7 天到期（若是 Testing 狀態拿的）。改 In production 後重拿一次可永久。
- **model 沒產出**：確認 `ANTHROPIC_API_KEY` 或 `NVIDIA_API_KEY` secret 有值；沒有 key 會退回「本地草稿」。

---

## 市場儀表板 → GitHub Pages（公網隨時看）

`market_monitor_web.py` 會把市場資料寫成**一個自包含的 `dashboard.html`**（538 行，CSS/JS 全內嵌，
只引用 Google Fonts CDN）。所以只要把它放到 GitHub Pages，瀏覽器開啟就正常顯示，不用額外檔案。

### 設定步驟（只做一次）

1. **開啟 Pages**：repo → Settings → Pages → **Build and deployment** 的 Source 選
   **Deploy from a branch**，branch 選 `main`、資料夾選 **`/ (root)`** → Save。
   幾分鐘後網址會是 `https://<你的帳號>.github.io/<repo>/`。

2. **讓 root 首頁指向 dashboard**：因為 `dashboard.html` 在 `Dashboard/` 子資料夾，最簡單是把
   根目錄放一個 `index.html` 跳轉到儀表板。若你已經有其它 index.html，可改成放一個 `.nojekyll`
   並調整 path（見下方說明）。<b>需要我幫你建一個根目錄 `index.html` 跳轉頁，跟我說一聲即可。</b>

3. **手動測一次**：repo → Actions → 左側 `market_monitor_pages` → Run workflow。
   跑完後到 Settings → Pages 看部署狀態，或直接開 `https://<帳號>.github.io/<repo>/dashboard.html`。

### 這個 workflow 每次做什麼

1. 抓 yfinance 資料（`market_monitor_web.py --no-open --out dashboard.html`）
2. 若 HTML 內容有變 → `git commit` 回 `main`（`permissions: contents: write` 已設好）
3. GitHub Pages 偵測到 commit → 自動重新部署

### 兩點注意

- **cron 目前是 `*/30 13-20 * * 1-5`（UTC）** = 美東 09:30~16:00 每 30 分鐘、週一到五。
  冬令時請改成 `*/30 14-21 * * 1-5`。想更靠近「盤中即時」可加大頻率，但受免費額度與
  yfinance 約 15 分鐘延遲限制，30 分鐘已足夠。
- **`dashboard.html` 已從 `.gitignore` 放行**，這樣 Actions 才能 commit 它給 Pages 用。
  但它**不是機密**（純資料快照＋樣式），可放心進版控。