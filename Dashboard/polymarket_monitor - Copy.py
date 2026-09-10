#!/usr/bin/env python3
"""
polymarket_monitor.py — Polymarket 預測市場監控模組

策略：一次抓取大量 active events（依交易量排序），在本地做關鍵字比對。
      不使用 /search 端點（該端點自 2026 年起需要認證，會回 401）。

安裝:  pip install requests
執行:
    python polymarket_monitor.py                    # 預設分類
    python polymarket_monitor.py --query "gold"     # 自訂關鍵字
    python polymarket_monitor.py --pages 5          # 抓更多頁（預設 3 頁 = 300 筆）
    python polymarket_monitor.py --debug            # 顯示抓取細節
"""

import json
import os
import re
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    sys.exit("請先安裝 requests:  pip install requests")

# ─────────────────────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────────────────────

GAMMA_BASE = "https://gamma-api.polymarket.com"

# 每個分類的關鍵字。比對邏輯：
#   - 單一字串 = 該字詞出現即命中
#   - tuple    = 所有字詞都要出現才命中（AND 條件，減少誤判）
SEARCH_QUERIES = {
    "聯準會 / 利率": [
        "fed",
        "fomc",
        "federal reserve",
        "interest rate",
        "rate hike",
        "rate cut",
        "powell",
        "warsh",
        "basis points",
    ],
    "通膨 / 經濟": [
        "inflation",
        "recession",
        "cpi",
        "unemployment rate",
        "gdp",
        "jobs report",
        "nonfarm",
        "payrolls",
    ],
    "科技巨頭": [
        # 用 AND 條件綁定金融語境，避免電影／娛樂類誤中
        ("apple", "stock"), ("apple", "iphone"), ("apple", "market cap"),
        ("nvidia", "stock"), ("nvidia", "earnings"), ("nvidia", "market cap"),
        ("microsoft", "stock"), ("microsoft", "earnings"),
        ("google", "stock"), ("alphabet", "stock"),
        ("amazon", "stock"), ("amazon", "earnings"),
        ("meta", "stock"), ("tesla", "stock"), ("tesla", "deliveries"),
        "magnificent 7",
    ],
    "AI / 半導體": [
        "openai",
        "anthropic",
        "chatgpt",
        "gemini",
        ("ai", "agi"),
        ("ai", "model"),
        ("ai", "bubble"),
        "semiconductor",
        "tsmc",
        ("chip", "export"),
        "data center",
    ],
    "貴金屬": [
        "gold",
        "silver",
        "bullion",
        "precious metals",
    ],
    "地緣政治 / 能源": [
        "iran",
        ("oil", "price"),
        ("oil", "barrel"),
        "opec",
        "hormuz",
        ("russia", "ukraine"),
        ("china", "taiwan"),
        "tariff",
        "tariffs",
    ],
}


# 排除詞：命中這些的賭盤直接跳過（減少誤判）
EXCLUDE_TERMS = [
    # 娛樂／影視（最常誤中「科技巨頭」，因為描述含 Apple TV+ / Amazon MGM）
    "movie", "box office", "grossing", "oscar", "oscars", "academy award",
    "golden globe", "emmy", "grammy", "album", "billboard", "rotten tomatoes",
    "netflix show", "streaming series", "song of the year",
    # 體育
    "nba", "nfl", "mlb", "nhl", "premier league", "super bowl", "world cup",
    "champions league", "olympics", "ufc", "wwe", "f1 ", "grand prix",
    "world series", "playoffs", "march madness",
    # 加密貨幣個別代幣（除非你想追蹤）
    "kraken ipo", "dogecoin", "shiba",
    # 娛樂人物／八卦
    "macron", "taylor swift", "kanye", "mrbeast",
]

# ─────────────────────────────────────────────────────────────
# 顏色
# ─────────────────────────────────────────────────────────────

if os.name == "nt":
    os.system("")

C = {
    "red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m",
    "blue": "\033[94m", "gray": "\033[90m", "bold": "\033[1m",
    "cyan": "\033[96m", "end": "\033[0m",
}

def color(text, c):
    return f"{C.get(c, '')}{text}{C['end']}"


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────

class PolymarketError(Exception):
    """API 完全不可用（連線失敗）。"""
    pass


