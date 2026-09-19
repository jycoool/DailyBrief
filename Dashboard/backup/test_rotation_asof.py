#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rotation_asof.py — 驗證 rotation_monitor 的「資料釘樁」修正。

重點：確認早跑/晚跑會拿到同一份資料，且能偵測
  (b) yfinance 塞的未來/幽靈 bar
  (c) 資料落後一天（你 8/29 早上那張的情境）

不碰網路：用 stub 假裝 yfinance，只測純函式。
    python test_rotation_asof.py
"""
import sys
import types
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd

# 讓 rotation_monitor 可被 import（它 import yfinance，這裡塞假模組）
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
import rotation_monitor as rm  # noqa: E402

ET = ZoneInfo("America/New_York")
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"{'✅' if ok else '❌'} {name}: got={got} want={want}")


def et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def mkdf(dates, val=1.0):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    return pd.DataFrame({"SPY": [val] * len(dates)}, index=idx)


# 曆法基準：2026-08-28 週五 / 08-29 週六 / 08-30 週日 / 08-31 週一
print("── 1) last_expected_session：執行時間 → 最後完整交易日 ──")
check("週五收盤前(16:10) → 週四",
      rm.last_expected_session(et(2026, 8, 28, 16, 10)), date(2026, 8, 27))
check("週五收盤後(16:30) → 週五",
      rm.last_expected_session(et(2026, 8, 28, 16, 30)), date(2026, 8, 28))
check("週六早上 11:03 → 週五",
      rm.last_expected_session(et(2026, 8, 29, 11, 3)), date(2026, 8, 28))
check("週六晚上 23:00 → 週五（早=晚，同一天）",
      rm.last_expected_session(et(2026, 8, 29, 23, 0)), date(2026, 8, 28))
check("週日 09:00 → 週五",
      rm.last_expected_session(et(2026, 8, 30, 9, 0)), date(2026, 8, 28))
check("週一盤前 09:00 → 週五（還沒收盤）",
      rm.last_expected_session(et(2026, 8, 31, 9, 0)), date(2026, 8, 28))
check("週一收盤後 17:00 → 週一",
      rm.last_expected_session(et(2026, 8, 31, 17, 0)), date(2026, 8, 31))

print("\n── 2) pin_to_asof：三種資料情境（asof=週五 08-28）──")
asof = date(2026, 8, 28)

# (a) 正常：資料到週五
_, m = rm.pin_to_asof(mkdf([date(2026, 8, 26), date(2026, 8, 27),
                            date(2026, 8, 28)]), asof)
check("(a)正常 actual", m["actual"], date(2026, 8, 28))
check("(a)正常 stale", m["stale"], False)
check("(a)正常 trimmed", m["trimmed"], 0)

# (b) 幽靈未來 bar：多一根週六 08-29 → 應被砍
_, m = rm.pin_to_asof(mkdf([date(2026, 8, 27), date(2026, 8, 28),
                            date(2026, 8, 29)]), asof)
check("(b)幽靈 actual（砍掉週六）", m["actual"], date(2026, 8, 28))
check("(b)幽靈 stale", m["stale"], False)
check("(b)幽靈 trimmed", m["trimmed"], 1)

# (c) 落後：資料只到週四 08-27 → 應標 stale（=你早上那張的病症）
_, m = rm.pin_to_asof(mkdf([date(2026, 8, 25), date(2026, 8, 26),
                            date(2026, 8, 27)]), asof)
check("(c)落後 actual", m["actual"], date(2026, 8, 27))
check("(c)落後 stale（會跳警告）", m["stale"], True)

# (d) 決定性：同一批資料 pin 兩次，結果必相同（早跑=晚跑）
df_full = mkdf([date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 29)])
r1, _ = rm.pin_to_asof(df_full.copy(), asof)
r2, _ = rm.pin_to_asof(df_full.copy(), asof)
check("(d)決定性 兩次結果相同", r1.equals(r2), True)

# (e) 全空 DataFrame 不炸
_, m = rm.pin_to_asof(pd.DataFrame(), asof)
check("(e)空表 不炸 trimmed", m["trimmed"], 0)

print("\n" + ("全部通過 🎉" if fails == 0 else f"⚠ {fails} 項失敗"))
sys.exit(1 if fails else 0)
