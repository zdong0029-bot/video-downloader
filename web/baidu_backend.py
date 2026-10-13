"""Baidu originals: try authorized share download, otherwise task-owned transfer."""
import json, hashlib, re, os, shutil, subprocess, threading, time, tempfile
from pathlib import Path
from http.cookies import SimpleCookie
from common import Cancelled
import download_tuning
from baidu_share import Share, AuthorizationRequired
import file_download
ROOT=Path(__file__).resolve().parent
LOCK=threading.Lock()

def configured():
    return (ROOT/'baidu-connected.flag').is_file() and (ROOT/'private-baidu').is_dir()

def decode_log(content):
    try:return content.decode('utf-8')
    except UnicodeDecodeError:return content.decode('gb18030',errors='replace')

def cli():
    executable=next((ROOT/'pcs').rglob('BaiduPCS-Go.exe'),None)
    if executable is None: raise ValueError('百度下载组件缺失')
    return executable

def remove_command(executable,env,remote):
    if not re.fullmatch(r'/网站下载任务/[0-9a-f]{12}',remote): raise ValueError('转存目录不属于本次任务')
    r=subprocess.run([str(executable),'rm',remote],env=env,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    message=decode_log(r.stdout)
    if r.returncode or '操作成功' not in message or '已删除' not in message or remote not in message: raise ValueError('百度未确认临时转存删除成功')

def delete_temp(content,remote):
    # Use the receipt's original account even if the administrator switched later.
    with tempfile.TemporaryDirectory(prefix='cleanup-',dir=ROOT/'private-baidu') as d:
        (Path(d)/'pcs_config.json').write_bytes(content)
        env=os.environ.copy();env['BAIDUPCS_GO_CONFIG_DIR']=d
        remove_command(cli(),env,remote)

def authorize(share,state):
    user=next((u for u in state.get('baidu_user_list',[]) if u.get('uid')==state.get('baidu_active_uid')),None)
    if not user: raise ValueError('百度账号授权数据无效，请重新扫码')
    cookie=SimpleCookie()
    try: cookie.load(user.get('cookies',''))
    except Exception: pass
    for key,m in cookie.items(): share.session.cookies.set(key,m.value,domain='.baidu.com',path='/')
    for key,field in [('BDUSS','bduss'),('STOKEN','stoken'),('BAIDUID','baiduid')]:
        if user.get(field): share.session.cookies.set(key,user[field],domain='.baidu.com',path='/')

def _relative(item,base):
    """Path of a file inside the share, from its absolute Baidu path."""
    path=str(item.get('path',''))
    return path[len(base)+1:] if base and path.startswith(base+'/') else str(item.get('server_filename',''))


def run(url,password,out,progress,cancelled):
    if not configured(): raise ValueError('百度后台账号尚未连接，请在首页网盘账号区域扫码')
    while not LOCK.acquire(timeout=.5):
        if cancelled(): raise Cancelled()
    config=ROOT/'private-baidu'/('task-'+out.name)
    share=None
    try:
        import cloud_accounts,cloud_cleanup
        identity,content=cloud_accounts.active_payload('baidu')
        if not content: raise ValueError('百度后台账号尚未连接')
        state=json.loads(content)
        share=Share(url,password,cancelled);authorize(share,state)
        files=share.files()
        if not files: raise ValueError('分享中没有文件')
        total=sum(int(f.get('size',0)) for f in files)
        if total>file_download.LIMIT: raise ValueError('单个分享任务暂限10GB，请缩小分享目录')
        if total>shutil.disk_usage(out).free-1024**3: raise ValueError('主机空间不足')
        try: links=[share.download_url(f) for f in files]
        except AuthorizationRequired: links=None
        if links:
            results=[];folders={};progress(files,results)
            headers={'User-Agent':state.get('pan_ua') or 'Mozilla/5.0','Referer':share.url,'Cookie':'; '.join(c.name+'='+c.value for c in share.session.cookies),'_cookie_domains':('baidu.com','baidupcs.com')}
            for item,link in zip(files,links):
                name=str(item['fs_id'])+'_'+file_download.safe_name(item['server_filename'])
                def report(size):progress(files,results,received=sum(p.stat().st_size for p in results)+size)
                p=file_download.fetch_original(link,out,name,int(item['size']),report,cancelled,headers,item,range_size=download_tuning.profile('baidu')['range_size'],workers=download_tuning.profile('baidu')['workers'])
                results.append(p);folders[p.name]=(file_download.share_folder(_relative(item,getattr(share,'root_parent',''))),item['server_filename']);progress(files,results)
            return file_download.CloudResult(results,'百度原文件直下载；'+download_tuning.describe('baidu')+'；未转存',folders)
        executable=cli();config.mkdir(parents=True,exist_ok=False)
        (config/'pcs_config.json').write_bytes(content)
        env=os.environ.copy();env['BAIDUPCS_GO_CONFIG_DIR']=str(config)
        def command(args,timeout=90,monitor=False):
            # Keep component logs inside task-local private config, never expose signed URLs.
            log=config/'command.tmp'
            with log.open('wb') as stream:
                process=subprocess.Popen([str(executable),*args],env=env,cwd=str(out),stdout=stream,stderr=stream,creationflags=subprocess.CREATE_NO_WINDOW)
                start=time.monotonic();last_change=start;last_state=None
                try:
                    while process.poll() is None:
                        if cancelled(): raise Cancelled()
                        if time.monotonic()-start>timeout: raise TimeoutError('百度下载超时，任务已停止')
                        if monitor:
                            paths=[p for p in out.rglob('*') if p.is_file()]
                            state=[(p.name,p.stat().st_size,p.stat().st_mtime_ns) for p in paths if not p.name.endswith(('.tmp','.BaiduPCS-Go-downloading'))]
                            now=time.monotonic()
                            if state!=last_state:last_change=now;last_state=state
                            if now-last_change>120:raise ValueError('百度下载通道连续120秒没有写入数据，已停止等待；未完整下载的转存保留，请稍后重试或在官方页面检查账号下载权限')
                            progress(files,paths)
                        time.sleep(.5)
                    if process.returncode: raise ValueError('百度后台命令失败，请检查账号有效期和下载权限')
                finally:
                    if process.poll() is None: process.terminate();process.wait(timeout=10)
            return decode_log(log.read_bytes())
        remote='/网站下载任务/'+out.name
        response=command(['mkdir',remote])
        if '创建目录成功' not in response: raise ValueError('未确认本次临时目录创建成功，尚未转存')
        cloud_cleanup.register(out.name,identity,'baidu',remote)
        command(['cd',remote]);command(['config','set','-savedir',str(out)])
        # Transfer only into this task's directory when the share cannot provide
        # an authorized original URL; use normal web permission after transfer.
        command(['transfer',url,*([password] if password and '?pwd=' not in url else [])],timeout=180)
        # The task now owns these transferred originals. Its normal signed-in
        # web download permission can use the same verified bounded CDN ranges.
        # A web authentication error is surfaced; no alternate identity is tried.
        from collections import Counter
        owned=share.owned_files(remote)
        expected=Counter((f['server_filename'],int(f['size'])) for f in files)
        if expected!=Counter((f['server_filename'],int(f['size'])) for f in owned):
            raise ValueError('临时转存文件与原分享大小或数量不符，转存保留')
        links=[share.owned_download_url(f) for f in owned]
        results=[];folders={};progress(files,results)
        headers={'User-Agent':state.get('pan_ua') or 'Mozilla/5.0','Referer':'https://pan.baidu.com/disk/main','Cookie':'; '.join(c.name+'='+c.value for c in share.session.cookies),'_cookie_domains':('baidu.com','baidupcs.com')}
        for item,link in zip(owned,links):
            name=str(item['fs_id'])+'_'+file_download.safe_name(item['server_filename'])
            def report(size):progress(files,results,received=sum(p.stat().st_size for p in results)+size)
            target=file_download.fetch_original(link,out,name,int(item['size']),report,cancelled,headers,item,range_size=download_tuning.profile('baidu')['range_size'],workers=download_tuning.profile('baidu')['workers'])
            results.append(target);folders[target.name]=(file_download.share_folder(_relative(item,remote)),item['server_filename']);progress(files,results)
        note=cloud_cleanup.complete(out,identity,'baidu',remote,results,delete=lambda remote:remove_command(executable,env,remote))
        return file_download.CloudResult(results,'百度官方网页授权原文件；'+download_tuning.describe('baidu')+'；'+note,folders)
    finally:
        if share: share.close()
        if config.parent.resolve()==(ROOT/'private-baidu').resolve() and config.name=='task-'+out.name: shutil.rmtree(config,ignore_errors=True)
        LOCK.release()
