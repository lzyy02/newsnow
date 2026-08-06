from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import akshare as ak
import pandas as pd

TASK = os.environ.get("TASK", "15-summary")
START = "20150101"; END = "20260801"
OUT = Path(os.environ.get("OUTPUT_DIR", "output")); OUT.mkdir(parents=True, exist_ok=True)
LOGS = []


def safe(step, fn, retries=2):
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


def dates(start, end):
    cal = safe("交易日历", ak.tool_trade_date_hist_sina)
    if cal.empty: return []
    s = pd.to_datetime(cal.iloc[:, 0], errors="coerce").dropna(); s = s[(s >= pd.Timestamp(start)) & (s <= pd.Timestamp(end))]
    return s.sort_values().dt.strftime("%Y%m%d").tolist()


def run_year(year):
    start = max(START, f"{year}0101"); end = min(END, f"{year}1231")
    if start > end: return
    out = OUT/f"15_个股融资融券日明细_{year}.csv"; failed = []
    def one(d):
        frames = []
        for market, fn_name in [("上交所", "stock_margin_detail_sse"), ("深交所", "stock_margin_detail_szse")]:
            if not hasattr(ak, fn_name): continue
            df = safe(f"{market}-{d}", lambda f=getattr(ak, fn_name), day=d: f(date=day))
            if df.empty: failed.append([d, market]); continue
            if "标的证券代码" in df.columns: df.rename(columns={"标的证券代码": "证券代码", "标的证券简称": "证券简称"}, inplace=True)
            if "交易日期" in df.columns: df.rename(columns={"交易日期": "源交易日期"}, inplace=True)
            df.insert(0, "交易日期", pd.to_datetime(d).strftime("%Y-%m-%d")); df.insert(1, "市场", market); df["数据源"] = f"{market}官方/AKShare {fn_name}"; frames.append(df)
        return frames
    buf = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for fut in as_completed([pool.submit(one, d) for d in dates(start, end)]):
            buf.extend(fut.result())
            if len(buf) >= 16: append_csv(out, pd.concat(buf, ignore_index=True, sort=False)); buf.clear()
    if buf: append_csv(out, pd.concat(buf, ignore_index=True, sort=False))
    pd.DataFrame(failed, columns=["交易日期", "市场"]).to_csv(OUT/f"15_失败交易日_{year}.csv", index=False, encoding="utf-8-sig")


def run_summary():
    frames = []
    for fn_name, label in [("macro_china_market_margin_sh", "上海市场汇总"), ("macro_china_market_margin_sz", "深圳市场汇总")]:
        if not hasattr(ak, fn_name): continue
        df = safe(label, getattr(ak, fn_name))
        if df.empty: continue
        dc = next((c for c in ["日期", "date", "信用交易日期"] if c in df.columns), None)
        if dc:
            d = pd.to_datetime(df[dc], errors="coerce"); df = df[d.isna() | ((d >= pd.Timestamp(START)) & (d <= pd.Timestamp(END)))].copy(); df[dc] = pd.to_datetime(df[dc], errors="coerce").dt.strftime("%Y-%m-%d")
        df.insert(0, "市场口径", label); df["数据源"] = f"交易所/AKShare {fn_name}"; frames.append(df)
    if frames: pd.concat(frames, ignore_index=True, sort=False).to_csv(OUT/"15_融资融券市场汇总_20150101_20260801.csv", index=False, encoding="utf-8-sig")


if TASK == "15-summary": run_summary()
else: run_year(int(TASK.split("-")[1]))
pd.DataFrame(LOGS, columns=["步骤", "状态", "行数", "耗时秒", "错误"]).to_csv(OUT/"运行报告.csv", index=False, encoding="utf-8-sig")
pd.DataFrame([["15", "融资融券个股明细按交易日抓取；证券出借独立历史未与融券字段混同"]], columns=["编号", "准确口径"]).to_csv(OUT/"数据口径说明.csv", index=False, encoding="utf-8-sig")
