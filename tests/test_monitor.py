import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import core,mail,market
from app.monitor import Monitor


def bars(today, closes=None):
    closes=closes or [10]*19+[12]
    return [{'date':(today.date()-dt.timedelta(days=len(closes)-1-i)).isoformat(),'close':x,'volume':200 if i==len(closes)-1 else 100} for i,x in enumerate(closes)]

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.p1=patch.object(core,'DATA',self.path);self.p2=patch.object(mail,'DATA',self.path)
        self.p1.start();self.p2.start();core.init()
        with core.connect() as c:c.execute('DELETE FROM holdings')
        core.save_holding({'code':'000001','name':'测试持仓','quantity':100,'cost':11})
    def tearDown(self):self.p1.stop();self.p2.stop();self.temp.cleanup()
    def test_signal_golden_volume(self):
        s=core.signal(bars(dt.datetime(2026,10,13,tzinfo=core.TZ)))
        self.assertTrue(s['candidate']);self.assertEqual(s['volume_ratio'],2)
    def test_no_candidate_without_volume(self):
        b=bars(dt.datetime(2026,10,13,tzinfo=core.TZ));b[-1]['volume']=100
        self.assertFalse(core.signal(b)['candidate'])
    def test_insufficient_or_bad_bars(self):
        self.assertIsNone(core.signal([]));b=bars(core.now());b[-1]['close']=float('nan');self.assertIsNone(core.signal(b))
    def test_alert_dedup(self):
        self.assertTrue(core.record_alert('x','k','c','t','d'));self.assertFalse(core.record_alert('x','k','c','t','d'))
    def test_secret_permissions_and_redaction(self):
        mail.save({'sender':'123@qq.com','recipient':'123@qq.com','password':'TEST-NOT-A-REAL-SECRET'})
        self.assertNotIn('password',mail.status());self.assertEqual(os.stat(self.path/'mail.json').st_mode & 0o777,0o600)
        mail.save({'sender':'123@qq.com','recipient':'123@qq.com','password':''});self.assertTrue(mail.status()['configured'])
    def test_invalid_holding(self):
        with self.assertRaises(ValueError):core.save_holding({'code':'123','quantity':100,'cost':1})
        with self.assertRaises(ValueError):core.set_settings({'cash':float('nan')})
    def mocked_scan(self,now_,day_offset=0):
        historic=now_-dt.timedelta(days=day_offset)
        with patch.object(core,'now',return_value=now_),patch.object(market,'overview',return_value={'indices':[],'sectors':[],'universe':[],'errors':[]}),patch.object(market,'quotes',return_value=[{'code':'000001','price':12,'net_flow':0,'timestamp':now_.timestamp()}]),patch.object(market,'daily',return_value=bars(historic)):
            m=Monitor();self.assertTrue(m.scan());return m
    def test_intraday_not_claimed_confirmed(self):
        m=self.mocked_scan(dt.datetime(2026,10,13,10,30,tzinfo=core.TZ))
        with core.connect() as c:kinds=[r['kind'] for r in c.execute('SELECT kind FROM alerts')]
        self.assertEqual(kinds,['盘中金叉']);self.assertNotEqual(m.state['holdings'][0]['signal']['date'],'2026-10-13')
    def test_old_daily_no_alert(self):
        self.mocked_scan(dt.datetime(2026,10,13,15,6,tzinfo=core.TZ),1)
        with core.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM alerts').fetchone()[0],0)
    def test_close_confirm_and_summary_dedup(self):
        n=dt.datetime(2026,10,13,15,6,tzinfo=core.TZ)
        self.mocked_scan(n);self.mocked_scan(n)
        with core.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM alerts').fetchone()[0],2)
    def test_mail_failed_not_marked_sent(self):
        core.record_alert('x','k','c','t','d');core.set_settings({'email_enabled':True})
        m=Monitor()
        with patch.object(mail,'send',side_effect=RuntimeError('failed')):m.deliver()
        with core.connect() as c:self.assertEqual(c.execute('SELECT sent FROM alerts').fetchone()[0],0)
        with patch.object(mail,'send') as sender:m.deliver();sender.assert_called_once()
        with core.connect() as c:self.assertEqual(c.execute('SELECT sent FROM alerts').fetchone()[0],1)
    def test_market_failure_no_fake_signal(self):
        with patch.object(market,'overview',return_value={'indices':[],'sectors':[],'universe':[],'errors':['网络拒绝']}),patch.object(market,'quotes',side_effect=RuntimeError('network')),patch.object(market,'daily',side_effect=RuntimeError('network')):
            m=Monitor();m.scan();self.assertIsNone(m.state['holdings'][0]['price']);self.assertTrue(m.state['errors'])
            with core.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM alerts').fetchone()[0],0)
    def test_flow_alert_requires_fresh_change(self):
        n=dt.datetime(2026,10,13,10,30,tzinfo=core.TZ)
        with patch.object(core,'now',return_value=n),patch.object(market,'overview',return_value={'indices':[],'sectors':[],'universe':[],'errors':[]}),patch.object(market,'daily',return_value=bars(n-dt.timedelta(days=1))),patch.object(market,'quotes',return_value=[{'code':'000001','price':12,'net_flow':20000000,'timestamp':n.timestamp()}]):
            m=Monitor();m.state['holdings']=[{'code':'000001','net_flow':0,'quote_timestamp':n.timestamp()-60}];m.scan()
            with core.connect() as c:self.assertEqual(c.execute('SELECT kind FROM alerts').fetchone()[0],'资金异动')

if __name__=='__main__':unittest.main()
