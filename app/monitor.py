import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from . import core, market, mail

class Monitor:
    def __init__(self):
        self.lock=threading.Lock(); self.stop=threading.Event(); self.thread=None
        self.mail_error=''; self.scanning=False; self.cursor=0
        self.state={'time':None,'indices':[],'sectors':[],'holdings':[],'candidates':[],
                    'errors':[],'note':'尚未获取行情。不会用截图价格或模拟数据生成交易提醒。'}
        with core.connect() as c:
            row=c.execute('SELECT payload FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
            if row:
                self.state=json.loads(row['payload'])
                self.state['note']='上次保存的数据；请核对更新时间，等待新扫描。'
        self.candidates={x['code']:x for x in self.state.get('candidates',[])}

    def trading(self):
        n=core.now()
        return n.weekday()<5 and (dt.time(9,30)<=n.time().replace(tzinfo=None)<=dt.time(11,30) or dt.time(13)<=n.time().replace(tzinfo=None)<=dt.time(15,10))

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def run(self):
        while not self.stop.is_set():
            if self.trading():
                try: self.scan()
                except Exception: self.state['errors']=['扫描失败，请检查网络与行情接口。']
            self.stop.wait(core.settings().get('interval_seconds',60))

    def scan(self):
        if not self.lock.acquire(blocking=False): return False
        self.scanning=True
        try:
            settings=core.settings(); hs=[h for h in core.holdings() if h['quantity']>0]
            data=market.overview(); errors=data.pop('errors')
            universe=data.pop('universe')
            try: qs={x['code']:x for x in market.quotes([h['code'] for h in hs])}
            except Exception as e: qs={};errors.append('持仓行情: '+str(e))
            # Limit history requests to avoid overwhelming free endpoints.
            eligible=[x for x in universe if x['code'].startswith(('0','3','6')) and 'ST' not in x['name'].upper() and '退' not in x['name'] and x['price'] and x['price']>0]
            batch=eligible[self.cursor:self.cursor+10]
            self.cursor=(self.cursor+10) if self.cursor+10<len(eligible) else 0
            codes=list(dict.fromkeys([h['code'] for h in hs]+[x['code'] for x in batch]))
            sigs={}; previews={}; history_lengths={}
            with ThreadPoolExecutor(max_workers=3) as pool:
                jobs={code:pool.submit(market.daily,code,True) for code in codes}
                for code,job in jobs.items():
                    try:
                        bars=job.result(); history_lengths[code]=len(bars)
                        previews[code]=core.signal(bars,settings['volume_ratio'])
                        sigs[code]=core.signal([b for b in bars if b['date']<core.now().date().isoformat() or core.now().hour>=15],settings['volume_ratio'])
                    except Exception as e: errors.append(code+'日线: '+str(e))
            rows=[]
            today=core.now().date().isoformat()
            previous={x['code']:x for x in self.state.get('holdings',[])}
            # Keep only same-date candidates: no old candidates masquerading as current ones.
            self.candidates={k:v for k,v in self.candidates.items() if v['date']==today}
            for h in hs:
                q=qs.get(h['code'],{}); s=sigs.get(h['code']); p=q.get('price')
                r={**h,'price':p,'change':q.get('change'),'amount':q.get('amount'),'net_flow':q.get('net_flow'),'signal':s}
                r['pnl_percent']=(p/h['cost']-1)*100 if p and p>0 else None
                r['value']=p*h['quantity'] if p and p>0 else None
                r['score']=0; r['reasons']=[];r['category']='等待数据'
                # Decisions use a completed bar from today, not an old history bar + fresh price.
                fresh=bool(s and s['date']==today)
                if s:
                    r['score']=(2 if s['price']>s['ma8'] else -2)+(1 if s['ma4']>s['ma8'] else -1)+(1 if s['rising'] else -1)
                    r['reasons'].append(f"日线{ s['date'] }："+('MA4高于MA8' if s['ma4']>s['ma8'] else 'MA4低于MA8'))
                    r['category']='趋势较强' if r['score']>0 else '减仓关注'
                    if not fresh:r['category']='历史趋势参考'
                if fresh:
                    close_pnl=(s['price']/h['cost']-1)*100
                    reasons=[]
                    if s['death']: reasons.append('4日线下穿8日线')
                    if not s['rising'] and s['volume_ratio']>=settings['volume_ratio']: reasons.append('放量下跌')
                    if close_pnl<=-settings['stop_loss']: reasons.append('达到设定亏损阈值')
                    if close_pnl>=settings['take_profit']: reasons.append('达到设定止盈观察阈值')
                    if reasons:
                        detail='；'.join(reasons)+f"。收盘价{s['price']:.2f}，成本{h['cost']:.4f}，量比{s['volume_ratio']:.2f}（对前5日均量）。请结合可卖数量判断，不自动下单。"
                        core.record_alert(f"risk:{h['code']}:{s['date']}",'持仓关注',h['code'],h['name']+'：短线持仓关注',detail)
                        r['reasons']+=reasons;r['category']='减仓/止盈关注'
                    if s['candidate']:
                        core.record_alert(f"golden:{h['code']}:{s['date']}",'持仓金叉',h['code'],h['name']+'：放量金叉',f"{s['date']}收盘确认；MA4上穿MA8，量比{s['volume_ratio']:.2f}。仅作加仓观察，需核对资金、仓位和板块。")
                # Intraday flow compares successive fresh observations, not estimated trades.
                timestamp=q.get('timestamp')
                live=bool(timestamp and 0<=core.now().timestamp()-timestamp<=180)
                old=previous.get(h['code'],{})
                prev=previews.get(h['code'])
                if live and self.trading():
                    if prev and prev['date']==today and prev['candidate'] and core.now().hour<15:
                        core.record_alert(f"preview:{h['code']}:{today}",'盘中金叉',h['code'],h['name']+'：盘中放量金叉预警',f"MA4暂时上穿MA8；当前累计成交量已达前5日全天均量{prev['volume_ratio']:.2f}倍。尚未收盘确认，可能回落失效。")
                    old_stamp=old.get('quote_timestamp')
                    if old_stamp and 0<timestamp-old_stamp<=180 and q.get('net_flow') is not None and old.get('net_flow') is not None:
                        delta=q['net_flow']-old['net_flow']
                        if abs(delta)>=settings.get('flow_delta',10000000):
                            bucket=int(timestamp//1800)
                            core.record_alert(f"flow:{h['code']}:{bucket}:{'in' if delta>0 else 'out'}",'资金异动',h['code'],h['name']+'：资金指标异动',f"相邻观测（约{timestamp-old_stamp:.0f}秒）估算净流入变化{delta/10000:.1f}万元。行情源估算口径，不等于真实资金流入；同方向半小时去重。")
                r['quote_timestamp']=timestamp
                r['intraday_preview']=prev if live and prev and prev['date']==today else None
                rows.append(r)
            for x in batch:
                preview=previews.get(x['code']); timestamp=x.get('timestamp')
                live=bool(timestamp and 0<=core.now().timestamp()-timestamp<=180)
                if live and self.trading() and core.now().hour<15 and preview and preview['date']==today and preview['candidate'] and history_lengths.get(x['code'],0)>=20:
                    core.record_alert(f"preview:{x['code']}:{today}",'新股盘中预警',x['code'],x['name']+'：盘中放量金叉候选',f"尚未收盘确认；当前累计量达前5日全天均量{preview['volume_ratio']:.2f}倍。来自涨幅前100候选池，不代表全市场。")
                s=sigs.get(x['code'])
                if s and s['candidate'] and s['date']==today and history_lengths.get(x['code'],0)>=20:
                    self.candidates[x['code']]={**x,**s}
                    core.record_alert(f"candidate:{x['code']}:{s['date']}",'新股候选',x['code'],x['name']+'：放量金叉候选',f"{s['date']}收盘确认；MA4 {s['ma4']:.2f}，MA8 {s['ma8']:.2f}，量比{s['volume_ratio']:.2f}。仅来自涨幅榜前100候选池，非全市场扫描。")
            rows.sort(key=lambda x:x['score'],reverse=True)
            self.state={'time':core.now().isoformat(),'indices':data['indices'],'sectors':data['sectors'],'holdings':rows,'candidates':list(self.candidates.values()),'errors':errors,
                        'note':'免费行情可能延迟。日线只用已收盘数据；候选池为涨幅榜前100只，每轮检查10只。非全市场实时扫描；盘中预警需行情时间戳有效且当日累计量超过前5日全天均量，收盘后另行确认。节假日以行情源返回为准。'}
            with core.connect() as c:
                c.execute('INSERT INTO snapshots(time,payload) VALUES(?,?)',(self.state['time'],json.dumps(self.state,ensure_ascii=False,allow_nan=False)))
                c.execute('DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT 5)')
            if core.now().weekday()<5 and core.now().hour==15 and core.now().minute>=5 and rows and any(x.get('signal') and x['signal']['date']==today for x in rows):
                summary='目标持仓'+str(settings['target_holdings'])+'只；当前'+str(len(rows))+'只，可用现金'+str(settings['cash'])+'元。\n'+'\n'.join(x['name']+'：'+x['category']+'；'+'；'.join(x['reasons']) for x in rows)+'\n排序仅按日线趋势，未融合板块归属、资金、流动性，不是最终留仓名单。'
                core.record_alert('summary:'+today,'收盘汇总','','短线持仓收盘汇总',summary)
            self.deliver()
            return True
        finally:
            self.scanning=False;self.lock.release()

    def deliver(self):
        if not core.settings()['email_enabled']: return
        with core.connect() as c:
            pending=[dict(r) for r in c.execute('SELECT * FROM alerts WHERE sent=0 ORDER BY id LIMIT 30')]
        if not pending:return
        # One digest per scan, successful deliveries only are marked sent.
        try:
            mail.send('A股监控：'+str(len(pending))+'条提醒','\n\n'.join(x['time']+'\n'+x['title']+'\n'+x['detail'] for x in pending))
            with core.connect() as c:
                c.executemany('UPDATE alerts SET sent=1 WHERE id=?',[(x['id'],) for x in pending])
            self.mail_error=''
        except Exception:
            self.mail_error='邮件发送失败：请检查SMTP授权码、邮箱设置和到smtp.qq.com:465的网络连接；未发送提醒会保留重试。'
