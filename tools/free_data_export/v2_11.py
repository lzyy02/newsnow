from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import akshare as ak
import pandas as pd

END = "20260801"
OUT = Path(os.environ.get("OUTPUT_DIR", "output")); OUT.mkdir(parents=True, exist_ok=True)
LOGS = []


def safe(step, fn, retries=3):
    t = time.time(); err = ""
    for i in range(retries):
        try:
            df = fn(); df = pd.DataFrame() if df is None else df.copy(); df.columns = [str(c).strip() for c in df.columns]
            LOGS.append([step, "成功", len(df), round(time.time()-t, 2), ""]); return df
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"; time.sleep(i + 1)
    LOGS.append([step, "失败", 0, round(time.time()-t, 2), err]); return pd.DataFrame()


def append_csv(path, df):
    if not df.empty: df.to_csv(path, mode="a", header=not path.exists(), index=False, encoding="utf-8-sig")


market = []
for symbol in ["北向资金", "沪股通", "深股通"]:
    df = safe(f"市场总量-{symbol}", lambda s=symbol: ak.stock_hsgt_hist_em(symbol=s))
    if df.empty: continue
    dc = next((c for c in ["日期", "交易日"] if c in df.columns), None)
    if dc:
        d = pd.to_datetime(df[dc], errors="coerce"); df = df[(d >= pd.Timestamp("20141117")) & (d <= pd.Timestamp(END))].copy(); df[dc] = pd.to_datetime(df[dc], errors="coerce").dt.strftime("%Y-%m-%d")
    df["口径"] = symbol; df["数据源"] = "东方财富/AKShare stock_hsgt_hist_em"; market.append(df)
if market: pd.concat(market, ignore_index=True, sort=False).to_csv(OUT/"11A_北向资金市场总量_20141117_20260801.csv", index=False, encoding="utf-8-sig")

cal = safe("交易日历", ak.tool_trade_date_hist_sina)
dates = []
if not cal.empty:
    s = pd.to_datetime(cal.iloc[:, 0], errors="coerce").dropna(); s = s[(s >= pd.Timestamp("20141117")) & (s <= pd.Timestamp("20240816"))]
    dates = s.groupby(s.dt.to_period("M")).max().dt.strftime("%Y%m%d").tolist()
dates += ["20240930", "20241231", "20250331", "20250630", "20250930", "20251231", "20260331", "20260630"]
dates = sorted(set(x for x in dates if x <= END))
out = OUT/"11B_北向个股持股月末及季度快照_20141117_20260801.csv"


def one(d):
    df = safe(f"个股快照-{d}", lambda: ak.stock_hsgt_stock_statistics_em(symbol="北向持股", start_date=d, end_date=d))
    if df.empty: return df
    df["查询日期"] = pd.to_datetime(d).strftime("%Y-%m-%d")
    df["快照频率"] = "月末" if d <= "20240816" else "季末公开口径尝试"
    df["数据源"] = "东方财富/AKShare stock_hsgt_stock_statistics_em"
    return df

buf = []
with ThreadPoolExecutor(max_workers=4) as pool:
    for fut in as_completed([pool.submit(one, d) for d in dates]):
        df = fut.result()
        if not df.empty: buf.append(df)
        if len(buf) >= 8: append_csv(out, pd.concat(buf, ignore_index=True, sort=False)); buf.clear()
if buf: append_csv(out, pd.concat(buf, ignore_index=True, sort=False))

pd.DataFrame(LOGS, columns=["步骤", "状态", "行数", "耗时秒", "错误"]).to_csv(OUT/"运行报告.csv", index=False, encoding="utf-8-sig")
pd.DataFrame([
    ["市场总量", "日频，截断至2026-08-01"],
    ["个股持股", "月末/季末快照；不将2024-08-19后已停止日披露的数据伪造为日频"]
], columns=["子数据集", "口径说明"]).to_csv(OUT/"数据口径说明.csv", index=False, encoding="utf-8-sig")
