"""Public Eastmoney HTTPS endpoints. Fail visibly; never substitute sample quotes."""
import json
import math
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from .core import now

BASE='https://push2.eastmoney.com/api/qt/'

def get(url, params):
    req=urllib.request.Request(url+'?'+urllib.parse.urlencode(params),headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'})
    with urllib.request.urlopen(req,timeout=12) as r:
        data=json.load(r)
    if data.get('data') is None: raise RuntimeError('行情源未返回数据')
    return data['data']

def numeric(x):
    try:
        result=float(x)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError): return None

def listing(fs, count=100):
    data=get(BASE+'clist/get',{'pn':1,'pz':count,'po':1,'np':1,'fltt':2,'invt':2,'fid':'f3','fs':fs,'fields':'f12,f14,f2,f3,f6,f62,f124'})
    rows=data.get('diff') or []
    if isinstance(rows,dict): rows=list(rows.values())
    return [{'code':x['f12'],'name':x['f14'],'price':numeric(x.get('f2')),'change':numeric(x.get('f3')),'amount':numeric(x.get('f6')),'net_flow':numeric(x.get('f62')),'timestamp':numeric(x.get('f124'))} for x in rows]

def secid(code): return ('1.' if code.startswith(('5','6','9')) else '0.')+code

def quotes(codes):
    if not codes: return []
    data=get(BASE+'ulist.np/get',{'secids':','.join(secid(x) for x in codes),'fltt':2,'fields':'f12,f14,f2,f3,f6,f62,f124'})
    return [{'code':x['f12'],'name':x['f14'],'price':numeric(x.get('f2')),'change':numeric(x.get('f3')),'amount':numeric(x.get('f6')),'net_flow':numeric(x.get('f62')),'timestamp':numeric(x.get('f124'))} for x in data.get('diff',[])]

def daily(code, include_current=False):
    data=get('https://push2his.eastmoney.com/api/qt/stock/kline/get',{'secid':secid(code),'klt':101,'fqt':1,'lmt':40,'end':'20500101','fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57'})
    today=now().date().isoformat()
    closed=now().hour>=15
    result=[]
    for line in data.get('klines',[]):
        x=line.split(',')
        if x[0]>today or (x[0]==today and not closed and not include_current): continue
        result.append({'date':x[0],'close':float(x[2]),'volume':float(x[5])})
    return result

def overview():
    funcs={
      'indices':lambda:indices(),
      'sectors':lambda:listing('m:90+t:2',30),
      # Deliberately bounded candidates, not claimed to be whole-market screening.
      'universe':lambda:listing('m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',100),
    }
    out={}; errors=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={k:pool.submit(f) for k,f in funcs.items()}
        for k,j in jobs.items():
            try: out[k]=j.result()
            except Exception as e: out[k]=[];errors.append(k+': '+str(e))
    out['errors']=errors
    return out


def indices():
    data=get(BASE+'ulist.np/get',{'secids':'1.000001,0.399001,0.399006,1.000300,1.000852','fltt':2,'fields':'f12,f14,f2,f3,f6,f124'})
    return [{'code':x['f12'],'name':x['f14'],'price':numeric(x.get('f2')),'change':numeric(x.get('f3')),'amount':numeric(x.get('f6')),'timestamp':numeric(x.get('f124'))} for x in data.get('diff',[])]
