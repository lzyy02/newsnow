#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import akshare as ak
import pandas as pd

APPOINT_SOURCE = "东方财富数据中心-预约披露时间"
APPOINT_URL = "https://data.eastmoney.com/bbsj/"
CNINFO_SOURCE = "巨潮资讯-信息披露公告"
CNINFO_URL = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
REPORT_CATEGORIES = {"一季报": "03-31", "半年报": "06-30", "三季报": "09-30", "年报": "12-31"}


def code6(value) -> str:
    digits = re.sub(r"\D", "", str(value))
    return digits.zfill(6)[-6:] if digits else ""


def exchange(code: str) -> str:
    if code.startswith(("6", "9")):
        return "上交所"
    if code.startswith(("0", "2", "3")):
        return "深交所"
    if code.startswith(("4", "8")):
        return "北交所"
    return "未识别"


def report_periods(start: str, end: str) -> list[pd.Timestamp]:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    values = [p.end_time.normalize() for p in pd.period_range(start_ts, end_ts, freq="Q")]
    prior_annual = pd.Timestamp(start_ts.year - 1, 12, 31)
    values = [prior_annual, *values]
    return sorted(set(x for x in values if x <= end_ts))


def report_type(period: pd.Timestamp) -> str:
    return {3: "一季报", 6: "半年报", 9: "三季报", 12: "年报"}[period.month]


def fetch_appointments(period: pd.Timestamp, sleep: float) -> pd.DataFrame:
    compact = period.strftime("%Y%m%d")
    frames, errors = [], []
    for market in ("沪深A股", "京市A股"):
        try:
            x = ak.stock_yysj_em(symbol=market, date=compact)
            if x is not None and not x.empty:
                frames.append(x.copy())
        except Exception as exc:
            errors.append(f"{market}:{exc}")
        time.sleep(sleep)
    if not frames:
        raise RuntimeError("；".join(errors) or "接口返回空")
    x = pd.concat(frames, ignore_index=True, sort=False)
    x = x.rename(columns={
        "首次预约时间": "预约披露日期", "一次变更日期": "一次变更日期",
        "二次变更日期": "二次变更日期", "三次变更日期": "三次变更日期",
        "实际披露时间": "预约表实际披露日期",
    })
    for col in ["预约披露日期", "一次变更日期", "二次变更日期", "三次变更日期", "预约表实际披露日期"]:
        if col not in x.columns:
            x[col] = pd.NaT
        x[col] = pd.to_datetime(x[col], errors="coerce")
    x["股票代码"] = x["股票代码"].map(code6)
    x["交易所"] = x["股票代码"].map(exchange)
    x["报告期"] = period
    x["报告类型"] = report_type(period)
    keep = ["股票代码", "股票简称", "交易所", "报告期", "报告类型", "预约披露日期",
            "一次变更日期", "二次变更日期", "三次变更日期", "预约表实际披露日期"]
    return x[keep].drop_duplicates(["股票代码", "报告期"], keep="last")


def infer_period(title: str, fallback_category: str | None = None) -> pd.Timestamp | pd.NaT:
    text = re.sub(r"<[^>]+>", "", str(title))
    year_match = re.search(r"(20\d{2})\s*年", text)
    if not year_match:
        return pd.NaT
    year = int(year_match.group(1))
    if re.search(r"第一季度|一季度|一季报", text):
        md = "03-31"
    elif re.search(r"半年度|半年报|中期报告|中报", text):
        md = "06-30"
    elif re.search(r"第三季度|三季度|三季报", text):
        md = "09-30"
    elif re.search(r"年度报告|年报", text):
        md = "12-31"
    elif fallback_category in REPORT_CATEGORIES:
        md = REPORT_CATEGORIES[fallback_category]
    else:
        return pd.NaT
    return pd.Timestamp(f"{year}-{md}")


def is_main_report(title: str, category: str) -> bool:
    text = re.sub(r"<[^>]+>", "", str(title))
    bad = r"摘要|英文版|审计报告|财务报表|问询|回复|提示性公告|取消|致歉|董事会|监事会|独立意见|核查意见|内部控制|社会责任|ESG"
    if re.search(bad, text):
        return False
    required = {
        "一季报": r"第一季度报告|一季度报告|一季报",
        "半年报": r"半年度报告|半年报|中期报告",
        "三季报": r"第三季度报告|三季度报告|三季报",
        "年报": r"年度报告|年报",
    }[category]
    return bool(re.search(required, text))


