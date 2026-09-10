#!/usr/bin/env bash
# run_daily_brief.sh — 每晚 20:00 ET 自動產出盤後初判（常駐排程模式）
#
# 用法：
#   1. 直接前台跑（看 log）：
#        bash run_daily_brief.sh
#   2. 背景常駐（推薦）：
#        nohup bash run_daily_brief.sh >> briefs/daily_brief.log 2>&1 &
#
# daily_brief.py 的 --schedule 模式內建：
#   - 每晚 20:00（美東，含日光節約自動調整）
#   - 只跑週一~五（美股交易日）
#   - 每次跑完若有「新觀點」，自動 merge 回 knowledge_slim.md
#
cd "$(dirname "$0")"

# 挑選可用 python：優先 .venv，其次 python3 / python
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY="python3"; else PY="python"; fi
fi

exec "$PY" daily_brief.py --mode afterclose --schedule --at 20:00 "$@"