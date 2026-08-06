from __future__ import annotations

import io
import json
import os
import re
import time
import traceback
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
from bs4 import BeautifulSoup

OUT = Path(os.environ.get('OUTPUT_DIR', 'output'))
OUT.mkdir(parents=True, exist_ok=True)
LOGS: list[dict] = []
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36'}


def clean_df(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime('%Y-%m-%d')
    return df


def save_csv(name: str, frames: list[pd.DataFrame]) -> None:
    good = [clean_df(x) for x in frames if x is not None and not x.empty]
    if good:
        df = pd.concat(good, ignore_index=True, sort=False)
    else:
        df = pd.DataFrame([{'状态': '未取得数据', '说明': '详见运行报告.csv'}])
    df.to_csv(OUT / name, index=False, encoding='utf-8-sig')


def run_step(task: str, label: str, fn: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    started = time.time()
    try:
        df = clean_df(fn())
        LOGS.append({'任务': task, '步骤': label, '状态': '成功', '行数': len(df), '耗时秒': round(time.time()-started, 2), '错误': ''})
        return df
    except Exception as exc:
        LOGS.append({'任务': task, '步骤': label, '状态': '失败', '行数': 0, '耗时秒': round(time.time()-started, 2), '错误': f'{type(exc).__name__}: {exc}'})
        print(f'[FAIL] {task} {label}: {exc}')
        traceback.print_exc()
        return pd.DataFrame()


def with_meta(df: pd.DataFrame, dataset: str, source: str, coverage: str, note: str = '') -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df.insert(0, '子数据集', dataset)
    df['数据源'] = source
    df['覆盖说明'] = coverage
    df['口径说明'] = note
    return df


def task_02(ak):
    frames = []
    spot = run_step('02', 'A股当前市值快照', ak.stock_zh_a_spot_em)
    frames.append(with_meta(spot, '当前市值快照', '东方财富/AKShare stock_zh_a_spot_em', '当前时点', '流通市值不是严格自由流通市值；免费源无法完整复刻Choice自由流通口径'))
    for year in range(2015, datetime.now().year + 1):
        for suffix, label in [('0630', '半年报'), ('1231', '年报')]:
            period = f'{year}{suffix}'
            df = run_step('02', f'分红配送-{period}', lambda p=period: ak.stock_fhps_em(date=p))
            if not df.empty:
                df['报告期'] = period
                frames.append(with_meta(df, f'分红送转-{label}', '东方财富/AKShare stock_fhps_em', '2015年至今半年报及年报口径', '包含送转、现金分红、总股本及公告/登记/除权日期'))
            time.sleep(0.15)
    save_csv('02_自由流通股本市值与公司行动_20150101至今.csv', frames)


def task_11(ak):
    frames = []
    for symbol in ['北向资金', '沪股通', '深股通']:
        df = run_step('11', f'互联互通资金历史-{symbol}', lambda s=symbol: ak.stock_hsgt_hist_em(symbol=s))
        frames.append(with_meta(df, f'资金历史-{symbol}', '东方财富/AKShare stock_hsgt_hist_em', '接口可返回的完整历史', '市场总量口径'))
    for indicator in ['今日排行', '5日排行', '10日排行', '月排行', '季排行', '年排行']:
        df = run_step('11', f'北向持股-{indicator}', lambda i=indicator: ak.stock_hsgt_hold_stock_em(market='北向', indicator=i))
        frames.append(with_meta(df, f'北向持股-{indicator}', '东方财富/AKShare stock_hsgt_hold_stock_em', '当前滚动窗口', '个股历史并非2014年至今逐日全覆盖；保留源站当前可查询范围'))
    save_csv('11_北向持股与自由流通占比_20141117至今.csv', frames)


def task_12(ak):
    frames = []
    for fn_name, label in [
        ('option_contract_info_ctp', '全部当前期权合约信息'),
        ('option_current_day_sse', '上交所当日合约'),
        ('option_current_day_szse', '深交所当日合约'),
    ]:
        if hasattr(ak, fn_name):
            df = run_step('12', label, getattr(ak, fn_name))
            frames.append(with_meta(df, label, f'交易所/AKShare {fn_name}', '当前有效及接口保留合约', '免费公开接口无法稳定还原2015年以来全部已到期合约逐日行情'))
    try:
        codes = ak.option_sse_codes_sina(symbol='看涨期权', trade_date='', underlying='510050')
        flat_codes = []
        if isinstance(codes, pd.DataFrame):
            flat_codes = codes.astype(str).stack().tolist()
        elif isinstance(codes, (list, tuple)):
            flat_codes = list(codes)
        flat_codes = [re.sub(r'\D', '', str(x)) for x in flat_codes if re.search(r'\d{8}', str(x))][:120]
        for code in flat_codes:
            df = run_step('12', f'50ETF活跃合约历史-{code}', lambda c=code: ak.option_sse_daily_sina(symbol=c))
            if not df.empty:
                df['合约代码'] = code
                frames.append(with_meta(df, '50ETF当前活跃合约日频历史', '新浪/AKShare option_sse_daily_sina', '当前活跃合约可回溯历史', '仅当前仍可查询合约，不等于全历史合约库'))
            time.sleep(0.05)
    except Exception as exc:
        LOGS.append({'任务': '12', '步骤': '当前活跃合约历史批量', '状态': '失败', '行数': 0, '耗时秒': 0, '错误': f'{type(exc).__name__}: {exc}'})
    save_csv('12_A股期权全合约日行情_20150209至今.csv', frames)


def task_13(ak):
    frames = []
    funcs = [
        'macro_china_gdp_yearly', 'macro_china_cpi_yearly', 'macro_china_cpi_monthly',
        'macro_china_ppi_yearly', 'macro_china_pmi_yearly', 'macro_china_cx_pmi_yearly',
        'macro_china_cx_services_pmi_yearly', 'macro_china_non_man_pmi',
        'macro_china_exports_yoy', 'macro_china_imports_yoy', 'macro_china_trade_balance',
        'macro_china_industrial_production_yoy', 'macro_china_m2_yearly',
        'macro_china_fx_reserves_yearly', 'macro_china_urban_unemployment'
    ]
    for fn_name in funcs:
        if hasattr(ak, fn_name):
            df = run_step('13', fn_name, getattr(ak, fn_name))
            frames.append(with_meta(df, fn_name, f'金十/国家统计口径/AKShare {fn_name}', '2010年至今或接口最早可得日期', '优先保留今值、预测值、前值；各指标起始日期不同'))
            time.sleep(0.1)
    save_csv('13_中国宏观一致预期与公布值_20100101至今.csv', frames)


def task_14(ak):
    frames = []
    for fn_name, label in [('macro_china_shibor_all', 'Shibor全期限'), ('macro_china_lpr', 'LPR')]:
        if hasattr(ak, fn_name):
            df = run_step('14', label, getattr(ak, fn_name))
            frames.append(with_meta(df, label, f'公开宏观数据/AKShare {fn_name}', '接口全部历史', '银行间基准利率'))
    if hasattr(ak, 'rate_interbank'):
        combos = [('上海银行同业拆借市场', 'Shibor人民币', x) for x in ['隔夜', '1周', '2周', '1月', '3月', '6月', '9月', '1年']]
        combos += [('中国银行间同业拆借市场', 'Chibor人民币', x) for x in ['隔夜', '1周', '2周', '1月', '3月', '6月', '9月', '1年']]
        for market, symbol, indicator in combos:
            df = run_step('14', f'{symbol}-{indicator}', lambda m=market, s=symbol, i=indicator: ak.rate_interbank(market=m, symbol=s, indicator=i))
            if not df.empty:
                df['市场'] = market
                df['品种'] = symbol
                df['期限'] = indicator
                frames.append(with_meta(df, f'{symbol}-{indicator}', '全球银行间拆借利率/AKShare rate_interbank', '接口全部历史', '未包含逐家机构融出方明细'))
            time.sleep(0.05)
    save_csv('14_银行间利率与机构融出_20100101至今.csv', frames)


def latest_trade_dates(ak, n=12):
    try:
        cal = ak.tool_trade_date_hist_sina()
        col = cal.columns[0]
        vals = pd.to_datetime(cal[col], errors='coerce').dropna()
        vals = vals[vals <= pd.Timestamp.today()].sort_values().tail(n)
        return [x.strftime('%Y%m%d') for x in vals[::-1]]
    except Exception:
        today = date.today()
        return [(today - timedelta(days=i)).strftime('%Y%m%d') for i in range(20)]


def task_15(ak):
    frames = []
    for fn_name, label in [('macro_china_market_margin_sh', '上海市场两融历史总量'), ('macro_china_market_margin_sz', '深圳市场两融历史总量')]:
        if hasattr(ak, fn_name):
            df = run_step('15', label, getattr(ak, fn_name))
            frames.append(with_meta(df, label, f'交易所/AKShare {fn_name}', '接口全部历史', '市场汇总口径'))
    dates = latest_trade_dates(ak)
    for fn_name, label in [('stock_margin_detail_sse', '沪市个股两融明细'), ('stock_margin_detail_szse', '深市个股两融明细')]:
        if not hasattr(ak, fn_name):
            continue
        for d in dates:
            df = run_step('15', f'{label}-{d}', lambda f=getattr(ak, fn_name), day=d: f(date=day))
            if not df.empty:
                df['交易日期'] = d
                frames.append(with_meta(df, label, f'交易所/AKShare {fn_name}', '最新可得交易日快照', '个股明细；证券出借字段以源接口实际返回为准'))
                break
    save_csv('15_个股融资融券与证券出借_20150101至今.csv', frames)


def task_17(ak):
    frames = []
    year_now = datetime.now().year
    periods = []
    for y in range(2015, year_now + 1):
        periods += [f'{y}一季', f'{y}半年报', f'{y}三季', f'{y}年报']
    for period in periods:
        df = run_step('17', f'预约及实际披露-{period}', lambda p=period: ak.stock_report_disclosure(market='沪深京', period=p))
        if not df.empty:
            df['报告期标签'] = period
            report_type = '一季报' if '一季' in period else ('半年报' if '半年报' in period else ('三季报' if '三季' in period else '年报'))
            df['报告类型'] = report_type
            frames.append(with_meta(df, '定期报告预约及实际披露', '巨潮资讯/AKShare stock_report_disclosure', '2015年至今（以接口接受的历史期数为准）', '这里的实际披露日即定期报告实际披露时间；十大股东通常随定期报告披露'))
        time.sleep(0.08)
    save_csv('17_十大股东报告实际公告日_20150930至今.csv', frames)


def task_18():
    frames = []
    page = requests.get('https://data.bis.org/bulkdownload', headers=UA, timeout=60)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, 'html.parser')
    target = None
    for a in soup.find_all('a', href=True):
        text = ' '.join(a.get_text(' ', strip=True).split()).lower()
        if 'global liquidity indicators' in text and 'csv' in text and 'flat' in text:
            target = requests.compat.urljoin(page.url, a['href'])
            break
    if target is None:
        for a in soup.find_all('a', href=True):
            text = ' '.join(a.get_text(' ', strip=True).split()).lower()
            if 'global liquidity indicators' in text and 'csv' in text:
                target = requests.compat.urljoin(page.url, a['href'])
                break
    if target is None:
        raise RuntimeError('未在BIS批量下载页定位Global liquidity CSV链接')
    resp = requests.get(target, headers=UA, timeout=180)
    resp.raise_for_status()
    content = resp.content
    files = []
    if zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for n in zf.namelist():
                if n.lower().endswith('.csv'):
                    raw = zf.read(n)
                    for enc in ['utf-8-sig', 'utf-8', 'latin1']:
                        try:
                            df = pd.read_csv(io.BytesIO(raw), encoding=enc, low_memory=False)
                            files.append((n, df))
                            break
                        except Exception:
                            continue
    else:
        files.append(('BIS_GLI.csv', pd.read_csv(io.BytesIO(content), low_memory=False)))
    for n, df in files:
        mask = pd.Series(False, index=df.index)
        for col in df.columns:
            if any(k in str(col).upper() for k in ['CURRENCY', 'DENOM', 'CURR']):
                s = df[col].astype(str).str.upper()
                mask |= s.str.contains('USD|US DOLLAR', regex=True, na=False)
        use = df[mask].copy() if mask.any() else df.copy()
        use.insert(0, '源文件', n)
        frames.append(with_meta(use, 'BIS全球美元信用', target, 'BIS Global Liquidity Indicators季度全历史', '优先筛选美元计价；若元数据字段无法识别则保留整套GLI数据'))
    save_csv('18_BIS全球美元信用_季度.csv', frames)


def task_19():
    frames = []
    base = 'https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm'
    page = requests.get(base, headers=UA, timeout=60)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, 'html.parser')
    links = []
    current = datetime.now().year
    for a in soup.find_all('a', href=True):
        text = ' '.join(a.get_text(' ', strip=True).split())
        href = requests.compat.urljoin(base, a['href'])
        if re.search(r'20(1[5-9]|2[0-9])', text + ' ' + href) and any(x in href.lower() for x in ['fut_fin', 'futfin', 'financial_futures']):
            if href.lower().endswith(('.xls', '.xlsx', '.zip')):
                links.append(href)
    if not links:
        links = [f'https://www.cftc.gov/files/dea/history/fut_fin_xls_{y}.zip' for y in range(2015, current + 1)]
    links = list(dict.fromkeys(links))
    for link in links:
        try:
            resp = requests.get(link, headers=UA, timeout=120)
            if resp.status_code != 200:
                continue
            data = resp.content
            candidates = []
            if zipfile.is_zipfile(io.BytesIO(data)):
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for n in zf.namelist():
                        if n.lower().endswith(('.xls', '.xlsx', '.csv', '.txt')):
                            candidates.append((n, zf.read(n)))
            else:
                candidates.append((Path(link).name, data))
            for n, raw in candidates:
                if n.lower().endswith(('.xls', '.xlsx')):
                    df = pd.read_excel(io.BytesIO(raw))
                else:
                    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
                name_col = next((c for c in df.columns if 'Market_and_Exchange_Names' in str(c) or 'Market and Exchange Names' in str(c)), df.columns[0])
                pat = r'DOLLAR INDEX|U\.S\. TREASURY|US TREASURY|S&P|NASDAQ|DOW JONES|RUSSELL|VIX|SOFR|FED FUNDS'
                filt = df[df[name_col].astype(str).str.contains(pat, case=False, regex=True, na=False)].copy()
                if not filt.empty:
                    filt.insert(0, '源文件', n)
                    frames.append(with_meta(filt, 'CFTC金融期货仓位', link, '2015年至今周频TFF Futures Only', '筛选美元指数、美债、股指及主要美元利率期货'))
        except Exception as exc:
            LOGS.append({'任务': '19', '步骤': link, '状态': '失败', '行数': 0, '耗时秒': 0, '错误': f'{type(exc).__name__}: {exc}'})
    save_csv('19_CFTC美元美债股指仓位_周频.csv', frames)


