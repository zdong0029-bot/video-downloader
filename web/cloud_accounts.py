"""Administrator cloud accounts. DPAPI secrets; process-safe profile transactions."""
import base64, ctypes, json, secrets, subprocess, threading, time
from ctypes import wintypes
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DIRECTORY=ROOT/'data'/'cloud-accounts'
from cloud_providers import PROVIDERS

class ProfileLock:
    def __init__(self): self.thread=threading.RLock();self.local=threading.local()
    def __enter__(self):
        self.thread.acquire()
        depth=getattr(self.local,'depth',0)
        try:
            if not depth:
                import msvcrt
                DIRECTORY.mkdir(parents=True,exist_ok=True)
                handle=(DIRECTORY/'profiles.lock').open('a+b')
                if handle.seek(0,2)==0: handle.write(b'0');handle.flush()
                handle.seek(0)
                deadline=time.monotonic()+15
                while True:
                    try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);break
                    except OSError:
                        if time.monotonic()>deadline: handle.close();raise ValueError('账号配置正在保存，请稍后重试')
                        time.sleep(.05)
                self.local.handle=handle
            self.local.depth=depth+1
            return self
        except BaseException: self.thread.release();raise
    def __exit__(self,*args):
        import msvcrt
        self.local.depth-=1
        try:
            if not self.local.depth:
                handle=self.local.handle
                try:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                finally:handle.close()
        finally:self.thread.release()
LOCK=ProfileLock()

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]

def crypt(data,decrypt=False):
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte)));target=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):raise OSError('Windows 凭据保护失败')
    try:return ctypes.string_at(target.data,target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)

def read():
    p=DIRECTORY/'profiles.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'accounts':[],'active':{}}

def write(data):
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    temp=DIRECTORY/('profiles-'+secrets.token_hex(6)+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temp.replace(DIRECTORY/'profiles.json')

def save(provider,label,payload,verified=False):
    if provider not in PROVIDERS:raise ValueError('未知网盘')
    with LOCK:
        data=read();identity=secrets.token_hex(12)
        data['accounts'].append({'id':identity,'provider':provider,'label':label[:60],'created':time.strftime('%Y-%m-%d %H:%M'),'verified':verified,'secret':base64.b64encode(crypt(payload)).decode('ascii'),'checked_at':time.time() if verified else 0,'auth_ok':True if verified else None,'message':'官方账号验证通过' if verified else '授权尚未检测'})
        write(data);return identity

def listing():
    with LOCK:
        data=read()
        return {'accounts':[{k:v for k,v in item.items() if k!='secret'} for item in data['accounts']],'active':data['active'],'providers':PROVIDERS}

def payload(identity):
    with LOCK:
        item=next((x for x in read()['accounts'] if x['id']==identity),None)
        if not item:raise ValueError('账号不存在')
        return item['provider'],crypt(base64.b64decode(item['secret']),True)

def active_payload(provider):
    with LOCK:
        data=read();identity=data['active'].get(provider)
        if not identity:return None,None
        key,content=payload(identity)
        if key!=provider:raise ValueError('账号配置不匹配')
        return identity,content

def update_payload(identity,state):
    with LOCK:
        data=read();item=next((x for x in data['accounts'] if x['id']==identity),None)
        if not item:raise ValueError('账号不存在')
        item['secret']=base64.b64encode(crypt(json.dumps(state).encode())).decode('ascii');write(data)

def health(identity,valid,message):
    with LOCK:
        data=read();item=next((x for x in data['accounts'] if x['id']==identity),None)
        if not item:return
        item.update(auth_ok=valid,checked_at=time.time(),message=message[:150]);write(data)

def public_status():
    with LOCK:
        data=read();items=[]
        for key,name in PROVIDERS.items():
            account=next((a for a in data['accounts'] if a['id']==data['active'].get(key) and a['provider']==key),None)
            items.append({'id':key,'name':name,'connected':bool(account),'auth_ok':account.get('auth_ok') if account else None,'checked_at':account.get('checked_at',0) if account else 0})
        return {'providers':items}

def activate(identity,validate=True):
    provider,content=payload(identity)
    if validate and provider!='baidu':
        import cloud_backend
        cloud_backend.validate(provider,json.loads(content),identity)
    import baidu_backend
    with LOCK:
        data=read();item=next((x for x in data['accounts'] if x['id']==identity),None)
        if not item:raise ValueError('账号不存在')
        if provider=='baidu':
            if not baidu_backend.LOCK.acquire(blocking=False):raise ValueError('百度任务正在下载，请完成或取消后切换')
            try:
                json.loads(content);path=ROOT/'private-baidu';path.mkdir(exist_ok=True)
                temp=path/'pcs_config.switch.tmp';temp.write_bytes(content);temp.replace(path/'pcs_config.json')
                (ROOT/'baidu-connected.flag').write_text('connected',encoding='ascii')
            finally:baidu_backend.LOCK.release()
        data['active'][provider]=identity;write(data)

def verify(identity):
    provider,content=payload(identity)
    if provider!='baidu':
        import cloud_backend
        cloud_backend.validate(provider,json.loads(content),identity)
    else:
        executable=next((ROOT/'pcs').rglob('BaiduPCS-Go.exe'),None)
        if executable is None:raise ValueError('百度下载组件缺失')
        import os,re,tempfile
        with tempfile.TemporaryDirectory(dir=ROOT/'private-baidu') as d:
            (Path(d)/'pcs_config.json').write_bytes(content)
            env=os.environ.copy();env['BAIDUPCS_GO_CONFIG_DIR']=d
            r=subprocess.run([str(executable),'who'],env=env,capture_output=True,timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode or not re.search(r'uid:\s*[1-9][0-9]*',r.stdout.decode('utf-8',errors='replace')):
            health(identity,False,'登录授权已失效，请重新扫码');raise ValueError('百度账号验证未通过，请重新扫码')
        health(identity,True,'官方下载组件账号验证通过')
    return {'ok':True,'message':PROVIDERS[provider]+'账号验证通过；具体分享仍受有效期与下载权限影响'}

def import_existing():
    with LOCK:
        if any(x['provider']=='baidu' for x in read()['accounts']):return
        path=ROOT/'private-baidu'/'pcs_config.json'
        if path.exists() and (ROOT/'baidu-connected.flag').exists():
            identity=save('baidu','当前百度账号',path.read_bytes(),False)
            data=read();data['active']['baidu']=identity;write(data)
