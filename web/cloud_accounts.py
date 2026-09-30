"""Administrator account profiles. Secrets use Windows user-scoped DPAPI."""
import base64, ctypes, json, os, secrets, threading, time
from ctypes import wintypes
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DIRECTORY=ROOT/'data'/'cloud-accounts'
PROVIDERS={'baidu':'百度网盘','ali':'阿里云盘','quark':'夸克网盘'}
LOCK=threading.RLock()

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]

def crypt(data, decrypt=False):
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte))); target=Blob()
    dll=ctypes.windll.crypt32
    fn=dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise OSError('Windows 凭据保护失败')
    try: return ctypes.string_at(target.data,target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)

def read():
    path=DIRECTORY/'profiles.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'accounts':[],'active':{}}

def write(data):
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    temp=DIRECTORY/'profiles.tmp'
    temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temp.replace(DIRECTORY/'profiles.json')

def save(provider,label,payload,verified=False):
    if provider not in PROVIDERS: raise ValueError('未知网盘')
    with LOCK:
        data=read(); identity=secrets.token_hex(12)
        data['accounts'].append({'id':identity,'provider':provider,'label':label[:60],
            'created':time.strftime('%Y-%m-%d %H:%M'),'verified':verified,
            'secret':base64.b64encode(crypt(payload)).decode('ascii')})
        write(data); return identity

def listing():
    with LOCK:
        data=read()
        return {'accounts':[{k:v for k,v in item.items() if k!='secret'} for item in data['accounts']],
                'active':data['active'],'providers':PROVIDERS}

def activate(identity):
    import baidu_backend
    with LOCK:
        data=read(); item=next((x for x in data['accounts'] if x['id']==identity),None)
        if not item: raise ValueError('账号不存在')
        if item['provider']=='baidu':
            if not baidu_backend.LOCK.acquire(blocking=False): raise ValueError('百度任务正在下载，请完成或取消后切换')
            try:
                content=crypt(base64.b64decode(item['secret']),True)
                json.loads(content)
                path=ROOT/'private-baidu';path.mkdir(exist_ok=True)
                temp=path/'pcs_config.switch.tmp';temp.write_bytes(content);temp.replace(path/'pcs_config.json')
                (ROOT/'baidu-connected.flag').write_text('connected',encoding='ascii')
            finally: baidu_backend.LOCK.release()
        data['active'][item['provider']]=identity;write(data)

def import_existing():
    with LOCK:
        if any(x['provider']=='baidu' for x in read()['accounts']): return
        path=ROOT/'private-baidu'/'pcs_config.json'
        if path.exists() and (ROOT/'baidu-connected.flag').exists():
            identity=save('baidu','当前百度账号',path.read_bytes(),True)
            data=read();data['active']['baidu']=identity;write(data)
