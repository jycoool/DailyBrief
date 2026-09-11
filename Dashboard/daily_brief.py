#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_brief.py — 每日市場初判引擎（草稿＋精修 的「草稿」端）

把 market_monitor.py 的資料快照接上分析框架，產出一份帶確信度標記、
防幻覺、落地到 watchlist 的 Markdown 初判。

兩種工作模式：
  1. API 模式：把快照＋框架丟給 LLM 生成初判。
     - 有 NVIDIA_API_KEY → 走 NVIDIA NIM（OpenAI 相容介面，優先）。
     - 有 ANTHROPIC_API_KEY → 走 Claude（退回選項）。
  2. 本地模式（無 key，沙盒/離線）：用內建規則產生一份結構化草稿，
     供人類帶進 Claude chat 深挖（「草稿＋精修」的人工端）。

用法：
    python daily_brief.py --mode afterclose            # 盤後
    python daily_brief.py --mode premarket             # 盤前
    python daily_brief.py --mode afterclose --dry-run  # 先印 prompt 審核（不花錢）
    python daily_brief.py --snapshot ./snapshot.txt    # 指定既有快照（跳過抓資料）

產出：
    ./briefs/每日初判_盤後_YYYY-MM-DD_HHMM.md  （或 盤前）
    ./briefs/brief_latest.html                 （搭配 open_brief.py 開啟）

環境變數：
    ANTHROPIC_API_KEY    Claude API key（optional；無 NVIDIA key 時的退回選項）
    ANTHROPIC_MODEL      預設 claude-sonnet-5（想更深改 claude-opus-4-8）
    NVIDIA_API_KEY       NVIDIA Build / NIM API key（optional；有則優先走 NVIDIA）
    NVIDIA_MODEL         預設 nvidia/llama-3.3-nemotron-super-49b-v1
    NVIDIA_BASE_URL      預設 https://integrate.api.nvidia.com/v1

    Google Drive 備份（選用，見 drive_backup.py）：
    GOOGLE_DRIVE_ENABLED       設 1 才啟用備份（預設關閉，安靜跳過）
    GOOGLE_DRIVE_REFRESH_TOKEN 授權拿到的 refresh token
    GOOGLE_DRIVE_CLIENT_ID     OAuth client id
    GOOGLE_DRIVE_CLIENT_SECRET OAuth client secret
    GOOGLE_DRIVE_FOLDER_NAME   目標子資料夾名稱（預設「每日初判」，缺則自動建）
    GOOGLE_DRIVE_FOLDER_ID     也可直接指定父資料夾 ID（選用）
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRIEFS = os.path.join(_HERE, "briefs")

# 載入同資料夾的 .env（若存在），讓 GOOGLE_DRIVE_ENABLED 等開關在判斷前就緒。
# 複用 drive_backup._load_dotenv 的解析邏輯，避免重複實作。
try:
    import drive_backup as _drive_backup_mod
    _drive_backup_mod._load_dotenv(os.path.join(_HERE, ".env"))
except Exception:
    pass

# ── 設定區 ──────────────────────────────────────────────────
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

# NVIDIA NIM（OpenAI 相容介面）設定
NVIDIA_MODEL = os.environ.get(
    "NVIDIA_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1")
