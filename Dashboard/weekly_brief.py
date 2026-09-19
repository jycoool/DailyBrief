#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_brief.py — 每週市場週報引擎（搭配 GitHub Actions 每週日自動執行）

做三件事：
  1. 彙整本週（週一～週五）briefs/ 裡的精修版/草稿初判，抽出每日一句話總結與附錄狀態表。
  2. 抓 Google News RSS（免 key）補 watchlist 主題的當週新聞標題，供核實錨點。
  3. 有 LLM key → 產出完整週報＋「框架與知識庫更新建議」；無 key → 產出結構化本地草稿。

產出：
    briefs/週報_YYYY-MM-DD_至_YYYY-MM-DD_Www.md
    briefs/週報_...Www.html                       （呼叫 briefs/render_deep_brief.py）
    knowledge_updates_log.md                    （追記「更新建議」段落，附日期與來源週）

設計原則（與 daily_brief.py 相同）：
  - LLM 只「建議」框架/知識庫更新，腳本「不直接改」knowledge_analysis.md 或
    核心分析框架.docx（唯一真相源，人工併入），只寫進 knowledge_updates_log.md。

用法：
    python weekly_brief.py                 # 本週（週日跑）
    python weekly_brief.py --dry-run       # 只印 prompt，不呼叫 LLM

