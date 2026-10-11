"""User-authorized Tencent Yuanbao session, protected by Windows DPAPI."""
import base64,json,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import requests
from cloud_accounts import crypt
ROOT=Path(__file__).resolve().parent
STORE=ROOT/'data'/'video-accounts'/'yuanbao.json'
PROBE='https://weixin.qq.com/sph/AD8rWl3ssY'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
def supported(url):
    p=urlsplit(url)
    return p.scheme in ('http','https') and ((p.hostname=='weixin.qq.com' and p.path.startswith('/sph/')) or (p.hostname=='channels.weixin.qq.com' and p.path.startswith('/finder-preview/')))
def status():
    d=json.loads(STORE.read_text(encoding='utf-8')) if STORE.exists() else {'connected':False,'message':'视频号后台授权尚未连接'}
    result={k:d.get(k) for k in ('connected','checked_at','message')}
    progress=STORE.parent/'qr-status.json'
    if progress.exists():
        try:
            qr=json.loads(progress.read_text(encoding='utf-8'))
            result['qr_available']=time.time()-qr.get('time',0)<720 and (STORE.parent/'qr-login.png').is_file() and qr.get('message','').startswith('二维码已生成')
            result['qr_message']=qr.get('message','')
        except (OSError,ValueError):pass
    return result
def load():
    d=json.loads(STORE.read_text(encoding='utf-8'))
    return json.loads(crypt(base64.b64decode(d['secret']),True))
def save(state):
    state={**state,'origins':[],'cookies':[c for c in state.get('cookies',[]) if c.get('domain','').lstrip('.') in ('tencent.com','yuanbao.tencent.com') or c.get('domain','').lstrip('.').endswith('.yuanbao.tencent.com')]}
    STORE.parent.mkdir(parents=True,exist_ok=True)
    data={'connected':True,'checked_at':time.time(),'message':'元宝官方授权解析验证通过','secret':base64.b64encode(crypt(json.dumps(state).encode())).decode()}
    temp=STORE.with_suffix('.tmp');temp.write_text(json.dumps(data),encoding='utf-8');temp.replace(STORE)
def parse(url,state):
    if not supported(url):raise ValueError('请粘贴视频号官方分享链接')
    s=requests.Session();s.trust_env=False
    try:
        for c in state.get('cookies',[]):
            domain=c.get('domain','').lstrip('.')
            if domain in ('yuanbao.tencent.com','tencent.com') or domain.endswith('.yuanbao.tencent.com'):
                s.cookies.set(c['name'],c['value'],domain=c['domain'],path=c.get('path','/'))
        headers={'User-Agent':state.get('ua',UA),'Origin':'https://yuanbao.tencent.com','Referer':'https://yuanbao.tencent.com/','Accept':'application/json, text/plain, */*'}
        # Only headers actually sent by this user's official browser are retained.
        allowed={'t-userid','x-agentid','x-device-id','x-hy92','x-hy93','x-id','x-platform','x-source','x-webversion','x-language'}
        headers.update({k:v for k,v in state.get('headers',{}).items() if k.lower() in allowed})
        r=s.post('https://yuanbao.tencent.com/api/weixin/get_parse_result',json={'type':'video_channel_url','url':url,'scene':1},headers=headers,timeout=(8,30))
        if r.status_code in (401,403):raise ValueError('元宝官方授权未生效或已过期，请在管理员主页连接视频号账号')
        if r.status_code!=200:raise ValueError('元宝解析接口返回 HTTP '+str(r.status_code))
        d=r.json();info=d.get('data') or {}
        if d.get('code') not in (0,'0'):raise ValueError('元宝未提供播放授权（代码 '+str(d.get('code'))+'），请在官方网页确认分享可播放')
        playable=info.get('playable_url','');p=urlsplit(playable)
        if p.hostname!='channels.weixin.qq.com':raise ValueError('元宝没有返回视频号官方播放地址')
        q=parse_qs(p.query);token=q.get('token',[''])[0];eid=q.get('eid',[''])[0]
        if not token or not eid:raise ValueError('元宝只返回预览信息，未提供原视频播放授权')
        return info,token,eid
    finally:s.close()
def check():
    if not STORE.exists():raise ValueError('请先连接视频号官方账号')
    parse(PROBE,load());return status()
