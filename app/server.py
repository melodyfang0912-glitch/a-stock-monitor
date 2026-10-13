import argparse
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from . import core,mail
from .monitor import Monitor

PAGE=core.ROOT/'app'/'index.html'
MONITOR=None
LOCAL_ONLY=True
TOKEN=os.environ.get('MONITOR_ACCESS_TOKEN','')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass  # No request content or credential logging.
    def respond(self,status,payload):
        data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
    def authorized(self):
        hostname=urlparse('http://'+self.headers.get('Host','')).hostname
        if LOCAL_ONLY and hostname not in ('127.0.0.1','localhost','::1'): return False
        return not TOKEN or hmac.compare_digest(self.headers.get('X-Access-Token',''),TOKEN)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/':
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(PAGE.read_bytes());return
        if not self.authorized(): self.respond(401,{'error':'需要访问口令'});return
        if path=='/api/status':
            with core.connect() as c: alerts=[dict(x) for x in c.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 100')]
            self.respond(200,{'settings':core.settings(),'mail':mail.status(),'holdings':core.holdings(),'market':MONITOR.state,'alerts':alerts,'scanning':MONITOR.scanning,'mail_error':MONITOR.mail_error,'missing_etf':'通信ETF富国：截图未显示代码，请核对后手工添加1700份、成本1.7811。'})
        elif path=='/health': self.respond(200,{'ok':True})
        else:self.respond(404,{'error':'未找到'})
    def do_POST(self):
        # Prevent cross-origin/CSRF requests, including malicious forms targeting localhost.
        host=self.headers.get('Host','')
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=host:
            self.respond(403,{'error':'拒绝跨站请求'});return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            self.respond(415,{'error':'仅接受JSON'});return
        if not self.authorized():self.respond(401,{'error':'需要访问口令'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=20000:raise ValueError('请求大小不合法')
            x=json.loads(self.rfile.read(length))
            if not isinstance(x,dict):raise ValueError('请求必须是对象')
            path=urlparse(self.path).path
            if path=='/api/holdings':core.save_holding(x)
            elif path=='/api/holdings/delete':
                with core.connect() as c:c.execute('DELETE FROM holdings WHERE code=?',(str(x.get('code','')),))
            elif path=='/api/settings':
                if x.get('email_enabled') and not mail.status()['configured']:raise ValueError('请先配置并测试邮件')
                core.set_settings(x)
            elif path=='/api/mail':mail.save(x)
            elif path=='/api/mail/test':
                try:mail.send('A股监控：测试邮件','邮件渠道已连通。这是测试通知，不包含交易信号。')
                except Exception:
                    self.respond(502,{'error':'发送失败，请核对授权码和SMTP服务，并检查smtp.qq.com:465网络访问。'});return
            elif path=='/api/scan':threading.Thread(target=MONITOR.scan,daemon=True).start()
            else:self.respond(404,{'error':'未找到'});return
            self.respond(200,{'ok':True})
        except (ValueError,KeyError,TypeError):self.respond(400,{'error':'输入不合法，请核对必填项、代码和数值范围。'})
        except Exception:self.respond(500,{'error':'操作失败，请检查本地配置和数据目录权限。'})

def main():
    global MONITOR,LOCAL_ONLY
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8080);parser.add_argument('--no-worker',action='store_true')
    args=parser.parse_args()
    if args.host not in ('127.0.0.1','localhost','::1') and not TOKEN:
        parser.error('对外监听必须设置MONITOR_ACCESS_TOKEN，并使用HTTPS反向代理；授权码不能通过公网HTTP传输。')
    LOCAL_ONLY=args.host in ('127.0.0.1','localhost','::1')
    core.init();MONITOR=Monitor()
    if not args.no_worker:MONITOR.start()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    print(f'监控服务已启动，端口 {args.port}。邮件默认关闭。',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:MONITOR.stop.set();server.server_close()

if __name__=='__main__':main()
