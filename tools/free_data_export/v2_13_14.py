from __future__ import annotations

import os
import time
from pathlib import Path

import akshare as ak
import pandas as pd

TASK = os.environ.get("TASK", "13")
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


def cut(df, start="20100101"):
    dc = next((c for c in ["日期", "date", "月份", "统计时间", "报告日", "TRADE_DATE", "公告时间"] if c in df.columns), None)
    if not dc: return df
    d = pd.to_datetime(df[dc], errors="coerce"); out = df[d.isna() | ((d >= pd.Timestamp(start)) & (d <= pd.Timestamp(END)))].copy(); out[dc] = pd.to_datetime(out[dc], errors="coerce").dt.strftime("%Y-%m-%d"); return out


def run13():
    funcs = {
        "GDP同比": "macro_china_gdp_yearly", "CPI同比": "macro_china_cpi_yearly", "CPI月率": "macro_china_cpi_monthly",
        "PPI同比": "macro_china_ppi_yearly", "官方制造业PMI": "macro_china_pmi_yearly", "财新制造业PMI": "macro_china_cx_pmi_yearly",
        "财新服务业PMI": "macro_china_cx_services_pmi_yearly", "官方非制造业PMI": "macro_china_non_man_pmi",
        "出口同比": "macro_china_exports_yoy", "进口同比": "macro_china_imports_yoy", "贸易帐": "macro_china_trade_balance",
        "工业增加值同比": "macro_china_industrial_production_yoy", "M2同比": "macro_china_m2_yearly",
        "外汇储备": "macro_china_fx_reserves_yearly", "城镇调查失业率": "macro_china_urban_unemployment"
    }
    frames = []
    for label, fn_name in funcs.items():
        if not hasattr(ak, fn_name): LOGS.append([fn_name, "失败", 0, 0, "AKShare无函数"]); continue
        df = safe(label, getattr(ak, fn_name))
        if df.empty: continue
        df = cut(df)
        if "商品" in df.columns: df.rename(columns={"商品": "原指标名"}, inplace=True)
        dc = next((c for c in ["date", "月份", "统计时间"] if c in df.columns), None)
        if dc and "日期" not in df.columns: df.rename(columns={dc: "日期"}, inplace=True)
        df.insert(0, "指标", label); df["数据源"] = f"AKShare {fn_name}（金十/国家统计局等公开口径）"; frames.append(df)
    if frames: pd.concat(frames, ignore_index=True, sort=False).to_csv(OUT/"13_中国宏观一致预期与公布值_20100101_20260801.csv", index=False, encoding="utf-8-sig")


def run14():
    rates = []
    combos = [("上海银行同业拆借市场", "Shibor人民币", x) for x in ["隔夜", "1周", "2周", "1月", "3月", "6月", "9月", "1年"]]
    combos += [("中国银行同业拆借市场", "Chibor人民币", x) for x in ["隔夜", "1周", "2周", "1月", "3月", "6月", "9月", "1年"]]
    for market, symbol, tenor in combos:
        df = safe(f"{symbol}-{tenor}", lambda m=market, s=symbol, t=tenor: ak.rate_interbank(market=m, symbol=s, indicator=t))
        if df.empty: continue
        df = cut(df); df.insert(0, "品种", symbol); df.insert(1, "期限", tenor); df["市场"] = market; df["数据源"] = "东方财富/AKShare rate_interbank"; rates.append(df)
    if rates: pd.concat(rates, ignore_index=True, sort=False).to_csv(OUT/"14A_Shibor与Chibor历史_20100101_20260801.csv", index=False, encoding="utf-8-sig")

    if hasattr(ak, "macro_china_lpr"):
        df = safe("LPR", ak.macro_china_lpr)
        if not df.empty: df = cut(df); df["数据源"] = "全国银行间同业拆借中心/AKShare macro_china_lpr"; df.to_csv(OUT/"14B_LPR历史_20100101_20260801.csv", index=False, encoding="utf-8-sig")

    repo = []
    if hasattr(ak, "repo_rate_query"):
        for symbol in ["回购定盘利率", "银银间回购定盘利率"]:
            df = safe(symbol, lambda s=symbol: ak.repo_rate_query(symbol=s))
            if df.empty: continue
            df = cut(df); df.insert(0, "品种", symbol); df["数据源"] = "中国外汇交易中心/AKShare repo_rate_query"; repo.append(df)
    if repo: pd.concat(repo, ignore_index=True, sort=False).to_csv(OUT/"14C_FR与FDR回购定盘利率_20100101_20260801.csv", index=False, encoding="utf-8-sig")


if TASK == "13": run13()
else: run14()
pd.DataFrame(LOGS, columns=["步骤", "状态", "行数", "耗时秒", "错误"]).to_csv(OUT/"运行报告.csv", index=False, encoding="utf-8-sig")
pd.DataFrame([[TASK, "所有日期均截断至2026-08-01；14不包含逐家机构融出方明细"]], columns=["编号", "口径说明"]).to_csv(OUT/"数据口径说明.csv", index=False, encoding="utf-8-sig")
