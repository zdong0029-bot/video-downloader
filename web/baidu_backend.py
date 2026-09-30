"""Bounded shared-account downloads via BaiduPCS-Go, isolated per job."""
import json
import hashlib
import re
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from common import Cancelled
from baidu_share import Share
from file_download import safe_name

ROOT=Path(__file__).resolve().parent
LOCK=threading.Lock()

def configured():
    return (ROOT/'baidu-connected.flag').is_file() and (ROOT/'private-baidu').is_dir()

def run(url,password,out,progress,cancelled):
    if not configured(): raise ValueError('百度后台账号尚未连接，请在主机的专用授权窗口完成连接')
    share=Share(url,password,cancelled)
    try: files=share.files()
    finally: share.close()
    if not files: raise ValueError('分享中没有文件')
    if sum(int(f.get('size',0)) for f in files)>10*1024**3:
        raise ValueError('单个分享任务暂限 10 GB，请缩小分享目录')
    if sum(int(f.get('size',0)) for f in files)>shutil.disk_usage(out).free-1024**3:
        raise ValueError('主机空间不足')
    executable=next((ROOT/'pcs').rglob('BaiduPCS-Go.exe'), None)
    if executable is None: raise ValueError('百度下载组件缺失，请在主机重新安装连接组件')
    while not LOCK.acquire(timeout=.5):
        if cancelled(): raise Cancelled()
    config=ROOT/'private-baidu'/('task-'+out.name)
    env=os.environ.copy(); env['BAIDUPCS_GO_CONFIG_DIR']=str(config)
    def command(args,timeout=90,monitor=False):
        process=subprocess.Popen([str(executable),*args],env=env,cwd=str(out),
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        start=time.monotonic()
        try:
            while process.poll() is None:
                if cancelled(): raise Cancelled()
                if time.monotonic()-start>timeout: raise TimeoutError('百度下载超时，任务已停止')
                if monitor: progress(files,list(out.rglob('*')))
                time.sleep(.5)
            if process.returncode: raise ValueError('百度后台命令失败，请检查账号有效期和下载权限')
        finally:
            if process.poll() is None: process.terminate(); process.wait(timeout=10)
    try:
        config.mkdir(parents=True,exist_ok=False)
        for p in (ROOT/'private-baidu').iterdir():
            if p.is_file() and p.name == 'pcs_config.json': shutil.copy2(p,config/p.name)
        remote='/网站下载任务/'+out.name
        command(['mkdir',remote]); command(['cd',remote])
        command(['config','set','-savedir',str(out)])
        command(['transfer','--download',url,*([password] if password and '?pwd=' not in url else [])],timeout=43200,monitor=True)
        # CLI exit codes alone do not prove success: match all expected original sizes.
        candidates=[p for p in out.rglob('*') if p.is_file() and p.name!='meta.json' and p.suffix not in ('.part','.tmp','.json')]
        results=[]; used=set()
        for item in files:
            source=next((p for p in candidates if p not in used and p.name==item['server_filename'] and p.stat().st_size==int(item['size'])),None)
            if source is None: raise ValueError('原文件未完整下载，不能标记完成；请检查百度账号或平台提示')
            checksum=item.get('md5','')
            if re.fullmatch(r'[0-9a-fA-F]{32}',checksum):
                digest=hashlib.md5()
                with source.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):
                        if cancelled(): raise Cancelled()
                        digest.update(block)
                if digest.hexdigest().lower()!=checksum.lower(): raise ValueError('原文件 MD5 校验失败，未标记完成')
            used.add(source)
            target=out/(str(item['fs_id'])+'_'+safe_name(item['server_filename']))
            if source!=target: source.replace(target)
            results.append(target)
        return results
    finally:
        # Only task-local credential copies; never remove cloud files or user config.
        if config.parent==ROOT/'private-baidu' and config.name=='task-'+out.name:
            shutil.rmtree(config,ignore_errors=True)
        LOCK.release()