def fetch_cninfo_category(category: str, start: str, end: str, sleep: float) -> pd.DataFrame:
    x = ak.stock_zh_a_disclosure_report_cninfo(
        symbol="", market="沪深京", keyword="", category=category,
        start_date=start.replace("-", ""), end_date=end.replace("-", ""),
    )
    time.sleep(sleep)
    if x is None or x.empty:
        return pd.DataFrame()
    x = x.copy()
    x["股票代码"] = x["代码"].map(code6)
    x["股票简称_公告"] = x["简称"].astype(str)
    x["公告标题"] = x["公告标题"].astype(str).str.replace(r"<[^>]+>", "", regex=True)
    x["公告时间"] = pd.to_datetime(x["公告时间"], errors="coerce")
    x["报告期"] = x["公告标题"].map(lambda t: infer_period(t, category))
    x["公告类别"] = category
    return x


def fetch_exact_reports(start: str, end: str, sleep: float, log: list[dict]) -> pd.DataFrame:
    frames = []
    first_year, last_year = pd.Timestamp(start).year, pd.Timestamp(end).year
    for year in range(first_year, last_year + 1):
        y_start = max(pd.Timestamp(start), pd.Timestamp(year, 1, 1)).strftime("%Y-%m-%d")
        y_end = min(pd.Timestamp(end), pd.Timestamp(year, 12, 31)).strftime("%Y-%m-%d")
        for category in REPORT_CATEGORIES:
            key = f"{year}-{category}"
            try:
                x = fetch_cninfo_category(category, y_start, y_end, sleep)
                if not x.empty:
                    x = x[x["公告标题"].map(lambda t: is_main_report(t, category))]
                    frames.append(x)
                log.append({"任务": "定期报告公告", "键": key, "状态": "成功", "行数": len(x), "错误": ""})
            except Exception as exc:
                log.append({"任务": "定期报告公告", "键": key, "状态": "失败", "行数": 0, "错误": str(exc)})
    if not frames:
        return pd.DataFrame(columns=["股票代码", "报告期", "实际公告日期时间", "实际公告标题", "实际公告URL"])
    x = pd.concat(frames, ignore_index=True, sort=False).dropna(subset=["股票代码", "报告期", "公告时间"])
    x = x.sort_values(["股票代码", "报告期", "公告时间"])
    x = x.drop_duplicates(["股票代码", "报告期"], keep="first")
    return x.rename(columns={"公告时间": "实际公告日期时间", "公告标题": "实际公告标题", "公告链接": "实际公告URL"})[
        ["股票代码", "报告期", "实际公告日期时间", "实际公告标题", "实际公告URL"]
    ]


def fetch_corrections(start: str, end: str, sleep: float, log: list[dict]) -> pd.DataFrame:
    frames = []
    first_year, last_year = pd.Timestamp(start).year, pd.Timestamp(end).year
    for year in range(first_year, last_year + 1):
        y_start = max(pd.Timestamp(start), pd.Timestamp(year, 1, 1)).strftime("%Y-%m-%d")
        y_end = min(pd.Timestamp(end), pd.Timestamp(year, 12, 31)).strftime("%Y-%m-%d")
        try:
            x = fetch_cninfo_category("补充更正", y_start, y_end, sleep)
            if not x.empty:
                x = x[x["报告期"].notna()]
                frames.append(x)
            log.append({"任务": "更正公告", "键": str(year), "状态": "成功", "行数": len(x), "错误": ""})
        except Exception as exc:
            log.append({"任务": "更正公告", "键": str(year), "状态": "失败", "行数": 0, "错误": str(exc)})
    if not frames:
        return pd.DataFrame(columns=["股票代码", "报告期", "更正公告日期", "更正公告标题", "更正公告URL"])
    x = pd.concat(frames, ignore_index=True, sort=False).dropna(subset=["股票代码", "报告期", "公告时间"])
    x = x.sort_values(["股票代码", "报告期", "公告时间"])
    return x.groupby(["股票代码", "报告期"], as_index=False).agg({
        "公告时间": lambda s: "; ".join(pd.to_datetime(s).dt.strftime("%Y-%m-%d %H:%M:%S")),
        "公告标题": lambda s: "；".join(dict.fromkeys(s.astype(str))),
        "公告链接": lambda s: "；".join(dict.fromkeys(s.astype(str))),
    }).rename(columns={"公告时间": "更正公告日期", "公告标题": "更正公告标题", "公告链接": "更正公告URL"})


def trade_calendar(start: str, end: str) -> pd.DatetimeIndex:
    x = ak.tool_trade_date_hist_sina()
    col = "trade_date" if "trade_date" in x.columns else x.columns[0]
    days = pd.to_datetime(x[col], errors="coerce").dropna().drop_duplicates().sort_values()
    return pd.DatetimeIndex(days[(days >= pd.Timestamp(start) - pd.Timedelta(days=10)) &
                                 (days <= pd.Timestamp(end) + pd.Timedelta(days=30))])


def first_tradable(ts, calendar: pd.DatetimeIndex):
    if pd.isna(ts):
        return pd.NaT
    value = pd.Timestamp(ts)
    day = value.normalize()
    same_day_open = value.hour < 9 or (value.hour == 9 and value.minute < 15)
    if same_day_open and day in calendar:
        return day
    pos = calendar.searchsorted(day, side="right")
    return calendar[pos] if pos < len(calendar) else pd.NaT