def main():
    import akshare as ak
    for num, func in [
        ('02', lambda: task_02(ak)), ('11', lambda: task_11(ak)), ('12', lambda: task_12(ak)),
        ('13', lambda: task_13(ak)), ('14', lambda: task_14(ak)), ('15', lambda: task_15(ak)),
        ('17', lambda: task_17(ak)), ('18', task_18), ('19', task_19)
    ]:
        started = time.time()
        try:
            print(f'===== TASK {num} START =====')
            func()
            LOGS.append({'任务': num, '步骤': '任务汇总', '状态': '完成', '行数': '', '耗时秒': round(time.time()-started, 2), '错误': ''})
        except Exception as exc:
            LOGS.append({'任务': num, '步骤': '任务汇总', '状态': '失败', '行数': 0, '耗时秒': round(time.time()-started, 2), '错误': f'{type(exc).__name__}: {exc}'})
            traceback.print_exc()
    pd.DataFrame(LOGS).to_csv(OUT / '运行报告.csv', index=False, encoding='utf-8-sig')
    manifest = {
        'generated_at_utc': datetime.utcnow().isoformat(),
        'files': [p.name for p in sorted(OUT.glob('*.csv'))],
        'note': '免费公开源最佳努力结果；不等同Choice商业数据库完整口径。'
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
