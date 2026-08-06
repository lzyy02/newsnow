from __future__ import annotations

import io
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

TASK = os.environ.get("TASK", "12-sse")
END = "20260801"
OUT = Path(os.environ.get("OUTPUT_DIR", "output")); OUT.mkdir(parents=True, exist_ok=True)
LOGS = []
UA = {"User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36"}


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


def trade_dates(start):
    cal = safe("交易日历", ak.tool_trade_date_hist_sina)
    if cal.empty: return []
    s = pd.to_datetime(cal.iloc[:, 0], errors="coerce").dropna(); s = s[(s >= pd.Timestamp(start)) & (s <= pd.Timestamp(END))]
    return s.sort_values().dt.strftime("%Y%m%d").tolist()


def exchange_stats(market):
    if market == "sse": start, label, fn = "20150209", "上交所", ak.option_daily_stats_sse
    else: start, label, fn = "20191223", "深交所", ak.option_daily_stats_szse
    out = OUT/f"12_{label}ETF期权日度概况_{start}_{END}.csv"
    def one(d):
        df = safe(f"{label}-{d}", lambda: fn(date=d))
        if df.empty: return df
        df["交易所"] = label; df["数据源"] = f"{label}官方/AKShare option_daily_stats_{market}"; return df
    buf = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in as_completed([pool.submit(one, d) for d in trade_dates(start)]):
            df = fut.result()
            if not df.empty: buf.append(df)
            if len(buf) >= 100: append_csv(out, pd.concat(buf, ignore_index=True, sort=False)); buf.clear()
    if buf: append_csv(out, pd.concat(buf, ignore_index=True, sort=False))


def cffex():
    out = OUT/"12_中金所股指期权全合约日行情_20191223_20260801.csv"
    for month in pd.period_range("2019-12", "2026-08", freq="M"):
        ym = month.strftime("%Y%m"); url = f"http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"; t = time.time()
        try:
            r = requests.get(url, headers=UA, timeout=90); r.raise_for_status()
            if not zipfile.is_zipfile(io.BytesIO(r.content)): raise ValueError("not zip")
            frames = []
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                for name in zf.namelist():
                    m = re.search(r"(\d{8})_1\.csv$", name)
                    if not m or m.group(1) > END: continue
                    raw = zf.read(name); text = None
                    for enc in ["gb2312", "gb18030", "utf-8-sig"]:
                        try: text = raw.decode(enc); break
                        except UnicodeDecodeError: pass
                    if text is None: continue
                    df = pd.read_csv(io.StringIO(text)); df.columns = [str(c).strip() for c in df.columns]
                    code_col = next((c for c in df.columns if "合约代码" in c), df.columns[0])
                    df[code_col] = df[code_col].astype(str).str.strip(); df = df[df[code_col].str.match(r"^(IO|MO|HO)")].copy()
                    if df.empty: continue
                    df.insert(0, "交易日期", pd.to_datetime(m.group(1)).strftime("%Y-%m-%d")); df["交易所"] = "中国金融期货交易所"; df["数据源"] = url; frames.append(df)
            if frames: append_csv(out, pd.concat(frames, ignore_index=True, sort=False))
            LOGS.append([f"中金所-{ym}", "成功", sum(len(x) for x in frames), round(time.time()-t, 2), ""])
        except Exception as exc:
            LOGS.append([f"中金所-{ym}", "失败", 0, round(time.time()-t, 2), f"{type(exc).__name__}: {exc}"])


if TASK == "12-sse": exchange_stats("sse")
elif TASK == "12-szse": exchange_stats("szse")
else: cffex()
pd.DataFrame(LOGS, columns=["步骤", "状态", "行数", "耗时秒", "错误"]).to_csv(OUT/"运行报告.csv", index=False, encoding="utf-8-sig")
pd.DataFrame([
    ["中金所IO/MO/HO", "全合约日行情"],
    ["上交所/深交所ETF期权", "公开接口日度概况；不冒充全合约价格"]
], columns=["范围", "准确口径"]).to_csv(OUT/"数据口径说明.csv", index=False, encoding="utf-8-sig")
