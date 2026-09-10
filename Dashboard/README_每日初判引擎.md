# 每日市場初判引擎 — 設定與使用

「草稿+精修」模式的「草稿」端。每天自動產一份 Markdown 初判，
平淡的日子掃一眼即可；出現真正的訊號時，再把它帶進 Claude chat 深挖。

---

## 1. 這個引擎做什麼

1. 讀 `market_monitor` 產生的 `dashboard.html`（版本無關，只讀可見文字）
2. 接上你的分析框架（核心框架、觀察哨、預測驗證、knowledge 檔）
3. 用 Claude API 的即時 web_search 查「今天最大變動股的為什麼」
4. 產出一份帶確信度標記、防幻覺、落地到 watchlist 的 Markdown 初判

盤前與盤後用不同任務框架：
- `--mode premarket` 前瞻：隔夜怎麼走、今天盯什麼、關鍵日期倒數
- `--mode afterclose` 回顧+綜合：今天發生什麼、命中哪個框架、最大變動股的為什麼

---

## 2. 安裝

```bash
pip install -r requirements.txt
```

---

## 3. 先 dry-run（不花錢，強烈建議第一次先做這個）

```bash
python daily_brief.py --mode afterclose --dry-run
```

會把「要送給 Claude 的完整 system + user prompt」印出來，讓你審核框架有沒有餵對、
快照有沒有解析對。確認滿意再花錢跑真的。

---

## 4. 實際產出

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # 你的金鑰
python daily_brief.py --mode afterclose    # 盤後
python daily_brief.py --mode premarket     # 盤前
```

產出存到 `./briefs/每日初判_盤後_YYYY-MM-DD_HHMM.md`。

**成本估算**：每次輸入約 1 萬 tokens + 輸出約 2-3 千 + 幾次 web search。
用 `claude-sonnet-5` 一次大約 US$0.05-0.15；一天兩次 × 30 天約 US$3-9/月。
要更深可把 `daily_brief.py` 頂端 `MODEL` 改成 `claude-opus-4-8`（較貴）。
web search 每千次 $10，`MAX_SEARCHES` 預設 6，別開太大。

---

## 5. 每天自動跑：兩台機器方案（你尚未決定，這裡是評估）

引擎本身跟跑在哪台機器無關。兩個選項：

### 選項 A：GitHub Actions（推薦，免費、免顧機器）

在你的 repo 建 `.github/workflows/daily_brief.yml`：

```yaml
name: daily_brief
on:
  schedule:
    # UTC 時間。美股盤前 8:30am ET = 12:30 UTC（夏令）；盤後 4:30pm ET = 20:30 UTC
    - cron: "30 12 * * 1-5"   # 盤前，週一到週五
    - cron: "30 20 * * 1-5"   # 盤後，週一到週五
  workflow_dispatch: {}        # 允許手動觸發測試
jobs:
  brief:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      # 這裡要先跑你的 market_monitor 產生 dashboard.html：
      - run: python market_monitor_web.py --no-open --out dashboard.html
      - name: 決定 mode（12:xx UTC=盤前，其餘=盤後）
        id: m
        run: |
          H=$(date -u +%H)
          if [ "$H" -lt 16 ]; then echo "mode=premarket" >> $GITHUB_OUTPUT
          else echo "mode=afterclose" >> $GITHUB_OUTPUT; fi
      - run: python daily_brief.py --mode ${{ steps.m.outputs.mode }}
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      # 產出上傳成 artifact（或見下方 Drive 段落改推 Drive）
      - uses: actions/upload-artifact@v4
        with: { name: brief, path: briefs/ }
```

金鑰放 repo 的 Settings → Secrets → `ANTHROPIC_API_KEY`。
排程有幾分鐘抖動，不影響用途。

> 注意：cron 是 UTC，且不會自動處理美國日光節約時間。夏令(EDT)與冬令(EST)
> 差一小時，換季時記得調整，或在腳本內用 ET 判斷。

### 選項 B：你的 Mac（cron / launchd）

只有在「一台一直開著插電的 Mac」時才可靠——闔上蓋子的 MacBook 不會跑。
`crontab -e` 加入（時間為本機時區）：

```
30 8  * * 1-5  cd /path/to/engine && /usr/bin/python3 run_both.sh premarket
30 16 * * 1-5  cd /path/to/engine && /usr/bin/python3 run_both.sh afterclose
```

（`launchd` 可設喚醒，但仍需 Mac 沒睡死。桌機/Mac mini 才建議走這條。）

---

## 6. 存到 Google Drive（你選的輸出目的地）

這是整個專案唯一要花點功夫設定的地方。你 knowledge 裡記過
「Drive 連接器只能新建、不能覆蓋」——本引擎正好每天新建一個帶日期的檔名，
符合這個限制，不需要覆蓋。

無人排程要寫 Drive，需用 Google Drive API + OAuth refresh token（設定一次、永久有效）：
1. Google Cloud Console 建專案，啟用 Drive API
2. 建 OAuth 用戶端（Desktop），跑一次授權拿 refresh token
3. 在引擎的 `save_output` 之後加一段上傳到你的
   `03_預測記錄與驗證/` 或新建的 `每日初判/` 資料夾

要我幫你把 Drive 上傳那段寫好、接到 `save_output` 後面，跟我說一聲。
（建議等你先本機 dry-run + 跑一兩天真的、確認初判品質夠用，再設 Drive。）

---

## 7. 要調整的地方（都在 daily_brief.py 頂端設定區）

- `MODEL`：sonnet（日常）↔ opus（深度）
- `FRAMEWORK_FILES`：框架檔清單（換版本、加檔案就改這裡）
- `KEY_DATES`：關鍵追蹤日期，過期的會自動略過——記得定期更新
- `STANDING_THEMES`：持續追蹤的結構性主題
- system prompt（`_AFTERCLOSE` / `_PREMARKET`）：想調分析風格改這裡
