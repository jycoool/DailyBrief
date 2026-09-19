#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refine_draft.py — 把「本地草稿」每日初判精修成「精修版」敘事報告。

工作流設計來自既有「草稿＋精修」模式：
  - 草稿端（daily_brief.py）產出:每日初判_盤後_YYYY-MM-DD_HHMM.md
  - 本腳本把草稿全文 + 分析框架檔 丟給 NVIDIA（OpenAI 相容介面）的
    deepseek-ai/deepseek-v4-flash-0731，按 9/11 精修版的風格產出因果敘事。

用法:
    NVIDIA_API_KEY=你的key python refine_draft.py
    NVIDIA_API_KEY=你的key python refine_draft.py --draft ./briefs/每日初判_盤後_XXXXXXXX_HHMM.md
    NVIDIA_API_KEY=你的key python refine_draft.py --dry-run   # 只印 prompt 不花錢

產出:
    ./briefs/每日初判_盤後_YYYY-MM-DD_精修版.md
"""
import argparse
import os
import re
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRIEFS = os.path.join(_HERE, "briefs")

# 與 daily_brief.py 一致：載入同資料夾 .env，方便放 key（不 commit）
try:
    import drive_backup as _db
    _db._load_dotenv(os.path.join(_HERE, ".env"))
except Exception:
    pass

NVIDIA_BASE_URL = os.environ.get(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.environ.get(
    "NVIDIA_MODEL", "deepseek-ai/deepseek-v4-flash-0731")

FRAMEWORK_FILES = [
    "knowledge_analysis.md",
    "knowledge_project.md",
    "README_每日初判引擎.md",
]

# 精修版輸出範本結構（對齊 9/11 精修版.md 的骨架）
OUTLINE = """\
請把輸入的「每日市場初判（盤後）草稿」雕琢成一份「精修版」因果敘事報告。
不要只複述草稿表格，要抓出今天的「為什麼」與「違反直覺處」，用分析框架翻譯。

輸出必須包含以下章節（用繁體中文）：

# 每日市場初判（盤後）— 精修版

> 產生時間：<今天日期> 美東盤後｜來源：快照資料 + 即時新聞 + 分析框架
> 精修方式：把「市場介面資料（技術/估值）+ 當日新聞 + 累積分析框架」交叉比對後重整成可讀的因果敘事。

## 一句話總結
用一段話回答：今天最該看懂的一件事是什麼。

## 一、今天最違反直覺的地方
把今天的數字擺出來當錨點，然後解釋「表面訊號 vs 框架邏輯」的張力。
- 若草稿有「下跌主軸」，點出這與某個結構性敘事（例如 AI 見頂、折現率、資金輪動）如何互動。
- 明確標出因果：不是「因為跌所以跌」，而是哪個領先因子轉動。
- 抓出一個「證偽條件」：給出具體數字門檻，跌破/突破它代表原敘事作廢。

## 二、事件深挖（抓 1~3 個草稿裡最關鍵的事件）
以個股/主題為單元，每個：先列數字 → 用框架翻譯 → 給「但書/中期判斷」→ 交叉新聞錨點確認。

## 三、落地到 watchlist，標好時間尺度
分「短期（到週三前）／中期（這一兩季）／長期（結構）」三層。
逐檔提示（結合草稿的技術快照）：TMF、記憶體鏈 MU/SKHY/MRVL、AVGO、NVDA/TSM、TSLA/SNOW、
AAPL、GOOGL、及任何「門檻警示」檔。每檔一句「現在它最怕什麼 / 證偽門檻是什麼」。

## 四、關鍵日期與自我檢查點
表格列出最近關鍵日期（年份以輸入草稿為準），並給「確信度分層」：
- 【我有信心的】／【需要你再確認的】／【我在猜的】三層，誠實標記。

## 附錄 A：即時新聞錨點（沿用草稿新聞，不要加油添醋）
## 附錄 B：技術快照（原樣保留草稿的表格與門檻警示，並註明快照時點）

---

*非投資建議，僅為個人研究與分析紀錄。*

