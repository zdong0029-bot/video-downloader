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

def open_public(url, headers=None, offset=0):
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
            conn.request('GET',(u.path or '/')+('?' + u.query if u.query else ''),headers=_download_headers(u.hostname, headers, offset))
            response = conn.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location'); conn.close()
                if not location: raise ValueError('下载跳转缺少目标地址')
                url=urljoin(url,location); continue
            if response.status not in ((200,206) if offset else (200,)): raise ValueError('文件服务器返回 HTTP '+str(response.status))
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


def _download_headers(host, extra, offset):
    headers = {'User-Agent':'FileDownloader/1.0', 'Accept-Encoding':'identity'}
    for name, value in (extra or {}).items():
        if name.lower() == 'cookie':
            # Cloud cookies may only accompany requests to the provider's CDN.
            if not any(host == domain or host.endswith('.'+domain) for domain in (extra or {}).get('_cookie_domains',('quark.cn','uc.cn'))): continue
        elif name.lower() not in ('user-agent', 'referer'): continue
        headers[name] = value
    if offset: headers['Range'] = 'bytes=' + str(offset) + '-'
    return headers


def fetch_original(url, directory, name, expected, progress, cancelled, headers=None, checksums=None):
    """Bounded original transfer, with safe redirects, resume and hash validation."""
    import hashlib, time
    name = safe_name(name); expected = int(expected or 0)
    partial = directory / (name + '.part'); target = directory / name
    budget = min(LIMIT, max(0, shutil.disk_usage(directory).free - 512*1024**2))
    if expected > budget: raise ValueError('原文件超过可用空间或单文件10GB上限')
    size = 0
    transport_md5 = None
    try:
        for attempt in range(3):
            if cancelled(): raise Cancelled()
            conn = None
            try:
                conn, response, final = open_public(url, headers, size)
                content_type = (response.getheader('Content-Type') or '').split(';')[0].lower()
                if content_type in ('text/html','application/xhtml+xml') or (content_type == 'application/json' and not name.lower().endswith('.json')):
                    raise ValueError('平台返回登录或错误页面，没有提供原文件')
                length = int(response.getheader('Content-Length') or 0)
                if response.status == 200 and response.getheader('Content-MD5'):
                    import base64
                    try: transport_md5 = base64.b64decode(response.getheader('Content-MD5'),validate=True).hex()
                    except ValueError: raise ValueError('原文件服务器校验头格式无效') from None
                    if len(transport_md5)!=32: raise ValueError('原文件服务器校验头格式无效')
                if response.status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.getheader('Content-Range') or '')
                    if not match or int(match[1]) != size: raise ValueError('续传范围无效，原文件未标记完成')
                    total = int(match[3])
                else:
                    size = 0; total = length
                if expected and total and total != expected: raise ValueError('原文件大小与分享列表不一致，未标记完成')
                if total > budget: raise ValueError('原文件超过可用空间')
                if not expected: expected = total
                with partial.open('ab' if size else 'wb') as stream:
                    while True:
                        if cancelled(): raise Cancelled()
                        block = response.read(256*1024)
                        if not block: break
                        size += len(block)
                        if size > budget or (expected and size > expected): raise ValueError('原文件传输大小异常')
                        stream.write(block);stream.flush();progress(size)
                if expected and size != expected: raise ConnectionError('文件传输中断')
                if not size: raise ValueError('平台返回空文件')
                break
            except (OSError, http.client.HTTPException):
                size = partial.stat().st_size if partial.exists() else 0
                if attempt == 2: raise ValueError('原文件传输中断，自动续传失败，未标记完成') from None
                for _ in range(4):
                    if cancelled(): raise Cancelled()
                    time.sleep(.25)
            finally:
                if conn: conn.close()
        if transport_md5:
            h = hashlib.md5()
            with partial.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    if cancelled(): raise Cancelled()
                    h.update(block)
            if h.hexdigest()!=transport_md5: raise ValueError('原文件服务器 Content-MD5 校验未通过，未标记完成')
        for alg, pattern in [('md5',r'[0-9a-fA-F]{32}'),('sha1',r'[0-9a-fA-F]{40}')]:
            value = str((checksums or {}).get(alg,'')).lower()
            if not re.fullmatch(pattern,value): continue
            h = hashlib.new(alg)
            with partial.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    if cancelled(): raise Cancelled()
                    h.update(block)
            if h.hexdigest() != value: raise ValueError('原文件 '+alg.upper()+' 校验未通过，未标记完成')
        os.replace(partial,target)
        return target
    finally:
        partial.unlink(missing_ok=True)


class CloudResult(list):
    def __init__(self,paths,note=''):
        super().__init__(paths);self.note=note
