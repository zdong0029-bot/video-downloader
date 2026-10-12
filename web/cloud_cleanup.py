"""Receipts for task-owned cloud copies; never delete before verified host files."""
import hashlib, json, re, secrets, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT/'data'/'cloud-transfers'
DOWNLOADS = ROOT/'downloads'
LOCK = threading.RLock()
NAMES = {'baidu':'百度网盘','ali':'阿里云盘','quark':'夸克网盘','uc':'UC网盘','xunlei':'迅雷云盘'}

def receipt_path(job):
    if not re.fullmatch(r'[0-9a-f]{12}',job): raise ValueError('无效的清理任务编号')
    return DIRECTORY/(job+'.json')

def read(job):
    return json.loads(receipt_path(job).read_text(encoding='utf-8'))

def write(record):
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    p=receipt_path(record['job']);tmp=p.with_name(p.stem+'-'+secrets.token_hex(4)+'.tmp')
    tmp.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8');tmp.replace(p)

def register(job,account,provider,remote):
    if provider not in NAMES: raise ValueError('未知网盘')
    p=receipt_path(job)
    if provider=='baidu' and remote!='/网站下载任务/'+job: raise ValueError('转存目录不属于本次任务')
    if not remote or not account: raise ValueError('缺少任务转存标识')
    with LOCK:
        if p.exists(): raise ValueError('任务转存记录已存在，不能覆盖')
        write({'job':job,'account':account,'provider':provider,'remote':str(remote),'status':'download_pending','files':[],'created':time.time(),'message':'等待全部原文件下载及校验，尚未清理'})

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def verified_files(out,paths):
    expected=(DOWNLOADS/out.name).resolve()
    if out.resolve()!=expected: raise ValueError('下载目录不属于本次任务')
    if not paths or len(set(paths))!=len(paths): raise ValueError('原文件未完整下载')
    records=[]
    for p in paths:
        if p.parent.resolve()!=expected or p.is_symlink() or not p.is_file() or p.name.endswith(('.part','.tmp')): raise ValueError('原文件尚未完成，不能清理转存')
        if p.stat().st_size<=0: raise ValueError('原文件为空，不能清理转存')
        records.append({'name':p.name,'size':p.stat().st_size,'sha256':digest(p)})
    return records

def ensure_files(record):
    directory=DOWNLOADS/record['job']
    if not record['files']: raise ValueError('尚未确认全部文件下载完成，禁止清理')
    for f in record['files']:
        p=directory/f['name']
        if p.parent.resolve()!=directory.resolve() or p.is_symlink() or not p.is_file() or p.stat().st_size!=f['size'] or digest(p)!=f['sha256']: raise ValueError('电脑上的完整文件缺失或校验不符，未删除云端转存')

