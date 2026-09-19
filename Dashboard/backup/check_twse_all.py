# -*- coding: utf-8 -*-
"""
check_twse_all.py — 一次診斷所有證交所端點

用途：找出哪些端點有資料、實際欄位名稱是什麼，
      據此修正 twstock_pro.py 的欄位對應。

執行：  python check_twse_all.py
"""

import os

_here = os.path.dirname(os.path.abspath(__file__))
_bundle = os.path.join(_here, "ca_bundle.pem")
if os.path.exists(_bundle):
    os.environ["CURL_CA_BUNDLE"] = _bundle
    os.environ["SSL_CERT_FILE"] = _bundle
    os.environ["REQUESTS_CA_BUNDLE"] = _bundle

import requests

BASE = "https://openapi.twse.com.tw/v1"

ENDPOINTS = [
    ("估值 BWIBBU",        f"{BASE}/exchangeReport/BWIBBU_ALL", "2330"),
    ("月營收 t187ap05_L",   f"{BASE}/opendata/t187ap05_L",       "2330"),
    ("三大法人 T86",        f"{BASE}/fund/T86",                  "2330"),
    ("除權息 t187ap04_L",   f"{BASE}/opendata/t187ap04_L",       None),
    ("損益表一般業 _ci",     f"{BASE}/opendata/t187ap06_L_ci",    "2330"),
    ("損益表異業 _mim",      f"{BASE}/opendata/t187ap06_L_mim",   None),
]

HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def probe(label, url, want_code):
    print("\n" + "=" * 66)
    print(f"【{label}】")
    print(url)
    print("=" * 66)
    try:
        r = requests.get(url, headers=HDR, timeout=20)
        print("狀態碼:", r.status_code, "｜ 內容長度:", len(r.text.strip()))
        if r.status_code != 200:
            print("→ 非 200，跳過")
            return
        body = r.text.strip()
        if not body:
            print("→ 回傳空白（此端點目前無資料，例如盤中的三大法人）")
            return
        try:
            data = r.json()
        except Exception:
            print("→ 不是 JSON。前 150 字：")
            print("  ", body[:150].replace("\n", " "))
            return
        if not isinstance(data, list) or not data:
            print("→ 資料為空 list")
            return

        print(f"筆數: {len(data)}")
        print("\n── 欄位名稱 ──")
        for k in data[0].keys():
            print("   ", repr(k))

        # 找目標公司
        target = None
        if want_code:
            for row in data:
                for key in ("證券代號", "公司代號", "股票代號", "Code"):
                    if str(row.get(key, "")).strip() == want_code:
                        target = row
                        break
                if target:
                    break
        sample = target or data[0]
        tag = want_code if target else "第一筆"
        print(f"\n── {tag} 的內容 ──")
        for k, v in sample.items():
            print(f"    {k} = {v}")

    except Exception as e:
        print("連線失敗:", type(e).__name__, "-", e)


print("開始診斷證交所所有端點…")
for label, url, code in ENDPOINTS:
    probe(label, url, code)

print("\n" + "=" * 66)
print("診斷完成。")
print("請把『損益表一般業 _ci』與『除權息 t187ap04_L』兩段的")
print("「欄位名稱」貼回來，我據此修正欄位對應。")
print("=" * 66)
