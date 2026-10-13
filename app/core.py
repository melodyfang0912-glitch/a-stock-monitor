import datetime as dt
import json
import math
import os
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('MONITOR_DATA_DIR', str(ROOT / '.data')))
TZ = ZoneInfo('Asia/Shanghai')

# Screenshots are an initial snapshot, not live quotes. ETF identity needs confirmation.
INITIAL = [
 ('002993','澳弘电子',100,48.6405),('000823','超声电子',100,11.7164),
 ('601579','会稽山',100,28.8903),('002580','圣阳股份',200,18.8552),
 ('603757','大元泵业',100,70.3607),('600967','内蒙一机',600,13.1430),
 ('001317','三羊马',100,64.6606),('600988','赤峰黄金',200,39.4454),
 ('603989','艾华集团',300,25.6569),('600150','中国船舶',100,39.2704),
 ('600403','大有能源',400,7.2826),('002491','通鼎互联',300,18.6569),
 ('000811','冰轮环境',200,37.0254),('002531','天顺风能',400,8.3426),
 ('601009','南京银行',1000,12.0801),('000523','红棉股份',1600,3.6532),
 ('603339','四方科技',300,12.7368),('600218','全柴动力',800,8.3463),
 ('600225','卓郎智能',300,4.6167),('000779','甘咨询',500,9.0901),
 ('002487','大金重工',100,45.6205),('002815','崇达技术',100,24.5602),
 ('600391','航发科技',100,42.7404),('002703','浙江世宝',400,15.8352),
 ('603200','上海洗霸',100,53.8505),('002298','特发信息',300,17.0468),
 ('600186','莲花控股',300,12.4668),('600206','有研新材',100,50.5005),
 ('603580','艾艾精工',100,75.6108),('600110','诺德股份',400,12.2026),
 ('601208','东材科技',100,54.9506),('603538','美诺华',300,29.0303),
 ('603256','宏和科技',100,161.5116),
]

def now():
    return dt.datetime.now(TZ)

def connect():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    c = sqlite3.connect(DATA / 'monitor.db', timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS holdings(code TEXT PRIMARY KEY,name TEXT,quantity INTEGER,cost REAL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, event_key TEXT UNIQUE, time TEXT, kind TEXT, code TEXT, title TEXT, detail TEXT, sent INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,time TEXT,payload TEXT);
        ''')
        if not c.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
            c.executemany('INSERT OR IGNORE INTO holdings VALUES(?,?,?,?)', INITIAL)
            for key,value in [('initialized', True),('cash',50000),('target_holdings',5),('volume_ratio',1.3),('stop_loss',8),('take_profit',15),('email_enabled',False),('interval_seconds',60),('flow_delta',10000000)]:
                c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(key,json.dumps(value)))

def settings():
    with connect() as c:
        return {r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM settings')}

def holdings():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM holdings ORDER BY code')]

def save_holding(x):
    code=str(x.get('code',''))
    if len(code)!=6 or not code.isdigit():
        raise ValueError('股票代码必须是6位数字；ETF代码请核对后添加')
    quantity=int(x['quantity']); cost=float(x['cost'])
    if quantity < 0 or not math.isfinite(cost) or cost <= 0:
        raise ValueError('数量不能为负，成本必须大于0')
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO holdings VALUES(?,?,?,?)',(code,str(x.get('name',code))[:40],quantity,cost))

def set_settings(x):
    rules={'cash':(0,1e10),'target_holdings':(1,100),'volume_ratio':(1,10),'stop_loss':(1,100),'take_profit':(1,1000),'interval_seconds':(30,3600),'flow_delta':(100000,1e10)}
    with connect() as c:
        for k,v in x.items():
            if k in rules:
                v=float(v)
                lo,hi=rules[k]
                if not math.isfinite(v) or not lo<=v<=hi: raise ValueError(f'{k}超出范围')
                if k in ('target_holdings','interval_seconds'): v=int(v)
            elif k=='email_enabled':
                if not isinstance(v,bool): raise ValueError('email_enabled必须为布尔值')
            else: continue
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,json.dumps(v)))

def signal(bars, threshold=1.3):
    # Only completed daily bars. Volume baseline excludes today's completed bar.
    if len(bars)<9: return None
    closes=[float(x['close']) for x in bars]
    volumes=[float(x['volume']) for x in bars]
    if any(not math.isfinite(x) or x<=0 for x in closes): return None
    if any(not math.isfinite(x) or x<0 for x in volumes): return None
    a4=sum(closes[-4:])/4; a8=sum(closes[-8:])/8
    p4=sum(closes[-5:-1])/4; p8=sum(closes[-9:-1])/8
    base=sum(volumes[-6:-1])/5
    ratio=volumes[-1]/base if base else 0
    return {'date':bars[-1]['date'],'price':closes[-1],'ma4':a4,'ma8':a8,
            'volume_ratio':ratio,'golden':p4<=p8 and a4>a8,
            'death':p4>=p8 and a4<a8,
            'candidate':p4<=p8 and a4>a8 and closes[-1]>closes[-2] and ratio>=threshold,
            'rising':closes[-1]>closes[-2]}

def record_alert(key,kind,code,title,detail):
    with connect() as c:
        cur=c.execute('INSERT OR IGNORE INTO alerts(event_key,time,kind,code,title,detail) VALUES(?,?,?,?,?,?)',
                      (key,now().isoformat(),kind,code,title,detail))
        return cur.rowcount==1