def fetch_events_page(offset=0, limit=100, order="volume24hr", timeout=20, debug=False):
    """
    抓取一頁 active events，依交易量排序。
    回傳 list of events。連線失敗時拋 PolymarketError。
    """
    url = f"{GAMMA_BASE}/events"
    params = {
        "active": "true",
        "closed": "false",
        "archived": "false",
        "limit": limit,
        "offset": offset,
        "order": order,
        "ascending": "false",
    }
    try:
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 401:
            # 這個排序參數可能需要認證，換一個
            if order != "volume":
                if debug:
                    print(color(f"    order={order} 回 401，改用 order=volume", "gray"))
                return fetch_events_page(offset, limit, "volume", timeout, debug)
            raise PolymarketError("API 需要認證（401）")
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []
    except requests.exceptions.ConnectionError as e:
        raise PolymarketError(f"連線失敗: {e}")
    except requests.exceptions.Timeout:
        raise PolymarketError("連線逾時")
    except PolymarketError:
        raise
    except Exception as e:
        if debug:
            print(color(f"    抓取失敗 (offset={offset}): {e}", "red"))
        return []


def fetch_event_pool(pages=3, debug=False):
    """
    抓取多頁 events 組成候選池。
    回傳 list of events。
    """
    pool = []
    for i in range(pages):
        offset = i * 100
        try:
            batch = fetch_events_page(offset=offset, limit=100, debug=debug)
        except PolymarketError:
            if i == 0:
                raise  # 第一頁就失敗 = API 不可用
            break      # 後續頁失敗就用已有的
        if not batch:
            break
        pool.extend(batch)
        if debug:
            print(color(f"    第 {i+1} 頁：{len(batch)} 筆（累計 {len(pool)}）", "gray"))
        if len(batch) < 100:
            break  # 沒有更多了
        time.sleep(0.25)
    return pool


def _event_text(event, include_description=False):
    """
    組合 event 的可搜尋文字。

    預設「不」納入 description —— 描述裡常出現發行商／串流平台名稱
    （Amazon MGM、Apple TV+ 等），會讓電影類賭盤誤中「科技巨頭」分類。
    只比對標題與 market 問題，精準度高很多。
    """
    parts = [
        event.get("title") or "",
        event.get("slug") or "",
    ]
    for m in event.get("markets", []) or []:
        parts.append(m.get("question") or "")
        parts.append(m.get("groupItemTitle") or "")
    if include_description:
        parts.append(event.get("description") or "")
    return " ".join(parts).lower()


def _word_in(text, word):
    """
    整詞比對，避免 substring 誤判。
    例：'meta' 不該命中 'metadata'，'ai' 不該命中 'Thailand'。
    含非英數字元的詞（如 'gpt-'、'interest rate'）退回 substring 比對。
    """
    w = word.lower().strip()
    if not w:
        return False
    # 純英數（可含空白）→ 用詞界比對
    if re.fullmatch(r"[a-z0-9 ]+", w):
        pattern = r"\b" + re.escape(w) + r"\b"
        return re.search(pattern, text) is not None
    # 其他（含連字號、符號）→ substring
    return w in text


def _matches(text, keyword):
    """
    keyword 是字串  → 整詞比對
    keyword 是 tuple → 所有元素都要出現（AND）
    """
    if isinstance(keyword, tuple):
        return all(_word_in(text, k) for k in keyword)
    return _word_in(text, keyword)


def _is_excluded(text):
    """命中排除詞即跳過。"""
    return any(_word_in(text, term) for term in EXCLUDE_TERMS)


def extract_markets(event):
    """從 event 提取有效的 market 資訊。"""
    results = []
    markets = event.get("markets") or []
    if not markets:
        return results

    event_title = event.get("title", "")

    for m in markets:
        if not m.get("active", True):
            continue
        if m.get("closed", False):
            continue

        # 機率
        yes_prob = None
        raw = m.get("outcomePrices")
        if raw:
            try:
                prices = json.loads(raw) if isinstance(raw, str) else raw
                if prices:
                    yes_prob = float(prices[0]) * 100
            except (json.JSONDecodeError, TypeError, ValueError, IndexError):
                pass
        if yes_prob is None:
            for k in ("lastTradePrice", "bestBid"):
                v = m.get(k)
                if v is not None:
                    try:
                        p = float(v)
                        if 0 <= p <= 1:
                            yes_prob = p * 100
                            break
                    except (ValueError, TypeError):
                        pass
        if yes_prob is None:
            continue

        # 交易量
        volume = 0.0
        for k in ("volumeNum", "volume", "volume24hr"):
            v = m.get(k)
            if v is not None:
                try:
                    volume = float(v)
                    if volume > 0:
                        break
                except (ValueError, TypeError):
                    pass

        question = m.get("question") or m.get("groupItemTitle") or event_title
        end_date = m.get("endDate")

        # 過濾已過期
        if end_date:
            try:
                dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                if dt.replace(tzinfo=None) < datetime.now():
                    continue
            except Exception:
                pass

        results.append({
            "question": question,
            "yes_prob": yes_prob,
            "volume": volume,
            "end_date": end_date,
            "event_title": event_title,
        })
    return results


