#!/usr/bin/env python3
"""check_nvidia.py — NVIDIA Build API 快速診斷
分別測兩個端點，幫你判斷 404 到底卡在哪：
  1) /v1/models        列出帳戶可見的模型（測金鑰 + 連線）
  2) /v1/chat/completions  真的呼叫一次（測 Public API Endpoints 權限）

用法：  python check_nvidia.py
需要：  已設好 NVIDIA_API_KEY 環境變數、pip install openai
"""
import os
import sys

try:
    from openai import OpenAI
except ImportError:
    sys.exit("請先安裝：pip install openai")

key = os.environ.get("NVIDIA_API_KEY")
if not key:
    sys.exit("找不到 NVIDIA_API_KEY。先確認環境變數設好，且『重開過』終端機。")

print(f"金鑰前綴：{key[:8]}…（長度 {len(key)}）")
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key)

# ── 測試 1：列模型 ──
print("\n[1] 測 /v1/models（列出可用模型）…")
try:
    ids = [m.id for m in client.models.list().data]
    print(f"  ✓ 成功，帳戶可見 {len(ids)} 個模型")
    hit = [m for m in ids if "deepseek-v3.1" in m]
    print(f"  含 deepseek-v3.1 的：{hit or '（沒有）'}")
    print(f"  前 10 個：{ids[:10]}")
    models_ok = True
except Exception as e:
    print(f"  ✗ 失敗：{type(e).__name__}: {str(e)[:200]}")
    models_ok = False

# ── 測試 2：真的呼叫 ──
print("\n[2] 測 /v1/chat/completions（實際呼叫一次）…")
try:
    r = client.chat.completions.create(
        model="deepseek-ai/deepseek-v3.1",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=10,
    )
    print(f"  ✓ 成功：{r.choices[0].message.content!r}")
    print("\n結論：一切正常，daily_brief.py 應該可以跑了。")
except Exception as e:
    print(f"  ✗ 失敗：{type(e).__name__}: {str(e)[:250]}")
    print("\n── 診斷 ──")
    if models_ok:
        print("  列模型成功、但呼叫 404 →")
        print("  這是 NVIDIA 帳戶缺『Public API Endpoints』權限（近期大量個人帳戶中招）。")
        print("  不是你的金鑰或程式問題，換模型也救不了。")
        print("  解法：到 NVIDIA 開發者論壇的 Access/Accounts 版發文請求開通此權限，")
        print("        或先用 --provider anthropic（Claude）那條路，不受影響。")
    else:
        print("  連列模型都失敗 → 多半是金鑰無效、被截斷、或公司網路/代理擋住。")
        print("  先確認金鑰完整無空白、環境變數設對、終端機有重開。")
