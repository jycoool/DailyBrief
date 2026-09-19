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
    data = r.json()
except Exception as e:
    print("\n[連線失敗]", type(e).__name__, "-", e)
    print("\n可能原因：")
    print("  1. 現在是台股非交易時段（資料收盤後約 15:00 才更新）")
    print("  2. 今天是週末或假日，沒有當日資料")
    print("  3. 網路 / SSL 問題（先跑 fix_ssl.py）")
    print("\n→ 若你人在美國，台灣多半是深夜，換台灣收盤後再試。")
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
