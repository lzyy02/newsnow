from __future__ import annotations

import json
import os
import time
from datetime import datetime

import pandas as pd
import akshare as ak

import export_targeted as ex

TASK = os.environ["TASK"]
funcs = {
    "02": lambda: ex.task_02(ak),
    "11": lambda: ex.task_11(ak),
    "12": lambda: ex.task_12(ak),
    "13": lambda: ex.task_13(ak),
    "14": lambda: ex.task_14(ak),
    "15": lambda: ex.task_15(ak),
    "17": lambda: ex.task_17(ak),
    "18": ex.task_18,
    "19": ex.task_19,
}

started = time.time()
try:
    funcs[TASK]()
    ex.LOGS.append({"任务": TASK, "步骤": "任务汇总", "状态": "完成", "行数": "", "耗时秒": round(time.time()-started, 2), "错误": ""})
except Exception as exc:
    ex.LOGS.append({"任务": TASK, "步骤": "任务汇总", "状态": "失败", "行数": 0, "耗时秒": round(time.time()-started, 2), "错误": f"{type(exc).__name__}: {exc}"})
finally:
    pd.DataFrame(ex.LOGS).to_csv(ex.OUT / f"运行报告_{TASK}.csv", index=False, encoding="utf-8-sig")
    (ex.OUT / f"manifest_{TASK}.json").write_text(json.dumps({"task": TASK, "generated_at_utc": datetime.utcnow().isoformat(), "files": [p.name for p in ex.OUT.glob("*")]}, ensure_ascii=False, indent=2), encoding="utf-8")
