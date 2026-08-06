from __future__ import annotations

import io
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

OUT = Path(os.environ.get('OUTPUT_DIR', 'output'))
OUT.mkdir(parents=True, exist_ok=True)
END = '20260801'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36', 'Referer':'https://www.cffex.com.cn/'}


def fetch_month(ym: str):
    started=time.time(); errors=[]
    for scheme in ['https','http']:
        url=f'{scheme}://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip'
        try:
            r=requests.get(url,headers=UA,timeout=(8,25),allow_redirects=True)
            if r.status_code!=200 or not zipfile.is_zipfile(io.BytesIO(r.content)):
                errors.append(f'{url}: status={r.status_code}, bytes={len(r.content)}'); continue
            frames=[]
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                for name in zf.namelist():
                    m=re.search(r'(\d{8})_1\.csv$',name)
                    if not m or m.group(1)>'20260801': continue
                    raw=zf.read(name); text=None
                    for enc in ['gb18030','gb2312','utf-8-sig','utf-8']:
                        try: text=raw.decode(enc); break
                        except UnicodeDecodeError: pass
                    if text is None: continue
                    df=pd.read_csv(io.StringIO(text)); df.columns=[str(c).strip() for c in df.columns]
                    code=next((c for c in df.columns if '合约代码' in c),df.columns[0])
                    df[code]=df[code].astype(str).str.strip()
                    df=df[df[code].str.match(r'^(IO|MO|HO)',na=False)].copy()
                    if df.empty: continue
                    df.insert(0,'交易日期',pd.to_datetime(m.group(1)).strftime('%Y-%m-%d'))
                    df['交易所']='中国金融期货交易所'; df['源月份']=ym; df['数据源']=url
                    frames.append(df)
            return ym, frames, '', round(time.time()-started,2)
        except Exception as exc:
            errors.append(f'{url}: {type(exc).__name__}: {exc}')
    return ym, [], ' | '.join(errors), round(time.time()-started,2)

months=pd.period_range('2019-12','2026-08',freq='M').strftime('%Y%m').tolist()
results=[]; logs=[]
with ThreadPoolExecutor(max_workers=8) as pool:
    futs=[pool.submit(fetch_month,m) for m in months]
    for fut in as_completed(futs):
        ym,frames,err,elapsed=fut.result(); results.extend(frames); logs.append({'月份':ym,'状态':'成功' if not err else '失败','记录数':sum(len(x) for x in frames),'耗时秒':elapsed,'错误':err})
if results:
    out=pd.concat(results,ignore_index=True,sort=False)
    code=next((c for c in out.columns if '合约代码' in c),None)
    keys=[c for c in ['交易日期',code] if c]
    out=out.drop_duplicates(subset=keys,keep='last').sort_values(keys)
    out.to_csv(OUT/'12C_中金所股指期权IO_MO_HO全合约日行情_20191223_20260801.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(logs).sort_values('月份').to_csv(OUT/'12C_中金所月度下载报告.csv',index=False,encoding='utf-8-sig')
pd.DataFrame([['12C','中金所官方月度历史ZIP；筛选IO/MO/HO逐合约日行情；日期上限2026-08-01']],columns=['编号','口径']).to_csv(OUT/'数据口径说明.csv',index=False,encoding='utf-8-sig')