NVIDIA_BASE_URL = os.environ.get(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
# 分析框架檔：換版本/加檔案就在這裡改
FRAMEWORK_FILES = [
    "knowledge_analysis.md",
    "knowledge_project.md",
    "README_每日初判引擎.md",
]
# 關鍵追蹤日期（過期自動略過）
KEY_DATES = [
    ("2026-09-16", "FOMC 會議 + 點陣圖（升息本身已消化，重點看點陣圖未來路徑訊號）"),
]
# 持續追蹤的結構性主題
STANDING_THEMES = [
    "AI 資本支出「何時兌現」（訂單本身不是問題，變現時間表與毛利率結構才是）",
    "30Y 長端供應/term premium 壓力（1.45 兆赤字 + 貝森特工具箱只能拖延）",
    "記憶體循環見頂觀察（MU/SKHY 高殖利率是週期高點陷阱）",
    "市場廣度：高度集中、AI 主導的狹窄領導結構",
    "折現率壓力 vs 盈餘殖利率（30Y 5.27% 這把尺）",
    "中東雙海峽瓶頸（荷莫茲海峽 + 葉門紅海港口/勝利港被占）→ 油價/柴油尾部風險",
    "Oracle 收入證明門檻通過 vs CapEx 三倍燒錢疑慮（框架一付錢方驗證）",
    "Apple 摺疊機 Duo 作為毛利率緩衝的策略能否兌現（下次財報見真章）",
]
# ── 收盤分析（web search 原因）設定 ──────────────────────────
# 自動對「今日變動最大的前 N 檔」查 Yahoo Finance 新聞，補足「為什麼」。
# 免費、免 API key；沙盒須能連 finance.yahoo.com（連不上會優雅降級，不噴錯）。
MAX_NEWS_SYMBOLS = int(os.environ.get("BRIEF_MAX_NEWS", "4"))   # 最多查幾檔新聞
NEWS_PER_SYMBOL = int(os.environ.get("BRIEF_NEWS_PER", "3"))     # 每檔抓幾條新聞標題
NEWS_TIMEOUT = int(os.environ.get("BRIEF_NEWS_TIMEOUT", "12"))   # 每檔 request 秒數
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
NEWS_HEADERS = {"User-Agent": "Mozilla/5.0"}                    # 避免被擋

# 觀察清單個股的「為什麼」備註（來自 knowledge 檔的歷史判斷，避免幻覺）
TICKER_NOTES = {
    "NVDA": "財報 beat & raise；Argus 目標價 280→310；有現金流現貨撐溢價",
    "MU": "記憶體循環高點；盈餘殖利率是週期陷阱；上行第二階導數已放緩",
    "SKHY": "HBM 市占稀釋但仍龍頭；HBM4 拿 2/3 份額；盈餘殖利率 21% 非週期常態",
    "MRVL": "Google 訂單兌現推到 2029；毛利率結構偏低；84x 本益比待消化",
    "AVGO": "客製晶片變現速度是關鍵；護城河裂縫（Google 分散單到 Marvell）未癒合，反彈不能算數，需重拿訂單能見度",
    "AAPL": "iPhone 18 Pro/ProMax 只漲 $100（Gurman 下緣）；摺疊 Duo $1999-3199 當毛利率緩衝；毛利率 47-48% 待財報驗證",
    "ORCL": "OCI 營收翻倍、現金流 +184%（收入證明門檻通過）；但 CapEx 85→285 億燒錢疑慮未解",
    "MSFT": "Azure backlog 強但現金流/資產負債表是第二關；Magnificent Seven 之一",
    "GOOGL": "Alphabet 2,050 億對應營收說不清（7 月被打）；AI 算力收錢方",
    "TSLA": "純敘事驅動；Cybercab 9/3；盈餘殖利率 0.61% 最極端",
    "INTC": "增發稀釋從 150 億上調 200 億；年線乖離極大",
    "TMF": "3 倍槓桿結構耗損；殖利率回落 vs 升息機率九成兩股拔河，勿當趨勢確立",
    "TSM": "台積電 ADR；AI 算力鏈「立即落地出貨」端",
}
# ─────────────────────────────────────────────────────────────

# ANSI 顏色/控制碼（market_monitor 終端輸出會帶，寫進 md 前要清掉）
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    """移除 ANSI 轉義碼，回傳乾淨文字。"""
    return ANSI_RE.sub("", text)

def fetch_stock_news(symbol, count=NEWS_PER_SYMBOL):
    """用 Yahoo Finance search API（免 key）抓某檔的近期新聞標題，回傳 list[dict]。
    回傳每則 {title, publisher, date}。連線失敗時回傳 []（優雅降級）。
    """
    try:
        import requests  # 延遲 import，market_monitor 原本就依賴
        url = (f"{YAHOO_SEARCH_URL}?q={symbol}"
               f"&newsCount={count}")
        r = requests.get(url, headers=NEWS_HEADERS, timeout=NEWS_TIMEOUT)
        if r.status_code != 200:
            return []
        out = []
        for n in r.json().get("news", [])[:count]:
            title = (n.get("title") or "").strip()
            if not title:
                continue
            ts = n.get("providerPublishTime")
            date = ""
            if ts:
                try:
                    date = datetime.fromtimestamp(ts).strftime("%m-%d")
                except Exception:
                    date = ""
            out.append({"title": title,
                        "publisher": n.get("publisher") or "",
                        "date": date,
                        "link": n.get("link") or ""})
        return out
    except Exception:
        return []


_SNAP_ROW = re.compile(
    r"^\s{2}(?P<sym>[A-Z0-9\.\-]{1,8})\s+(?P<name>.+?)\s{2,}"
    r"(?P<price>[\d,]+\.\d+)\s+(?P<chg>[+\-]?\d+\.\d+%)\s+"
    r"(?P<dev>[+\-]?\d+\.\d+%|n/a)\s+(?P<rsi>\d+|n/a)\s+(?P<qrsi>\d+|n/a)\s+"
    r"(?P<stoch>\d+|n/a)\s+(?P<macd>[▲▼])\s+(?P<relvol>[\d\.]+x|n/a)\s+"
    r"(?P<ey>[\d\.]+%|n/a)\s*$"
)


def parse_snapshot(snapshot):
    """解析 market_monitor『個股觀察清單』，回傳 dict[symbol] -> row（chg/dev/rsi/relvol/ey）。"""
    rows = {}
    for line in snapshot.splitlines():
        m = _SNAP_ROW.match(line)
        if not m:
            continue
        def f(v, d=None):
            try:
                return float(v.replace("%", "").replace(",", ""))
            except (ValueError, AttributeError):
                return d
        rv = m.group("relvol")
        rows[m.group("sym")] = {
            "sym": m.group("sym"),
            "chg": f(m.group("chg"), 0.0),
            "dev": f(m.group("dev")),
            "rsi": f(m.group("rsi")),
            "relvol": f(rv.replace("x", "")) if rv != "n/a" else None,
            "ey": f(m.group("ey")),
        }
    return rows


def parse_macro(snapshot):
    """解析『總經與市場指標』，回傳 dict[指標名] -> 浮點數。"""
    out, inblk = {}, False
    for line in snapshot.splitlines():
        if "【總經與市場指標】" in line:
            inblk = True; continue
        if inblk:
            if "【" in line:
                break
            m = re.match(r"^\s{2}(.+?)\s{2,}([\d,\.]+)\s+([+\-]?\d+\.\d+)%\s*$", line)
            if m:
                try:
                    out[m.group(1).strip()] = float(m.group(2).replace(",", ""))
                except ValueError:
                    pass
    return out


def build_close_analysis(snapshot):
    """規則引擎『收盤分析』：解析快照→漲跌榜＋估值體檢＋曝險＋web search 新聞。
    免 API key；新聞走免費 Yahoo Finance search API。回傳 Markdown 區段文字。
    """
    rows = parse_snapshot(snapshot)
    macro = parse_macro(snapshot)
    if not rows:
        return ("\n## 收盤分析\n\n"
                "> 快照解析不到個股明細（抓失敗或格式變動），收盤分析略過。\n")

    movers = sorted(rows.values(), key=lambda r: abs(r["chg"] or 0),
                    reverse=True)[:MAX_NEWS_SYMBOLS]
    y30 = next((v for k, v in macro.items() if "30年期" in k), None)

    buf = ["\n## 收盤分析（引擎＋web search 原因）\n",
           "> 規則引擎自動產出：漲跌榜/估值體檢/曝險，並對『變動最大前 "
           f"{len(movers)} 檔』用 Yahoo Finance 新聞補『為什麼』。新聞免 key，僅作錨點。\n"]

    # 一、最大變動榜 + 新聞原因
    buf.append("\n### 一、最大變動榜（含 web search 新聞原因）\n")
    for r in movers:
        arrow = "▲" if r["chg"] >= 0 else "▼"
        rv = f"{r['relvol']:.1f}x" if r.get("relvol") is not None else "n/a"
        buf.append(f"\n**{r['sym']}** {arrow} {r['chg']:+.2f}%（量 {rv}）\n")
        news = fetch_stock_news(r["sym"], NEWS_PER_SYMBOL)
        if not news:
            buf.append("  - [新聞] （連線失敗或無新聞，略過）\n")
        for n in news:
            pub = (f"（{n['publisher']} {n['date']}）" if n.get("publisher")
                   else "")
            buf.append(f"  - [新聞] {n['title']}{pub}\n")

    # 二 / 三：Top 漲跌
    up = sorted([r for r in rows.values() if r["chg"] > 0],
                key=lambda r: r["chg"], reverse=True)
    down = sorted([r for r in rows.values() if r["chg"] < 0],
                  key=lambda r: r["chg"])
    def ey_txt(r):
        return f"{r['ey']:.2f}%" if r.get("ey") is not None else "n/a"
    buf.append("\n\n### 二、上漲前段（Top）\n")
    for r in up[:5]:
        buf.append(f"- **{r['sym']}** +{r['chg']:.2f}%（盈餘殖利率 {ey_txt(r)}）\n")
    buf.append("\n\n### 三、下跌前段（Top）\n")
    for r in down[:5]:
        buf.append(f"- **{r['sym']}** {r['chg']:.2f}%（盈餘殖利率 {ey_txt(r)}）\n")

    # 四、估值體檢
    buf.append(f"\n\n### 四、估值體檢（盈餘殖利率 vs 30Y 公債 {y30 if y30 else '—'}%）\n")
    if y30:
        below = sorted([r for r in rows.values()
                        if r.get("ey") is not None and r["ey"] < y30],
                       key=lambda r: r["ey"])
        above = sorted([r for r in rows.values()
                        if r.get("ey") is not None and r["ey"] >= y30],
                       key=lambda r: r["ey"], reverse=True)
        buf.append(f"\n**低於 30Y {y30}%（風險/報酬倒掛）：**\n")
        for r in below:
            buf.append(f"- {r['sym']}：{r['ey']:.2f}%（今日 {r['chg']:+.2f}%）\n")
        buf.append(f"\n\n**高於 30Y {y30}%：**\n")
        for r in above:
            buf.append(f"- {r['sym']}：{r['ey']:.2f}%（今日 {r['chg']:+.2f}%）\n")
    else:
        buf.append("\n> 快照中找不到 30 年期公債殖利率，估值體檢略過。\n")

    # 五、年線乖離曝險掃描
    buf.append("\n\n### 五、年線乖離曝險掃描\n")
    big_pos = sorted([r for r in rows.values() if (r["dev"] or 0) >= 20],
                     key=lambda r: r["dev"], reverse=True)
    big_neg = sorted([r for r in rows.values() if (r["dev"] or 0) <= -5],
                     key=lambda r: r["dev"])
    if big_pos:
        buf.append("\n**高於年線 ≥+20%（乖離偏大，留意拉回）：**\n")
        for r in big_pos:
            buf.append(f"- {r['sym']}：年線 {r['dev']:+.1f}%\n")
    if big_neg:
        buf.append("\n\n**低於年線 ≤-5%（已跌破，警惕）：**\n")
        for r in big_neg:
            buf.append(f"- {r['sym']}：年線 {r['dev']:+.1f}%\n")

    buf.append("\n\n> ⚠️ 自動規則引擎產出；新聞僅為『原因錨點』，更深入的因果與防幻覺需人工或 Claude API 精修。\n")
    return "".join(buf)


def load_framework():
    """讀入框架檔，串成要丟給模型的背景。"""
    buf = []
    for name in FRAMEWORK_FILES:
        p = os.path.join(_HERE, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                buf.append(f"\n===== 檔案：{name} =====\n{f.read()}")
    return "".join(buf)


def run_market_monitor(out_path=None):
    """呼叫 market_monitor.py 抓一份快照，回傳 stdout 文字。"""
    script = os.path.join(_HERE, "market_monitor.py")
    # 優先使用 .venv 的 python（沙盒）；否則退回系統 python3/python
    for py in (os.path.join(_HERE, ".venv", "bin", "python"), "python3", "python"):
        try:
            r = subprocess.run([py, script], capture_output=True, text=True,
                               timeout=180, cwd=_HERE)
            if r.returncode == 0 and r.stdout.strip():
                if out_path:
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(r.stdout)
                return r.stdout
        except Exception:
            continue
    return None


def get_snapshot(args):
    """取得快照：優先 --snapshot 指定檔，否則現跑 market_monitor。"""
    if args.snapshot and os.path.exists(args.snapshot):
        with open(args.snapshot, encoding="utf-8") as f:
            return strip_ansi(f.read())
    print("  [1/3] 抓取 market_monitor 快照...")
    snaps = os.path.join(_HERE, "snapshot.txt")
    snap = run_market_monitor(snaps)
    if snap is None:
        snap = "（抓取失敗：沙盒無法連 Yahoo，或 market_monitor 未安裝套件。）"
    return strip_ansi(snap)


def build_prompt(mode, snapshot):
    """組出要送給 Claude 的 system + user prompt。"""
    framework = load_framework()
    if mode == "premarket":
        task = (
            "你是盤前展望。根據下方快照＋框架，產出一份「盤前初判」：\n"
            "1. 隔夜怎麼走、外部（殖利率/美元/日圓/油價）訊號\n"
            "2. 今天要盯的三件事\n"
            "3. 關鍵日期倒數（從 KEY_DATES 挑尚未過期的）\n"
            "4. 落地到 watchlist 的具體看點\n"
            "用繁體中文，第一人稱研判口吻，帶確信度標記（高/中/低）與防幻覺（沒資料就明說）。"
        )
    else:
        task = (
            "你是盤後回顧。根據下方快照＋框架，產出一份「盤後初判」：\n"
            "1. 今天真正重要的三件事（先濾雜訊）\n"
            "2. 每件事的因果鏈拆解（對照框架裡的歷史判斷，標記印證/更新）\n"
            "3. 估值體檢：盈餘殖利率 vs 30Y 公債\n"
            "4. 組合曝險掃描（同一因子的集中風險）\n"
            "5. 落地到 watchlist 的具體動作\n"
            "6. 驗證舊判斷小結\n"
            "7. 一句話總結\n"
            "用繁體中文，第一人稱研判口吻，帶確信度，防幻覺（沒資料就明說）。"
        )
    system = (
        f"你是我的個人市場研究助理，負責每天產出「每日市場初判」。\n"
        f"模型：{MODEL}。以下是我累積的分析框架（歷史判斷，供對照是否仍成立）：\n"
        f"{framework}\n"
        f"持續主題：\n" + "\n".join(f"- {t}" for t in STANDING_THEMES) + "\n"
        f"關鍵日期：\n" + "\n".join(f"- {d} {e}" for d, e in KEY_DATES)
    )
    user = f"{task}\n\n===== 今日 market_monitor 快照 =====\n{snapshot}"
    return system, user


def save_output(mode, text):
    """存成 briefs/每日初判_*.md，並呼叫 open_brief.py 產生 HTML。"""
    os.makedirs(_BRIEFS, exist_ok=True)
    ts = _now_et().strftime("%Y-%m-%d_%H%M")
    tag = "盤後" if mode == "afterclose" else "盤前"
    fname = f"每日初判_{tag}_{ts}.md"
    path = os.path.join(_BRIEFS, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  已產出：{os.path.relpath(path, _HERE)}")
    # 嘗試轉 HTML（用 open_brief.py，--no-open 不開瀏覽器）
    try:
        subprocess.run([sys.executable, os.path.join(_HERE, "open_brief.py"),
                        "--no-open"], cwd=_HERE, timeout=30)
    except Exception:
        pass
    # 選用：備份到 Google Drive（需先設好認證，見 drive_backup.py）
    try_backup_to_drive(path)
    return path


def try_backup_to_drive(path):
    """把剛產出的初判備份到 Google Drive（選用）。

    只在明確開啟（GOOGLE_DRIVE_ENABLED=1）時才動作，否則安靜跳過。
    失敗只印提示、絕不影響初判的正常產出（優雅降級）。
    """
    if os.environ.get("GOOGLE_DRIVE_ENABLED", "").strip() not in ("1", "true", "True"):
        return
    try:
        import drive_backup  # 同資料夾的備份模組
    except ImportError as e:
        print(f"  [Drive] 略過：drive_backup.py 無法匯入（{e}）")
        return
    try:
        fid = drive_backup.upload_file(path)
        print(f"  [Drive] 已備份 {os.path.basename(path)} → file_id={fid}")
    except SystemExit as e:
        print(f"  [Drive] 略過：{e}")
    except Exception as e:
        print(f"  [Drive] 備份失敗（不影響初判）：{type(e).__name__}: {e}")


def local_draft(mode, snapshot):
    """本地（無 API key）的結構化草稿：把資料攤開＋附上防幻覺錨點。"""
    head = (
        f"# 每日市場初判（{'盤後' if mode == 'afterclose' else '盤前'}）\n\n"
        f"產生時間：{_now_et():%Y-%m-%d %H:%M} ET｜引擎：{active_engine()}（本地草稿模式，待精修）\n\n"
        f"---\n\n"
    )
    themes = "\n".join(f"- {t}" for t in STANDING_THEMES)
    dates = "\n".join(f"- {d}：{e}" for d, e in KEY_DATES)
    notes = "\n".join(f"- **{k}**：{v}" for k, v in TICKER_NOTES.items())
    if mode == "afterclose":
        body = (
            "# 盤後回顧（草稿）\n\n"
            "> 這是「草稿＋精修」的草稿端自動產出（本地模式，尚未經 Claude 精修）。\n"
            "> 請把下方的「快照＋框架對照」帶進 Claude chat 深挖真正的「為什麼」。\n\n"
            "## 待命中的追蹤主題\n\n" + themes + "\n\n"
            "## 關鍵日期倒數\n\n" + dates + "\n\n"
            "## 防幻覺錨點（個股備註）\n\n" + notes + "\n\n"
            + build_close_analysis(snapshot) + "\n\n"
            "## 今日快照（原始資料）\n\n```\n" + snapshot + "\n```\n"
        )
    else:
        body = (
            "# 盤前展望（草稿）\n\n"
            "> 草稿端自動產出（本地模式）。請帶快照＋框架進 Claude 深挖。\n\n"
            "## 今天要盯的主題\n\n" + themes + "\n\n"
            "## 關鍵日期\n\n" + dates + "\n\n"
            "## 快照\n\n```\n" + snapshot + "\n```\n"
        )
    footer = "\n\n*非投資建議，僅為個人研究與分析紀錄。*\n"
    return head + body + footer


def api_generate(system, user):
    """呼叫 LLM 生成初判。

    優先順序：
      1. 有 NVIDIA_API_KEY → 走 NVIDIA NIM（OpenAI 相容介面）。
      2. 否則有 ANTHROPIC_API_KEY → 走 Claude（original behaviour）。
      3. 都無 → 回傳 None（呼叫端會降級為本地草稿模式）。

    NVIDIA NIM 採 OpenAI chat/completions 介面，改用 messages 傳 system prompt，
    沒有獨立的 system 參數。
    """
    nvidia_key = os.environ.get("NVIDIA_API_KEY")

    # ── 1. NVIDIA NIM ──────────────────────────────────────
    if nvidia_key:
        try:
            from openai import OpenAI
        except ImportError:
            print("  （想走 NVIDIA，但 openai 套件未裝 → 退回 Anthropic）")
        else:
            global _ACTUAL_ENGINE
            try:
                client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=nvidia_key)
                messages = [{"role": "system", "content": system},
                            {"role": "user", "content": user}]
                resp = client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=messages,
                    max_tokens=4000,
                    temperature=0,
                )
                _ACTUAL_ENGINE = f"nvidia／{NVIDIA_MODEL}"
                return resp.choices[0].message.content
            except Exception as e:
                print(f"  [NVIDIA 呼叫失敗] {type(e).__name__}: {str(e)[:400]}")
                print(f"    模型：{NVIDIA_MODEL}｜base_url：{NVIDIA_BASE_URL}")
                print("  → 退回 Anthropic（若無 ANTHROPIC_API_KEY 則走本地草稿）")
                # 不 return，往下走 Anthropic 嘗試

    # ── 2. Anthropic（original path） ──────────────────────
    try:
        import anthropic
    except ImportError:
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _ACTUAL_ENGINE = f"anthropic／{MODEL}"
    return "".join(b.text for b in resp.content if getattr(b, "text", None))


# 記錄「實際成功」的引擎（api_generate 成功後寫入，供報告標頭反映真相）
# 避免標頭宣稱 nvidia/... 但實際上早已降級到本地草稿的混淆。
_ACTUAL_ENGINE = None


def active_engine():
    """回傳『實際使用』的引擎（成功呼叫過才算），供報告標頭顯示。

    若 api_generate 尚未成功過，退回用環境變數推測（方便本地除錯）。
    """
    if _ACTUAL_ENGINE:
        return _ACTUAL_ENGINE
    if os.environ.get("NVIDIA_API_KEY"):
        return f"nvidia／{NVIDIA_MODEL}"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return f"anthropic／{MODEL}"
    return "local-draft"


def _now_et():
    """回傳美東時間（含 DST 自動調整）。用 zoneinfo，零新套件。

    注意：zoneinfo 需要 tzdata 套件提供時區資料（GitHub ubuntu-latest 內建、
    requirements.txt 也已納入 tzdata）。若 tzdata 缺失，zoneinfo 不會報錯、
    只會靜默套用 0 偏移（= 本地時區），導致時間標錯；此處主動偵測並改回 UTC
    且列印警告，避免默默產出錯誤時間戳。
    """
    try:
        import zoneinfo
        if not zoneinfo.available_timezones():
            raise RuntimeError("zoneinfo 沒有可用的時區資料（缺 tzdata）")
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception as e:
        print(f"  [時區警告] 無法取得美東時間（{e}），改用系統時間（可能是 UTC）。")
        print("           → 請確認已 pip install tzdata（已加入 requirements.txt）。")
        return datetime.now()


def _schedule_next_run(at_hour=20, at_minute=0):
    """計算距離「今晚 at_hour:at_minute ET」還有多久（秒）。已過則算明天。"""
    from datetime import timedelta
    now = _now_et()
    target = now.replace(hour=at_hour, minute=at_minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def schedule_loop(mode, at_hour=20, at_minute=0):
    """每晚 at_hour:at_minute ET 自動跑一次盤後初判。直到 Ctrl-C。

    - 只在平日（週一~五）美股交易日執行（週末美股不開盤，跳過）。
    - 每次跑完，若報告帶「新觀點」，會自動 merge 進 knowledge。
    """
    import time
    print(f"  排程模式啟動：每天 {at_hour:02d}:{at_minute:02d} ET（美東）自動產出 {mode} 初判。")
    print("  按 Ctrl-C 停止。")
    while True:
        now = _now_et()
        # 週末跳過（美股不開盤）
        if now.weekday() >= 5:  # 5=週六 6=週日
            print(f"  [{now:%Y-%m-%d %H:%M %a}] 週末不開盤，休眠到週一。")
            time.sleep(6 * 3600)
            continue
        sleep_sec = _schedule_next_run(at_hour, at_minute)
        print(f"  [{now:%Y-%m-%d %H:%M %a}] 距下次執行還剩 {sleep_sec/3600:.1f} 小時，休眠中…")
        time.sleep(sleep_sec)
        print(f"  [{_now_et():%Y-%m-%d %H:%M}] 時間到，執行 {mode} 初判…")
        try:
            snap = get_snapshot(argparse.Namespace(snapshot=None))
            system, user = build_prompt(mode, snap)
            text = api_generate(system, user)
            if text is None:
                global _ACTUAL_ENGINE
                _ACTUAL_ENGINE = "local-draft"
                text = local_draft(mode, snap)
            save_output(mode, text.strip() + "\n")
            # 嘗試把新觀點 merge 進 knowledge（見下方函式）
            merge_insights_to_knowledge(mode)
        except Exception as e:
            print(f"  [錯誤] 本次執行失敗：{e}")


def merge_insights_to_knowledge(mode):
    """從最新一份報告提取「新觀點 / 更新」區塊，追加到 knowledge_slim.md 的驗證記錄。

    報告裡若含標題「## 新觀點（本次新增）」或「## 驗證記錄更新」，
    就把該區塊內容（無表頭）併入 knowledge_slim.md 的「驗證記錄」段，
    並把「最後更新」日期更新為今天。這樣每天的分析會反哺知識庫。
    """
    import glob
    files = sorted(glob.glob(os.path.join(_BRIEFS, "*.md")),
                   key=os.path.getmtime)
    if not files:
        return
    latest = files[-1]
    with open(latest, encoding="utf-8") as f:
        text = f.read()
    # 抓「新觀點」與「驗證記錄更新」兩個可能的區塊
    insights = []
    for header in ("## 新觀點（本次對知識庫/框架的更新）", "## 驗證記錄更新"):
        idx = text.find(header)
        if idx != -1:
            seg = text[idx + len(header):]
            # 到下一組 ## 就截斷
            nxt = seg.find("\n## ")
            seg = seg[:nxt] if nxt != -1 else seg
            for line in seg.strip().splitlines():
                line = line.strip()
                if line and not line.startswith("|") and "|" not in line:
                    insights.append(line.rstrip())
    if not insights:
        return
    ks = os.path.join(_HERE, "knowledge_slim.md")
    if not os.path.exists(ks):
        return
    with open(ks, encoding="utf-8") as f:
        ktext = f.read()
    today = datetime.now().strftime("%Y-%m-%d")
    # 更新「最後更新」日期
    ktext = re.sub(r"最後更新：[\d\-]+", f"最後更新：{today}", ktext, count=1)
    # 洗掉重複行，避免每天重複追加相同句子
    existing = set(ktext.splitlines())
    new_lines = [ln for ln in insights if ln not in existing]
    if not new_lines:
        print("  （無新的 knowledge 觀點需要合併）")
        return
    # 在「## 驗證記錄」段落標題後插入
    marker = "## 驗證記錄"
    if marker in ktext:
        ktext = ktext.replace(marker,
                              marker + "\n" + "\n".join(new_lines) + "\n", 1)
    else:
        ktext += "\n\n## 驗證記錄\n" + "\n".join(new_lines) + "\n"
    with open(ks, "w", encoding="utf-8") as f:
        f.write(ktext)
    print(f"  [knowledge] 已合併 {len(new_lines)} 條新觀點進 knowledge_slim.md")


def main():
    p = argparse.ArgumentParser(description="每日市場初判引擎")
    p.add_argument("--mode", choices=["premarket", "afterclose"],
                   default="afterclose", help="盤前/盤後（預設盤後）")
    p.add_argument("--dry-run", action="store_true",
                   help="只印要送給 Claude 的 prompt，不花錢")
    p.add_argument("--snapshot", help="指定既有快照檔（跳過抓資料）")
    p.add_argument("--schedule", action="store_true",
                   help="排程模式：每晚 20:00 ET 自動跑（平日）")
    p.add_argument("--at", default="20:00",
                   help="排程執行時間（ET，格式 HH:MM，預設 20:00）")
    args = p.parse_args()

    if args.schedule:
        try:
            hh, mm = args.at.split(":")
            schedule_loop(args.mode, int(hh), int(mm))
        except ValueError:
            sys.exit("--at 格式錯誤，應為 HH:MM（如 20:00）")
        return

    snap = get_snapshot(args)
    system, user = build_prompt(args.mode, snap)

    if args.dry_run:
        print("========== SYSTEM PROMPT（審核用） ==========\n")
        print(system[:6000])
        print("\n\n========== USER PROMPT（審核用） ==========\n")
        print(user[:8000])
        print("\n\n（dry-run 結束。確認框架餵對、快照解析對，再跑真的。）")
        return

    print("  [2/3] 呼叫模型生成初判...")
    text = api_generate(system, user)
    if text is None:
        print("  （無 NVIDIA_API_KEY / ANTHROPIC_API_KEY，或呼叫失敗）→ 改用本地草稿模式")
        global _ACTUAL_ENGINE
        _ACTUAL_ENGINE = "local-draft"
        text = local_draft(args.mode, snap)

    print("  [3/3] 存檔...")
    save_output(args.mode, text.strip() + "\n")
    merge_insights_to_knowledge(args.mode)
    print("  完成。可用 python open_brief.py 開啟瀏覽器預覽。")


if __name__ == "__main__":
    main()