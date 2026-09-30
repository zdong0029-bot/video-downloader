"""Original-file downloads; resolve and pin public endpoints on every redirect."""
import http.client
import ipaddress
import os
import re
import shutil
import socket
import ssl
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit, urljoin, unquote
from common import Cancelled

EXTENSIONS = {'.zip','.7z','.rar','.exe','.msi','.apk','.dmg','.iso','.pkg',
              '.pdf','.docx','.xlsx','.pptx','.txt','.mp4','.mov','.mkv','.webm','.avi','.mp3','.wav'}
LIMIT = 10 * 1024**3

def provider(url):
    host = (urlsplit(url).hostname or '').lower()
    for suffix, name in [('pan.baidu.com','百度网盘'),('yun.baidu.com','百度网盘'),
                         ('pan.quark.cn','夸克网盘'),('alipan.com','阿里云盘'),
                         ('aliyundrive.com','阿里云盘'),('feishu.cn','飞书')]:
        if host == suffix or host.endswith('.'+suffix): return name
    return None

def is_file(url):
    return Path(unquote(urlsplit(url).path)).suffix.lower() in EXTENSIONS

def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:180]
    if not value or value.lower() in ('meta.json', 'download.part', '.hidden.json', '.cloud-incomplete'): value = 'download.bin'
    if value.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *['COM'+str(i) for i in range(10)], *['LPT'+str(i) for i in range(10)]}: value = '_'+value
    return value

def open_public(url):
    for _ in range(6):
        u = urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            raise ValueError('请提供 http(s) 文件直链')
        port = u.port or (443 if u.scheme == 'https' else 80)
        if port not in (80,443): raise ValueError('文件直链仅支持 80/443 端口')
        addresses = socket.getaddrinfo(u.hostname,port,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ValueError('文件直链不能访问本机或内网地址')
        conn = http.client.HTTPConnection(u.hostname,port,timeout=30)
        conn.sock = socket.create_connection((addresses[0][4][0],port),timeout=30)
        try:
            if u.scheme == 'https': conn.sock = ssl.create_default_context().wrap_socket(conn.sock,server_hostname=u.hostname)
            conn.request('GET',(u.path or '/')+('?' + u.query if u.query else ''),headers={'User-Agent':'FileDownloader/1.0','Accept-Encoding':'identity'})
            response = conn.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location'); conn.close()
                if not location: raise ValueError('下载跳转缺少目标地址')
                url=urljoin(url,location); continue
            if response.status != 200: raise ValueError('文件服务器返回 HTTP '+str(response.status))
            return conn,response,url
        except Exception:
            conn.close(); raise
    raise ValueError('文件下载跳转次数过多')

def download(url, directory, progress, cancelled):
    conn,response,final = open_public(url)
    temporary = directory / 'download.part'
    try:
        content_type=(response.getheader('Content-Type') or '').split(';')[0].lower()
        if content_type in ('text/html','application/xhtml+xml','application/json'):
            raise ValueError('这是网页或登录接口，不是原文件下载地址；请取得文件直链')
        header=Message(); header['Content-Disposition']=response.getheader('Content-Disposition') or ''
        name=safe_name(header.get_filename() or unquote(urlsplit(final).path.rsplit('/',1)[-1]) or 'download.bin')
        total=int(response.getheader('Content-Length') or 0)
        free=shutil.disk_usage(directory).free
        budget=min(LIMIT,max(0,free-512*1024**2))
        if total > budget: raise ValueError('文件超过可用空间或单文件 10 GB 上限')
        size=0
        with temporary.open('wb') as f:
            while True:
                if cancelled(): raise Cancelled()
                data=response.read(256*1024)
                if not data: break
                size+=len(data)
                if size>budget: raise ValueError('文件超过可用空间或单文件 10 GB 上限')
                f.write(data); progress(size,total,name)
        if total and size!=total: raise ValueError('文件传输不完整，请重试')
        if not size: raise ValueError('服务器返回空文件')
        target=directory/name
        os.replace(temporary,target)
        return target
    finally:
        conn.close()
        temporary.unlink(missing_ok=True)