def fetch_all_relevant_markets(queries=None, pages=3, per_category=5, debug=False):
    """
    抓取候選池，在本地做關鍵字比對分類。
    回傳 dict: {category: [market, ...]}，或 None（API 不可用）
    """
    if queries is None:
        queries = SEARCH_QUERIES

    try:
        pool = fetch_event_pool(pages=pages, debug=debug)
    except PolymarketError as e:
        print(color(f"  Polymarket: {e}", "yellow"))
        return None

    if not pool:
        return {}

    if debug:
        print(color(f"    候選池：{len(pool)} 個 events", "gray"))

    results = {}
    global_seen = set()

    for category, keywords in queries.items():
        matched = []
        for event in pool:
            if not isinstance(event, dict):
                continue
            text = _event_text(event)
            if _is_excluded(text):
                continue
            if not any(_matches(text, kw) for kw in keywords):
                continue

            for m in extract_markets(event):
                q = m["question"]
                if q in global_seen:
                    continue
                global_seen.add(q)
                matched.append(m)

        if matched:
            matched.sort(key=lambda x: x["volume"], reverse=True)
            results[category] = matched[:per_category]
            if debug:
                print(color(f"    {category}: {len(matched)} 筆 → 取前 {min(len(matched), per_category)}", "gray"))
        elif debug:
            print(color(f"    {category}: 無命中", "gray"))

    return results


# ─────────────────────────────────────────────────────────────
# 顯示
# ─────────────────────────────────────────────────────────────

def format_volume(v):
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def prob_color(p):
    if p is None:
        return color("  n/a", "gray")
    s = f"{p:.0f}%"
    if p >= 60:
        return color(s, "green")
    if p <= 30:
        return color(s, "red")
    return color(s, "yellow")


def render_polymarket(results):
    print("\n" + "=" * 78)
    print(color("  Polymarket 預測市場   ", "bold") +
          color(f"{datetime.now():%Y-%m-%d %H:%M:%S}", "gray"))
    print(color("  機率反映市場共識，非事實預測。僅供參考。", "gray"))
    print("=" * 78)

    if results is None:
        print(color("\n  ⚠ Polymarket API 無法連線", "yellow"))
        print("=" * 78 + "\n")
        return

    if not results:
        print(color("\n  候選池中沒有符合分類關鍵字的賭盤。", "gray"))
        print(color("  可用 --pages 5 擴大搜尋範圍，或 --debug 查看細節。", "gray"))
        print("=" * 78 + "\n")
        return

    for category, markets in results.items():
        print(color(f"\n  【{category}】", "bold"))
        for m in markets:
            q = m["question"]
            if len(q) > 58:
                q = q[:55] + "..."
            prob = prob_color(m["yes_prob"])
            vol = format_volume(m["volume"])
            end = ""
            if m["end_date"]:
                try:
                    dt = datetime.fromisoformat(m["end_date"].replace("Z", "+00:00"))
                    end = f"  到期 {dt:%m/%d}"
                except Exception:
                    pass
            print(f"    {q:<60} YES {prob:>8}  Vol {vol:>8}{end}")

    print(color("\n  ※ Polymarket 在美國無法交易，數據僅供研判市場共識。", "gray"))
    print("=" * 78 + "\n")


def run_polymarket(pages=3, debug=False):
    print(color("正在抓取 Polymarket 賭盤...", "blue"))
    results = fetch_all_relevant_markets(pages=pages, debug=debug)
    render_polymarket(results)
    return results


def main():
    import argparse
    p = argparse.ArgumentParser(description="Polymarket 預測市場監控")
    p.add_argument("--query", "-q", type=str, default=None,
                   help="自訂關鍵字（空白分隔，覆蓋預設分類）")
    p.add_argument("--pages", type=int, default=3,
                   help="抓取頁數，每頁 100 筆（預設 3）")
    p.add_argument("--limit", "-n", type=int, default=5,
                   help="每個分類顯示筆數（預設 5）")
    p.add_argument("--debug", action="store_true", help="顯示抓取細節")
    args = p.parse_args()

    if args.query:
        queries = {"自訂搜尋": args.query.split()}
    else:
        queries = None

    results = fetch_all_relevant_markets(
        queries, pages=args.pages, per_category=args.limit, debug=args.debug
    )
    render_polymarket(results)


if __name__ == "__main__":
    main()
