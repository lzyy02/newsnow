from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import akshare as ak
import pandas as pd

OUT = Path(os.environ.get('OUTPUT_DIR', 'output')); OUT.mkdir(parents=True, exist_ok=True)
START='20150101'; END='20260801'; LOGS=[]


def get_list():
    methods = [
        ('stock_info_a_code_name', lambda: ak.stock_info_a_code_name()),
        ('stock_zh_a_spot_em', lambda: ak.stock_zh_a_spot_em()),
        ('stock_sh_a_spot_em', lambda: ak.stock_sh_a_spot_em()),
    ]
    for attempt in range(8):
        for label, fn in methods:
            t=time.time()
            try:
                df=fn(); df=pd.DataFrame(df); df.columns=[str(c).strip() for c in df.columns]
                cc=next((c for c in df.columns if '代码' in c or 'code' in c.lower()), None)
                nc=next((c for c in df.columns if '简称' in c or '名称' in c or 'name' in c.lower()), None)
                if cc and nc and len(df)>500:
                    x=df[[cc,nc]].copy(); x.columns=['证券代码','证券简称']; x['证券代码']=x['证券代码'].astype(str).str.extract(r'(\d{6})',expand=False)
                    x=x[x['证券代码'].str.startswith(('600','601','603','605','688','689'),na=False)].drop_duplicates('证券代码')
                    if len(x)>1000:
                        LOGS.append([f'列表-{label}', '成功', len(x), round(time.time()-t,2), '']); return x
                LOGS.append([f'列表-{label}', '失败', len(df), round(time.time()-t,2), '返回不足'])
            except Exception as exc:
                LOGS.append([f'列表-{label}', '失败', 0, round(time.time()-t,2), f'{type(exc).__name__}: {exc}'])
            time.sleep(2)
        time.sleep(5)
    return pd.DataFrame(columns=['证券代码','证券简称'])


def safe_share(code):
    err=''; t=time.time()
    for i in range(3):
        try:
            df=ak.stock_share_change_cninfo(symbol=code,start_date=START,end_date=END); df=pd.DataFrame(df)
            if df.empty: raise ValueError('empty')
            df.columns=[str(c).strip() for c in df.columns]
            LOGS.append([f'股本-{code}','成功',len(df),round(time.time()-t,2),'']); return df
        except Exception as exc:
            err=f'{type(exc).__name__}: {exc}'; time.sleep(i+1)
    LOGS.append([f'股本-{code}','失败',0,round(time.time()-t,2),err]); return pd.DataFrame()


def append(path,df):
    if not df.empty: df.to_csv(path,mode='a',header=not path.exists(),index=False,encoding='utf-8-sig')

shard=int(os.environ.get('SHARD_INDEX','0')); total=int(os.environ.get('SHARD_TOTAL','8'))
u=get_list().reset_index(drop=True); u=u.iloc[[i for i in range(len(u)) if i%total==shard]]
out=OUT/f'02A_上海A股公司股本变动_20150101_20260801_shard{shard:02d}.csv'; failed=[]

def one(row):
    code,name=row; df=safe_share(code)
    if df.empty: failed.append([code,name]); return df
    if '证券代码' not in df.columns: df.insert(0,'证券代码',code)
    if '证券简称' not in df.columns: df.insert(1,'证券简称',name)
    for c in ['公告日期','变动日期']:
        if c in df.columns:
            d=pd.to_datetime(df[c],errors='coerce'); df=df[d.isna()|((d>=pd.Timestamp(START))&(d<=pd.Timestamp(END)))].copy(); df[c]=pd.to_datetime(df[c],errors='coerce').dt.strftime('%Y-%m-%d')
    df['证券来源']='上海A股重试列表'; df['数据源']='巨潮资讯 p_stock2215 / AKShare'; df['口径说明']='已流通股份不等同于Choice严格自由流通股本；历史市值需与日行情合并'; return df

buf=[]
with ThreadPoolExecutor(max_workers=5) as pool:
    for fut in as_completed([pool.submit(one,r) for r in u[['证券代码','证券简称']].itertuples(index=False,name=None)]):
        df=fut.result()
        if not df.empty: buf.append(df)
        if len(buf)>=25: append(out,pd.concat(buf,ignore_index=True,sort=False)); buf.clear()
if buf: append(out,pd.concat(buf,ignore_index=True,sort=False))
pd.DataFrame(failed,columns=['证券代码','证券简称']).to_csv(OUT/f'02A_上海失败代码_shard{shard:02d}.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(LOGS,columns=['步骤','状态','行数','耗时秒','错误']).to_csv(OUT/'运行报告.csv',index=False,encoding='utf-8-sig')