寫作要求：
1. 資料時點誠實：所有數字必須來自輸入草稿，不得編造。草稿沒有的數據不要補「我一定知道」的假數字。
2. 若草稿技術快照與新聞時點不同步，要在附錄 B 誠實寫明，並提醒以最新 market_monitor 為準。
3. 確信度要分層，有把握的才用「高」，不確定的明確標示。
4. 全文用繁體中文，語氣 = 對自己持倉講真心話的資深策略師。
"""


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_frameworks():
    parts = []
    for name in FRAMEWORK_FILES:
        p = os.path.join(_HERE, name)
        if os.path.exists(p):
            parts.append(f"===== 分析框架檔：{name} =====\n{read_text(p)}")
    return "\n\n".join(parts)


def find_latest_draft():
    files = []
    for fn in os.listdir(_BRIEFS):
        # 草稿檔名：每日初判_盤後_2026-09-14_2102.md（日期帶連字號）
        if re.match(r"每日初判_盤後_\d{4}-\d{2}-\d{2}_\d{4}\.md$", fn):
            files.append(fn)
    if not files:
        sys.exit("找不到盤後草稿檔。")
    files.sort()
    latest = os.path.join(_BRIEFS, files[-1])
    print(f"  選用草稿：{latest}")
    return latest


def dry_run_print(sys_prompt, user_prompt):
    print("=" * 70)
    print("【SYSTEM PROMPT】")
    print("=" * 70)
    print(sys_prompt)
    print("\n" + "=" * 70)
    print("【USER PROMPT】")
    print("=" * 70)
    print(user_prompt)


def main():
    p = argparse.ArgumentParser(description="把每日初判草稿精修成精修版")
    p.add_argument("--draft", default=None, help="指定草稿檔（預設取最新）")
    p.add_argument("--dry-run", action="store_true", help="只印 prompt 不呼叫")
    args = p.parse_args()

    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        sys.exit("找不到 NVIDIA_API_KEY。先設好環境變數再跑。")

    draft_path = args.draft or find_latest_draft()
    draft = read_text(draft_path)
    framework = load_frameworks()

    sys_prompt = OUTLINE
    user_prompt = (
        "以下是我今天（盤後）的『草稿』與給你參考的分析框架。\n"
        "請依上方系統指示的章節，產出完整的『精修版』報告。\n\n"
        "===== 今日盤後草稿 =====\n" + draft + "\n\n"
        "===== 分析框架參考 =====\n" + framework + "\n"
    )

    if args.dry_run:
        dry_run_print(sys_prompt, user_prompt)
        return

    # 用草稿檔名推日期（檔名為 每日初判_盤後_2026-09-14_2102.md）
    m = re.search(r"盤後_(\d{4}-\d{2}-\d{2})", os.path.basename(draft_path))
    date_tag = m.group(1) if m else datetime.now().strftime("%Y-%m-%d")

    try:
        from openai import OpenAI
        from openai import APIStatusError, APIConnectionError, APITimeoutError
    except ImportError:
        sys.exit("請先安裝：pip install openai")

    # NVIDIA 託管端點不穩，常會 502/503/504。對 5xx／連線／逾時做重試＋退避。
    RETRIES = int(os.environ.get("REFINE_RETRIES", "5"))

    def _call():
        client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=key,
                        timeout=float(os.environ.get("REFINE_TIMEOUT", "600")))
        return client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=int(os.environ.get("REFINE_MAX_TOKENS", "6000")),
            temperature=0.3,
            stream=False,
        )

    import time
    resp = None
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = _call()
            break
        except (APIConnectionError, APITimeoutError) as e:
            last_err = e
        except APIStatusError as e:
            status = getattr(e, "status_code", None) or (e.response.status_code
                                                         if getattr(e, "response", None) else None)
            if status not in (502, 503, 504, 429):  # 其他（如 401/404）直接放棄
                sys.exit(f"NVIDIA 回傳錯誤 {status}: {type(e).__name__} {str(e)[:200]}")
            last_err = e
        print(f"  [重試 {attempt}/{RETRIES}] NVIDIA 呼叫失敗：{type(last_err).__name__}"
              f"（{getattr(last_err, 'status_code', None) or getattr(getattr(last_err, 'response', None), 'status_code', None)}）"
              f"；{2 ** attempt}s 後重試…", flush=True)
        time.sleep(2 ** attempt)
    if resp is None:
        sys.exit(f"重試 {RETRIES} 次仍失敗，最後錯誤：{type(last_err).__name__} {str(last_err)[:200]}")

    content = resp.choices[0].message.content
    if not content or not content.strip():
        sys.exit("NVIDIA 回傳空內容。")

    out_path = os.path.join(_BRIEFS, f"每日初判_盤後_{date_tag}_精修版.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"已產出精修版：{out_path}")
    print(f"（字數：{len(content)}）")


if __name__ == "__main__":
    main()