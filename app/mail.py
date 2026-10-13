import json
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from .core import DATA

LOCK=threading.Lock()

def config():
    path=DATA/'mail.json'
    if not path.exists(): return {}
    return json.loads(path.read_text())

def status():
    x=config()
    return {'sender':x.get('sender',''),'recipient':x.get('recipient',''),'configured':bool(x.get('sender') and x.get('recipient') and x.get('password'))}

def save(x):
    sender=str(x.get('sender','')).strip(); recipient=str(x.get('recipient','')).strip()
    for addr in (sender,recipient):
        if '@' not in addr or any(ch.isspace() for ch in addr) or ',' in addr or ';' in addr:
            raise ValueError('请输入一个有效邮箱地址')
    if not sender.lower().endswith('@qq.com'): raise ValueError('当前发件配置支持QQ邮箱，请使用@qq.com地址')
    with LOCK:
        old=config()
        password=str(x.get('password','')).strip() or old.get('password','')
        if not password: raise ValueError('请填写QQ邮箱SMTP授权码')
        DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp=DATA/'mail.tmp'
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f: json.dump({'sender':sender,'recipient':recipient,'password':password},f)
        os.chmod(temp,0o600)
        os.replace(temp,DATA/'mail.json')
    return status()

def send(subject,body):
    x=config()
    if not status()['configured']: raise ValueError('尚未配置邮件')
    msg=EmailMessage(); msg['Subject']=subject;msg['From']=x['sender'];msg['To']=x['recipient'];msg.set_content(body)
    # Preserve TLS certificate verification. Do not log credentials or server response bodies.
    with smtplib.SMTP_SSL('smtp.qq.com',465,context=ssl.create_default_context(),timeout=20) as smtp:
        smtp.login(x['sender'],x['password'])
        smtp.send_message(msg)