def perform(record,client=None,delete=None):
    if record['provider'] in ('quark','uc'):
        if client is None: raise ValueError('缺少转存账号连接')
        roots=client.quark_list('0')
        item=next((x for x in roots if str(x.get('fid'))==record['remote']),None)
        if item:
            if item.get('file_name')!='网站下载任务-'+record['job']: raise ValueError('云端目录名称已改变，未清理')
            # Only the exact folder created for this receipt, never other cloud files.
            data=client.request('/file/delete',{'action_type':2,'filelist':[record['remote']],'exclude_fids':[]}).get('data',{})
            if data.get('task_id'):
                for retry in range(60):
                    d=client.request('/task',params={'task_id':data['task_id'],'retry_index':retry}).get('data',{})
                    if d.get('status')==2: break
                    if d.get('status')==3: raise ValueError('平台未完成临时转存清理')
                    time.sleep(.5)
                else: raise ValueError('平台清理等待超时')
            if any(str(x.get('fid'))==record['remote'] for x in client.quark_list('0')): raise ValueError('临时转存目录仍在云端，清理未确认')
    elif record['provider']=='ali':
        if client is None:raise ValueError('缺少转存账号连接')
        parts=record['remote'].split(':')
        if len(parts)!=2 or not all(re.fullmatch(r'[A-Za-z0-9_-]{1,100}',v) for v in parts):raise ValueError('临时转存标识无效')
        drive,fid=parts
        from cloud_backend import PlatformError
        try:item=client.ali_private('/v2/file/get',{'drive_id':drive,'file_id':fid})
        except PlatformError as error:
            if error.code!='NotFound.File':raise
            item=None
        if item and not item.get('trashed'):
            if item.get('name')!='网站下载任务-'+record['job'] or item.get('type')!='folder':raise ValueError('云端目录名称已改变，未清理')
            client.ali_private('/v3/file/delete',{'drive_id':drive,'file_id':fid})
            for _ in range(10):
                try:after=client.ali_private('/v2/file/get',{'drive_id':drive,'file_id':fid})
                except PlatformError as error:
                    if error.code!='NotFound.File':raise
                    after=None
                if not after or after.get('trashed'):break
                time.sleep(.5)
            else:raise ValueError('阿里临时转存清理尚未确认')
    elif record['provider']=='xunlei':
        if client is None:raise ValueError('缺少转存账号连接')
        roots=client.xunlei_list('')
        item=next((f for f in roots if str(f.get('id'))==record['remote']),None)
        if item:
            if item.get('name')!='网站下载任务-'+record['job'] or item.get('kind')!='drive#folder':
                raise ValueError('云端目录名称或类型已改变，未清理')
            client.xunlei_delete_task(record['remote'],record['job'])
            for _ in range(20):
                if not any(str(f.get('id'))==record['remote'] for f in client.xunlei_list('')):break
                time.sleep(.5)
            else:raise ValueError('迅雷临时转存清理尚未确认')
    elif record['provider']=='baidu':
        if record['remote']!='/网站下载任务/'+record['job']: raise ValueError('转存目录不属于本次任务')
        if delete is None: raise ValueError('缺少转存账号连接')
        delete(record['remote'])
    else:
        raise ValueError('此平台使用原文件直下载，没有需要清理的转存')

def attempt(record,client=None,delete=None):
    try:
        ensure_files(record)
        perform(record,client,delete)
    except Exception:
        record.update(status='cleanup_pending',message='原文件已校验完成；临时转存清理未通过，可在网盘账号区域重试',updated=time.time())
    else:
        record.update(status='cleaned',message='电脑原文件已完整校验；本次临时转存已删除（平台回收站规则适用）',updated=time.time())
    write(record)
    return record['message']

def complete(out,account,provider,remote,paths,client=None,delete=None):
    with LOCK:
        record=read(out.name)
        if (record['account'],record['provider'],record['remote'])!=(account,provider,str(remote)): raise ValueError('清理记录与任务不匹配')
        record['files']=verified_files(out,paths);record['status']='cleanup_pending';write(record)
        return attempt(record,client,delete)

def listing():
    with LOCK:
        records=[]
        for p in sorted(DIRECTORY.glob('*.json'),reverse=True):
            try: r=read(p.stem)
            except (OSError,ValueError): continue
            if r['status']=='cleaned': continue
            records.append({'job':r['job'],'provider':NAMES[r['provider']],'status':r['status'],'files':len(r['files']),'message':r['message'],'created':r['created']})
        return {'items':records}

def retry(job):
    import cloud_accounts,cloud_backend
    with LOCK: record=read(job)
    if record['status']=='cleaned': return {'ok':True,'message':record['message']}
    if record['status']!='cleanup_pending': raise ValueError('任务未全部下载完成，不能清理云端转存')
    ensure_files(record)
    provider,content=cloud_accounts.payload(record['account'])
    if provider!=record['provider']: raise ValueError('转存账号记录不匹配')
    if provider=='baidu':
        import baidu_backend
        if not baidu_backend.LOCK.acquire(blocking=False): raise ValueError('百度正在下载，请稍后重试清理')
        try:
            with LOCK: note=attempt(record,delete=lambda remote:baidu_backend.delete_temp(content,remote))
        finally: baidu_backend.LOCK.release()
    else:
        lock=cloud_backend.LOCKS[provider]
        if not lock.acquire(blocking=False): raise ValueError('网盘正在下载，请稍后重试清理')
        c=None
        try:
            if provider in ('uc','xunlei'):
                from linkswift_cloud import Client
            else:Client=cloud_backend.Client
            c=Client(provider,json.loads(content),record['account'])
            with LOCK: note=attempt(record,c)
        finally:
            try:
                if c:c.close()
            finally:lock.release()
    return {'ok':record['status']=='cleaned','message':note}
