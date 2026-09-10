## 每天晚上 8 點（美東）自動產出盤後報告

> 本頁說明如何讓 `daily_brief.py` 每晚 20:00 ET 自動跑一份盤後初判，
> 並在發現新觀點時自動反哺知識庫（knowledge_slim.md）。

### 為什麼用「常駐排程」而不是 cron

`daily_brief.py --schedule` 內建一個常駐迴圈，與 cron 相比：

| | cron | --schedule 常駐 |
|---|---|---|
| 時區/日光節約 | 要手動調（README 已警告過） | zoneinfo 自動處理 America/New_York |
| 週末跳過 | 要寫 cron 條件 | 內建（週一~五才跑） |
| 失敗重試 | 有，但要等下一 cycle | 立即報錯、下一天自動再試 |
| 依賴 | 要額外安裝 cron | 零依賴 |

### 啟動（一次性）

```bash
cd /workspaces/agent-sandbox/Dashboard
nohup bash run_daily_brief.sh >> briefs/daily_brief.log 2>&1 &
```

在你的 Mac（`crDashboard` 資料夾）上：

```bash
cd ~/Desktop/c\Dashboard   # 或你的實際路徑
nohup bash run_daily_brief.sh >> briefs/daily_brief.log 2>&1 &
```

> Mac 闔上蓋子會睡，若要真·每天跑，建議用桌機 / Mac mini（見 README_每日初判引擎.md 的選項 B）。

### 保活（選用）：用 cron 每小時確認進程還在

執行 `crontab -e` 加入（本機時區）：

```
0 * * * *  pgrep -f "daily_brief.py --mode afterclose --schedule" >/dev/null || (cd /path/to/Dashboard && nohup bash run_daily_brief.sh >> briefs/daily_brief.log 2>&1 &)
```

### 手動立刻跑一次（不排程）

```bash
cd /workspaces/agent-sandbox/Dashboard
python daily_brief.py --mode afterclose          # 盤後，抓全新快照
python daily_brief.py --mode afterclose --dry-run # 先審核 prompt 不花錢
```

### 產出與回饋

- 報告：`briefs/每日初判_盤後_YYYY-MM-DD_HHMM.md`
- 每次跑完，若報告含「## 驗證記錄更新」或「## 新觀點」區塊，會自動
  把新觀點合併進 `knowledge_slim.md`，並更新「最後更新」日期。
- 想讓 Claude 深度分析，把報告（草稿）帶進 chat 追問即可。