環境變數：與 daily_brief.py 完全相同（NVIDIA_API_KEY / ANTHROPIC_API_KEY / GOOGLE_DRIVE_*）。
"""

import argparse
import glob
import html
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta

import daily_brief as d  # 複用：快照、框架載入、api_generate、Drive 備份、ET 時區

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRIEFS = os.path.join(_HERE, "briefs")

# 週報新聞搜尋主題（Google News RSS，免 key）
NEWS_QUERIES = [
    "Micron SK Hynix HBM memory",
    "Broadcom Marvell AI chip",
    "oil price WTI Hormuz",
    "Federal Reserve rate yields 30-year treasury",
    "Apple iPhone margin",
    "Oracle cloud capex",
]
MAX_NEWS_PER_QUERY = 5


def week_range(now_et):
    """回傳本週一～週五（若今天週日，就是剛結束的這一週）。"""
    weekday = now_et.weekday()          # 一=0 … 日=6
    monday = now_et.date() - timedelta(days=now_et.weekday())  # 週六/週日跑剛好回到本週一
    return monday, monday + timedelta(days=4)


def collect_week_briefs(monday, sunday):
    """收集週一～週日內的精修版與草稿，回傳 [(date, kind, path, text)]。

    精修版優先；同一天有精修版就略過草稿，避免重複。
    """
    out = []
    for path in sorted(glob.glob(os.path.join(_BRIEFS, "每日初判_*_*.md"))):
        name = os.path.basename(path)
        m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", name)
        if not m:
            continue
        from datetime import date
        dte = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if not (monday <= dte <= sunday):
            continue
        kind = "精修版" if "精修" in name else "草稿"
        out.append((dte, kind, path))
    # 精修版優先：同日兩者都有→丟草稿
    refined_dates = {dte for dte, kind, _ in out if kind == "精修版"}
    final = [(dte, kind, p) for dte, kind, p in out
             if kind == "精修版" or dte not in refined_dates]
    result = []
    for dte, kind, p in sorted(final):
        with open(p, encoding="utf-8") as f:
            result.append((dte, kind, os.path.basename(p), f.read()))
    return result



def fetch_week_news():
    """抓 Google News RSS，回傳 {query: [(title, source, date_str)]}。失敗回空。"""
    news = {}
    for q in NEWS_QUERIES:
        url = ("https://news.google.com/rss/search?q="
               + urllib.request.quote(q) + "&hl=en-US&gl=US&ceid=US:en")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                root = ET.fromstring(r.read())
            items = []
            for it in root.iter("item"):
                title = it.findtext("title", "")
                src = it.find("source")
                items.append((title, src.text if src is not None else "",
                              it.findtext("pubDate", "")[:16]))
            news[q] = items[:MAX_NEWS_PER_QUERY]
        except Exception as e:
            news[q] = [("（抓取失敗：" + str(e)[:80] + "）", "", "")]
    return news


def build_weekly_prompt(briefs, news, snapshot):
    """組週報用的 system + user prompt。"""
    framework = d.load_framework()
    system = (
        "你是我的個人市場研究助理。現在要做「每週回顧」。\n"
        "以下是我的核心分析框架＋歷史判斷（唯一真相源）：\n"
        f"{framework}\n"
    )
    briefs_txt = "\n\n".join(
        f"===== {dte}（{kind}）{name} =====\n{text}" for dte, kind, name, text in briefs)
    news_txt = "\n".join(
        f"[{q}] " + "；".join(f"{t}（{s} {dt}）" for t, s, dt in items)
        for q, items in news.items())
    user = (
        "請產出一份 W 週週報，固定七節：\n"
        "0. 一週一句話\n"
        "1. 本週大事年表（表格：日/事件/市場反應）\n"
        "2. 新聞核實紀錄（逐條標 ✅核實／⚠️僅標題待查，說明對框架的意義）\n"
        "3. 一週主線回顧（對照框架編號，3-6 條）\n"
        "4. Watchlist 一週體檢（用週五快照數字）\n"
        "5. 驗證記錄（本週新增：舊判斷/結果/✅❌⚠️⏳）\n"
        "6. 下週觀察清單（帶具體價位或條件的證偽線）\n"
        "7. 確信度分層（我有信心／需要再確認／我在猜的）\n"
        "---\n"
        "8. 【框架與知識庫更新建議】：另起一段，列出『需要』改動框架或 knowledge 的條目"
        "（格式：檔案/段落 → 建議新增或修改內容 → 觸發理由）。"
        "沒有必要就明寫「本週無需更新」。這段會被自動抽出存檔，不會自動覆寫原檔。\n"
        "規則：繁體中文、第一人稱、防幻覺（沒資料明說）、引用新聞只引用下方給的標題。\n\n"
        f"===== 本週每日初判 =====\n{briefs_txt}\n\n"
        f"===== 本週新聞（Google News RSS）=====\n{news_txt}\n\n"
        f"===== 週五收盤快照 =====\n{snapshot}"
    )
    return system, user


def local_weekly_draft(monday, friday, briefs, news, snapshot):
    """無 LLM 時的本地草稿：彙整檔案清單＋新聞標題＋快照。"""
    files = "\n".join(f"- {dte}（{kind}）`{name}`" for dte, kind, name, _ in briefs)
    news_txt = "\n".join(
        f"### {q}\n" + "\n".join(f"- {t}（{s} {dt}）" for t, s, dt in items)
        for q, items in news.items())
    iso = monday.isocalendar()
    return (
        f"# 市場週報 — {monday} 至 {friday}（W{iso.week:02d}）\n\n"
        f"產生時間：{d._now_et().strftime('%Y-%m-%d %H:%M')} ET｜引擎：local-weekly-draft（無 LLM key，規則彙整）\n\n"
        "> 這是草稿端自動產出：只彙整本週檔案＋新聞＋快照，真正的「週綜合」與框架/知識庫更新建議，"
        "請把本檔帶進 Claude chat 精修。\n\n"
        "## 本週檔案清單\n\n" + files + "\n\n"
        "## 本週新聞（Google News RSS，僅標題層級）\n\n" + news_txt + "\n\n"
        "## 框架與知識庫更新建議\n\n（本地模式無法判斷——請在 Claude 精修時依週報產出，"
        "有更新就貼進 knowledge_analysis.md 的「框架更新紀錄」段落。）\n\n"
        "## 週五快照（原始資料）\n\n```\n" + snapshot + "\n```\n\n"
        "*非投資建議，僅為個人研究與分析紀錄。*\n"
    )



def extract_update_section(report_md: str) -> str | None:
    """從週報中抽出「框架與知識庫更新建議」段落（找不到回 None）。"""
    m = re.search(r"(#{1,3}\s*.*框架與知識庫更新建議.*)(\n[\s\S]*)", report_md)
    if not m:
        return None
    return (m.group(1) + m.group(2)).strip()


def main():
    ap = argparse.ArgumentParser(description="每週市場週報引擎")
    ap.add_argument("--dry-run", action="store_true", help="只印 prompt，不呼叫 LLM、不存檔")
    ap.add_argument("--snapshot", help="指定既有快照檔（跳過抓市場資料）")
    args = ap.parse_args()

    now = d._now_et()
    monday, friday = week_range(now)
    week_no = monday.isocalendar().week
    print(f"▶ 週報範圍：{monday} ~ {friday}（W{week_no:02d}）")

    print("  [1/4] 彙整本週初判…")
    briefs = collect_week_briefs(monday, now.date())
    print(f"        找到 {len(briefs)} 份（{sum(1 for _, k, _, _ in briefs if k == '精修版')} 份精修）")

    print("  [2/4] 抓 Google News RSS…")
    news = fetch_week_news()

    print("  [3/4] 抓週五快照…")
    snapshot = d.get_snapshot(argparse.Namespace(snapshot=args.snapshot))

    print("  [4/4] 產出週報…")
    system, user = build_weekly_prompt(briefs, news, snapshot)
    if args.dry_run:
        print("=" * 70 + "\nDRY RUN — system prompt 長度：%d，user 長度：%d\n" % (len(system), len(user)))
        print(system[:1500], "\n…\n", user[:1500])
        return

    engine = d.active_engine()
    text = None
    try:
        text = d.api_generate(system, user)
        engine = d.active_engine()
    except Exception as e:
        print(f"  [LLM 失敗] {type(e).__name__}: {str(e)[:200]} → 本地草稿")
    if not text:
        text = local_weekly_draft(monday, friday, briefs, news, snapshot)
        engine = "local-weekly-draft"

    header = (
        f"產生時間：{now.strftime('%Y-%m-%d %H:%M')} ET｜引擎：{engine}\n"
        f"資料：本週初判 × {len(briefs)}＋Google News RSS＋週五快照\n\n---\n\n"
    )
    full = header + text + "\n\n*非投資建議，僅為個人研究與分析紀錄。*\n"

    fname = f"週報_{monday}_至_{friday}_W{week_no:02d}.md"
    out = os.path.join(_BRIEFS, fname)
    if os.path.exists(out):
        # 同名檔已存在（通常是人工精修過的版本）→ 自動版另存，不覆蓋
        fname = f"週報_{monday}_至_{friday}_W{week_no:02d}_自動版.md"
        out = os.path.join(_BRIEFS, fname)
        print(f"  注意：同名週報已存在 → 自動版另存為 {fname}（保留人工精修版）")
    with open(out, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"  已產出：{os.path.relpath(out, _HERE)}")

    # 轉 HTML（呼叫通用版渲染器）
    try:
        sys.path.insert(0, _BRIEFS)
        import render_deep_brief as rdb
        html_out = out[:-3] + ".html"
        rdb.render(full, html_out,
                   f"市場週報 {monday}–{friday}（W{week_no:02d}）",
                   fname, f"W{week_no:02d} 週報")
    except Exception as e:
        print(f"  （HTML 轉換略過：{e}）")

    # 抽出「框架與知識庫更新建議」→ 追加到 knowledge_updates_log.md（不自動改正式檔）
    upd = extract_update_section(text) if engine != "local-weekly-draft" else None
    if upd and "無需更新" not in upd:
        log = os.path.join(_HERE, "knowledge_updates_log.md")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n## W{week_no:02d} 週報更新建議（{monday} ~ {friday}，產出 {now.strftime('%Y-%m-%d')}）\n\n{upd}\n")
        print("  更新建議已追加至：knowledge_updates_log.md（請人工確認後併入 knowledge_analysis.md / 框架 docx）")
        print("  ⚠️ 腳本不自動改 knowledge_analysis.md 與核心分析框架.docx（唯一真相源）。")

    # Drive 備份（跟 daily_brief 相同機制）
    d.try_backup_to_drive(out)
    print("✅ 週報完成")


if __name__ == "__main__":
    main()
