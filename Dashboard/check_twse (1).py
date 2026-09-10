# -*- coding: utf-8 -*-
"""
check_twse.py — 證交所三大法人 API 診斷工具

用途：確認為什麼台股儀表板的外資/投信/自營商欄位沒資料。
執行：  python check_twse.py
"""

import os

# 沿用同資料夾的 ca_bundle.pem（若存在），解決 SSL 問題
_here = os.path.dirname(os.path.abspath(__file__))
_bundle = os.path.join(_here, "ca_bundle.pem")
if os.path.exists(_bundle):
    os.environ["CURL_CA_BUNDLE"] = _bundle
    os.environ["SSL_CERT_FILE"] = _bundle
    os.environ["REQUESTS_CA_BUNDLE"] = _bundle

import requests

URL = "https://openapi.twse.com.tw/v1/fund/T86"

print("=" * 60)
print("正在連線證交所 T86（三大法人買賣超）…")
print("=" * 60)

try:
    r = requests.get(
        URL,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=15,
    )
    print("HTTP 狀態碼:", r.status_code)
    r.raise_for_status()

    # 先看回傳內容長度，區分「空資料」與「有資料」
    body = r.text.strip()
    print("回傳內容長度:", len(body), "字元")

    if not body:
        print("\n[連線成功，但證交所回傳空內容]")
        print("→ 這幾乎確定是『台灣非交易時段』。")
        print("  T86 三大法人只提供最新一個交易日的資料，")
        print("  台灣收盤（下午 3 點多）後才會產生。")
        print("  你人在美國的話，請在『台灣交易日的收盤後』再跑，")
        print("  換算大約是美東時間凌晨 3 點以後、或台灣當地下午。")
        raise SystemExit

    data = r.json()
except SystemExit:
    raise
except requests.exceptions.JSONDecodeError:
    print("\n[連線成功（200），但回傳的不是 JSON]")
    print("回傳內容前 200 字：")
    print("  ", r.text[:200].replace("\n", " "))
    print("\n→ 最可能是台灣非交易時段，證交所回了空白或一段 HTML。")
    print("  換台灣收盤後再試。你的網路與 SSL 都正常（狀態碼是 200）。")
    raise SystemExit
except Exception as e:
    print("\n[連線失敗]", type(e).__name__, "-", e)
    print("\n可能原因：")
    print("  1. 網路 / SSL 問題（先跑 fix_ssl.py）")
    print("  2. 證交所暫時維護")
    raise SystemExit

print("資料筆數:", len(data))

if not data:
    print("\n[連線成功，但沒有資料]")
    print("→ 通常是非交易日，或收盤資料尚未更新。換台灣交易日收盤後再跑。")
    raise SystemExit

print("\n" + "=" * 60)
print("欄位名稱（請把這一段完整貼回給我）")
print("=" * 60)
for k in data[0].keys():
    print("   ", repr(k))

print("\n" + "=" * 60)
print("台積電(2330) 這一筆的完整內容")
print("=" * 60)
found = False
for row in data:
    code = str(row.get("證券代號") or row.get("Code") or "").strip()
    if code == "2330":
        for k, v in row.items():
            print("   ", k, "=", v)
        found = True
        break
if not found:
    print("   （找不到 2330，改印第一筆）")
    for k, v in data[0].items():
        print("   ", k, "=", v)

print("\n完成。若上面有印出欄位與數字，代表資料正常，")
print("把「欄位名稱」那一段貼給我，我對照修正儀表板即可。")
