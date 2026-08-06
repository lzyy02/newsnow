from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import akshare as ak
import pandas as pd

START = "20150101"
END = "20260801"
OUT = Path(os.environ.get("OUTPUT_DIR", "output"))
OUT.mkdir(parents=True, exist_ok=True)
TASK = os.environ.get("TASK", "02-actions")
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


def add_list(frames, df, label):
    if df.empty: return
    cc = next((c for c in df.columns if c in ["证券代码", "A股代码"] or "代码" in c or "code" in c.lower()), None)
    nc = next((c for c in df.columns if c in ["证券简称", "A股简称"] or "简称" in c or "名称" in c or "name" in c.lower()), None)
    if cc and nc:
        x = df[[cc, nc]].copy(); x.columns = ["证券代码", "证券简称"]; x["证券来源"] = label; frames.append(x)


def universe():
    frames = []
    for step, fn in [
        ("上交所主板A股", lambda: ak.stock_info_sh_name_code(symbol="主板A股")),
        ("上交所科创板", lambda: ak.stock_info_sh_name_code(symbol="科创板")),
        ("深交所A股", lambda: ak.stock_info_sz_name_code(symbol="A股列表")),
        ("北交所A股", ak.stock_info_bj_name_code),
        ("深交所退市", lambda: ak.stock_info_sz_delist(symbol="终止上市公司")),
    ]:
        add_list(frames, safe(step, fn), step)
    if not frames: return pd.DataFrame(columns=["证券代码", "证券简称", "证券来源"])
    out = pd.concat(frames, ignore_index=True)
    out["证券代码"] = out["证券代码"].astype(str).str.extract(r"(\d{6})", expand=False)
    out = out.dropna(subset=["证券代码"])
    out = out[~out["证券代码"].str.startswith(("200", "900"))]
    return out.drop_duplicates("证券代码").sort_values("证券代码")


def run_shard():
    shard = int(os.environ.get("SHARD_INDEX", "0")); total = int(os.environ.get("SHARD_TOTAL", "8"))
    u = universe().reset_index(drop=True); u = u.iloc[[i for i in range(len(u)) if i % total == shard]]
    out = OUT/f"02A_公司股本变动_20150101_20260801_shard{shard:02d}.csv"; failed = []
    def one(row):
        code, name, source = row
        df = safe(f"股本变动-{code}", lambda: ak.stock_share_change_cninfo(symbol=code, start_date=START, end_date=END))
        if df.empty: failed.append([code, name, source]); return df
        if "证券代码" not in df.columns: df.insert(0, "证券代码", code)
        if "证券简称" not in df.columns: df.insert(1, "证券简称", name)
        for c in ["公告日期", "变动日期"]:
            if c in df.columns:
                d = pd.to_datetime(df[c], errors="coerce"); df = df[d.isna() | ((d >= pd.Timestamp(START)) & (d <= pd.Timestamp(END)))].copy(); df[c] = pd.to_datetime(df[c], errors="coerce").dt.strftime("%Y-%m-%d")
        df["证券来源"] = source; df["数据源"] = "巨潮资讯 p_stock2215 / AKShare stock_share_change_cninfo"; df["口径说明"] = "已流通股份不等同于Choice严格自由流通股本；历史市值需与日行情合并"
        return df
    buf = []; rows = list(u[["证券代码", "证券简称", "证券来源"]].itertuples(index=False, name=None))
    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in as_completed([pool.submit(one, r) for r in rows]):
            df = fut.result()
            if not df.empty: buf.append(df)
            if len(buf) >= 30: append_csv(out, pd.concat(buf, ignore_index=True, sort=False)); buf.clear()
    if buf: append_csv(out, pd.concat(buf, ignore_index=True, sort=False))
    pd.DataFrame(failed, columns=["证券代码", "证券简称", "证券来源"]).to_csv(OUT/f"02A_失败代码_shard{shard:02d}.csv", index=False, encoding="utf-8-sig")


def run_actions():
    frames = []
    for year in range(2015, 2027):
        for suffix, label in [("0630", "半年报"), ("1231", "年报")]:
            period = f"{year}{suffix}"; df = safe(f"分红送转-{period}", lambda p=period: ak.stock_fhps_em(date=p))
            if df.empty: continue
            df["报告期"] = period; df["报告类型"] = label; df["数据源"] = "东方财富/AKShare stock_fhps_em"
            for c in ["最新公告日期", "公告日期", "股权登记日", "除权除息日"]:
                if c in df.columns:
                    d = pd.to_datetime(df[c], errors="coerce"); df = df[d.isna() | (d <= pd.Timestamp(END))].copy(); df[c] = pd.to_datetime(df[c], errors="coerce").dt.strftime("%Y-%m-%d"); break
            frames.append(df)
    if frames: pd.concat(frames, ignore_index=True, sort=False).to_csv(OUT/"02B_公司行动_20150101_20260801.csv", index=False, encoding="utf-8-sig")


if TASK.startswith("02-shard"): run_shard()
else: run_actions()
pd.DataFrame(LOGS, columns=["步骤", "状态", "行数", "耗时秒", "错误"]).to_csv(OUT/"运行报告.csv", index=False, encoding="utf-8-sig")
pd.DataFrame([["02", "巨潮股本变动+东方财富公司行动；严格自由流通市值未伪造，需与用户自有行情合并"]], columns=["编号", "口径说明"]).to_csv(OUT/"数据口径说明.csv", index=False, encoding="utf-8-sig")