def write_excel(result: pd.DataFrame, log: list[dict], path: Path):
    sources = pd.DataFrame([
        [APPOINT_SOURCE, APPOINT_URL, "预约披露日期、历次变更及预约表实际披露日期"],
        [CNINFO_SOURCE, CNINFO_URL, "实际公告日期时间、公告标题、公告链接及补充更正公告"],
    ], columns=["来源", "网址", "用途"])
    with pd.ExcelWriter(path, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm:ss") as writer:
        result.to_excel(writer, sheet_name="披露事件明细", index=False)
        pd.DataFrame(log).to_excel(writer, sheet_name="下载日志", index=False)
        sources.to_excel(writer, sheet_name="数据源", index=False)
        wb, ws = writer.book, writer.sheets["披露事件明细"]
        head = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#17365D", "align": "center", "text_wrap": True})
        wrap = wb.add_format({"text_wrap": True, "valign": "top"})
        for i, name in enumerate(result.columns):
            ws.write(0, i, name, head)
            width = 55 if "标题" in name or "URL" in name or name == "口径说明" else 18
            ws.set_column(i, i, width, wrap if width == 55 else None)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, max(len(result), 1), len(result.columns) - 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--end-date", default="2026-08-06")
    parser.add_argument("--output", default="A股定期报告披露事件_20150101_20260806.xlsx")
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--skip-corrections", action="store_true")
    args = parser.parse_args()

    log, appointment_frames = [], []
    for i, period in enumerate(report_periods(args.start_date, args.end_date), 1):
        print(f"预约披露 {i}: {period.date()}", flush=True)
        try:
            x = fetch_appointments(period, args.sleep)
            appointment_frames.append(x)
            log.append({"任务": "预约披露", "键": str(period.date()), "状态": "成功", "行数": len(x), "错误": ""})
        except Exception as exc:
            log.append({"任务": "预约披露", "键": str(period.date()), "状态": "失败", "行数": 0, "错误": str(exc)})
    if not appointment_frames:
        raise SystemExit("预约披露数据全部失败")

    result = pd.concat(appointment_frames, ignore_index=True, sort=False)
    exact = fetch_exact_reports(args.start_date, args.end_date, args.sleep, log)
    result = result.merge(exact, on=["股票代码", "报告期"], how="left")
    result["实际公告日期时间"] = result["实际公告日期时间"].fillna(result["预约表实际披露日期"])
    result["实际公告时间精度"] = result["实际公告标题"].notna().map({True: "日期时间", False: "日"})
    result["公告来源"] = result["实际公告标题"].notna().map({True: CNINFO_SOURCE, False: APPOINT_SOURCE})
    result["公告来源URL"] = result["实际公告URL"].fillna(APPOINT_URL)

    if args.skip_corrections:
        corrections = pd.DataFrame(columns=["股票代码", "报告期", "更正公告日期", "更正公告标题", "更正公告URL"])
    else:
        corrections = fetch_corrections(args.start_date, args.end_date, args.sleep, log)
    result = result.merge(corrections, on=["股票代码", "报告期"], how="left")
    result["更正公告来源"] = result["更正公告日期"].notna().map({True: CNINFO_SOURCE, False: ""})

    calendar = trade_calendar(args.start_date, args.end_date)
    result["公告后首个可交易日"] = result["实际公告日期时间"].map(lambda x: first_tradable(x, calendar))
    event_date = result["实际公告日期时间"].fillna(result["预约披露日期"])
    result = result[(event_date >= pd.Timestamp(args.start_date)) & (event_date <= pd.Timestamp(args.end_date))].copy()
    result["数据下载状态"] = "已完成"
    result["口径说明"] = "实际公告时间优先取巨潮完整定期报告的最早发布时间；匹配不到时回退预约披露表日期。公告后首个可交易日：盘前公告取当日，否则取下一交易日。"

    columns = ["股票代码", "股票简称", "交易所", "报告期", "报告类型", "预约披露日期", "一次变更日期", "二次变更日期", "三次变更日期",
               "实际公告日期时间", "实际公告时间精度", "公告后首个可交易日", "公告来源", "公告来源URL", "实际公告标题", "实际公告URL",
               "更正公告日期", "更正公告标题", "更正公告来源", "更正公告URL", "数据下载状态", "口径说明"]
    for col in columns:
        if col not in result.columns:
            result[col] = ""
    result = result[columns].sort_values(["报告期", "股票代码"]).reset_index(drop=True)
    write_excel(result, log, Path(args.output))
    print(f"完成 {args.output}，共 {len(result):,} 行", flush=True)


if __name__ == "__main__":
    main()
