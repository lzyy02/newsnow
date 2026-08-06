#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import export_disclosures_ultrafast as exporter

exporter.APPOINT_API = "https://www.cninfo.com.cn/new/information/getPrbookInfo"


def appointment_task(period: pd.Timestamp, sleep: float):
    for attempt in range(1, 4):
        try:
            return exporter.fetch_appointments(period, sleep)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(attempt)


def correction_task(year: int, label: str, keyword: str, start: str, end: str, sleep: float):
    y_start = max(pd.Timestamp(start), pd.Timestamp(year, 1, 1)).strftime("%Y%m%d")
    y_end = min(pd.Timestamp(end), pd.Timestamp(year, 12, 31)).strftime("%Y%m%d")
    for attempt in range(1, 4):
        try:
            x = exporter.ak.stock_zh_a_disclosure_report_cninfo(
                symbol="", market="沪深京", keyword=keyword, category="补充更正",
                start_date=y_start, end_date=y_end,
            )
            if x is None or x.empty:
                return pd.DataFrame(), f"{year}-{label}"
            x = x.copy()
            x["股票代码"] = x["代码"].map(exporter.code6)
            x["公告标题"] = x["公告标题"].astype(str).str.replace(r"<[^>]+>", "", regex=True)
            x["公告时间"] = pd.to_datetime(x["公告时间"], errors="coerce")
            x["报告期"] = x["公告标题"].map(exporter.infer_period)
            x = x.dropna(subset=["股票代码", "报告期", "公告时间"])
            return x, f"{year}-{label}"
        except Exception:
            if attempt == 3:
                raise
            time.sleep(attempt)
        finally:
            time.sleep(sleep)


def aggregate_corrections(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [x for x in frames if x is not None and not x.empty]
    if not frames:
        return pd.DataFrame(columns=["股票代码", "报告期", "更正公告日期", "更正公告标题", "更正公告URL"])
    x = pd.concat(frames, ignore_index=True, sort=False)
    x = x.drop_duplicates(["股票代码", "报告期", "公告时间", "公告标题"])
    x = x.sort_values(["股票代码", "报告期", "公告时间"])
    return x.groupby(["股票代码", "报告期"], as_index=False).agg({
        "公告时间": lambda s: "; ".join(pd.to_datetime(s).dt.strftime("%Y-%m-%d %H:%M:%S")),
        "公告标题": lambda s: "；".join(dict.fromkeys(s.astype(str))),
        "公告链接": lambda s: "；".join(dict.fromkeys(s.astype(str))),
    }).rename(columns={"公告时间": "更正公告日期", "公告标题": "更正公告标题", "公告链接": "更正公告URL"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--end-date", default="2026-08-06")
    parser.add_argument("--output", default="A股定期报告披露事件_20150101_20260806.xlsx")
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-corrections", action="store_true")
    args = parser.parse_args()

    log: list[dict] = []
    periods = exporter.report_periods(args.start_date, args.end_date)
    appointment_frames: list[pd.DataFrame] = []
    print(f"并行下载 {len(periods)} 个报告期", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(appointment_task, p, args.sleep): p for p in periods}
        for n, future in enumerate(as_completed(futures), 1):
            period = futures[future]
            try:
                x = future.result()
                appointment_frames.append(x)
                log.append({"任务": "预约披露", "键": str(period.date()), "状态": "成功", "行数": len(x), "错误": ""})
                print(f"预约披露 {n}/{len(periods)} {period.date()} {len(x):,}行", flush=True)
            except Exception as exc:
                log.append({"任务": "预约披露", "键": str(period.date()), "状态": "失败", "行数": 0, "错误": str(exc)})
                print(f"预约披露失败 {period.date()}: {exc}", flush=True)
    if not appointment_frames:
        raise SystemExit("预约披露数据全部失败")
    result = pd.concat(appointment_frames, ignore_index=True, sort=False)

    correction_frames: list[pd.DataFrame] = []
    if not args.skip_corrections:
        keywords = [("年报", "年度报告"), ("半年报", "半年度报告"),
                    ("一季报", "第一季度报告"), ("三季报", "第三季度报告")]
        tasks = [(year, label, keyword)
                 for year in range(pd.Timestamp(args.start_date).year, pd.Timestamp(args.end_date).year + 1)
                 for label, keyword in keywords]
        print(f"并行下载 {len(tasks)} 组更正公告", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
            futures = {
                pool.submit(correction_task, year, label, keyword, args.start_date, args.end_date, args.sleep): (year, label)
                for year, label, keyword in tasks
            }
            for n, future in enumerate(as_completed(futures), 1):
                year, label = futures[future]
                key = f"{year}-{label}"
                try:
                    x, _ = future.result()
                    if not x.empty:
                        correction_frames.append(x)
                    log.append({"任务": "更正公告", "键": key, "状态": "成功", "行数": len(x), "错误": ""})
                    print(f"更正公告 {n}/{len(tasks)} {key} {len(x):,}行", flush=True)
                except Exception as exc:
                    log.append({"任务": "更正公告", "键": key, "状态": "失败", "行数": 0, "错误": str(exc)})
                    print(f"更正公告失败 {key}: {exc}", flush=True)

    corrections = aggregate_corrections(correction_frames)
    result = result.merge(corrections, on=["股票代码", "报告期"], how="left")
    result["更正公告来源"] = result["更正公告日期"].notna().map({True: exporter.CNINFO_SOURCE, False: ""})
    calendar = exporter.trade_calendar(args.start_date, args.end_date)
    result["公告后首个可交易日"] = result["实际公告日期时间"].map(lambda x: exporter.next_trade_day(x, calendar))
    event_date = result["实际公告日期时间"].fillna(result["预约披露日期"])
    result = result[(event_date >= pd.Timestamp(args.start_date)) & (event_date <= pd.Timestamp(args.end_date))].copy()
    result["数据下载状态"] = "已完成"
    result["口径说明"] = "实际公告日期来自巨潮预约披露主表，历史口径通常精确到日，不虚构时分秒；公告后首个可交易日统一取公告日期之后的第一个交易日。更正公告按标题中的报告年度与报告类型匹配；下载日志保留失败项。"
    columns = ["股票代码", "股票简称", "交易所", "报告期", "报告类型", "预约披露日期", "一次变更日期", "二次变更日期", "三次变更日期",
               "实际公告日期时间", "实际公告时间精度", "公告后首个可交易日", "公告来源", "公告来源URL",
               "更正公告日期", "更正公告标题", "更正公告来源", "更正公告URL", "数据下载状态", "口径说明"]
    for col in columns:
        if col not in result.columns:
            result[col] = ""
    result = result[columns].drop_duplicates(["股票代码", "报告期"]).sort_values(["报告期", "股票代码"]).reset_index(drop=True)
    exporter.write_excel(result, log, Path(args.output))
    print(f"完成 {args.output}，共 {len(result):,} 行", flush=True)


if __name__ == "__main__":
    main()
