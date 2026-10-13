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
              '.pdf','.docx','.xlsx','.pptx','.txt','.mp4','.mov','.mkv','.webm','.avi','.mp3','.wav','.jpg','.jpeg','.png','.webp','.gif'}
LIMIT = 10 * 1024**3

class TransferHTTPError(ValueError):
    def __init__(self, status, retry_after=None):
        self.status = int(status)
        try: self.retry_after = min(max(float(retry_after or 0),0),30)
        except (ValueError,TypeError): self.retry_after = 0
        super().__init__('文件服务器返回 HTTP '+str(self.status))

def provider(url):
    import cloud_providers
    key=cloud_providers.provider(url)
    if key:return cloud_providers.PROVIDERS[key]
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

def open_public(url, headers=None, offset=0, end=None):
    for _ in range(6):
        u = urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            raise ValueError('请提供 http(s) 文件直链')
        port = u.port or (443 if u.scheme == 'https' else 80)
        if port not in (80,443): raise ValueError('文件直链仅支持 80/443 端口')
        addresses = socket.getaddrinfo(u.hostname,port,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ValueError('文件直链不能访问本机或内网地址')
        response = None
        conn = http.client.HTTPConnection(u.hostname,port,timeout=30)
        try:
            if (headers or {}).get('_local_proxy'):
                # Server-selected local proxy; public destination DNS is still checked
                # and pinned before CONNECT. This cannot be set by website visitors.
                import socks
                chosen=next((a for a in addresses if a[0]==socket.AF_INET),addresses[0])
                conn.sock=socks.socksocket(chosen[0],socket.SOCK_STREAM)
                conn.sock.set_proxy(socks.SOCKS5,'127.0.0.1',10808)
                conn.sock.settimeout(30);conn.sock.connect((chosen[4][0],port))
            else:
                conn.sock = socket.create_connection((addresses[0][4][0],port),timeout=30)
            if u.scheme == 'https': conn.sock = ssl.create_default_context().wrap_socket(conn.sock,server_hostname=u.hostname)
            request_headers=_download_headers(u.hostname, headers, offset, end)
            # HTTPConnection defaults to port 80 even when its socket is wrapped
            # in TLS. Supply the normal HTTPS Host instead of appending :443;
            # signed cloud CDNs can reject that different authority.
            request_headers['Host']=u.hostname
            conn.request('GET',(u.path or '/')+('?' + u.query if u.query else ''),headers=request_headers)
            response = conn.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location'); response.close(); conn.close()
                if not location: raise ValueError('下载跳转缺少目标地址')
                url=urljoin(url,location); continue
            if response.status not in ((200,206) if offset or end is not None else (200,)):
                raise TransferHTTPError(response.status,response.getheader('Retry-After'))
            return conn,response,url
        except Exception:
            if response is not None:response.close()
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
        response.close();conn.close()
        temporary.unlink(missing_ok=True)


def _download_headers(host, extra, offset, end=None):
    headers = {'User-Agent':'FileDownloader/1.0', 'Accept-Encoding':'identity'}
    for name, value in (extra or {}).items():
        if name.lower() == 'cookie':
            # Cloud cookies may only accompany requests to the provider's CDN.
            if not any(host == domain or host.endswith('.'+domain) for domain in (extra or {}).get('_cookie_domains',('quark.cn','uc.cn'))): continue
        elif name.lower() not in ('user-agent', 'referer'): continue
        headers[name] = value
    if offset or end is not None: headers['Range'] = 'bytes=' + str(offset) + '-' + (str(end) if end is not None else '')
    return headers

def xunlei_gcid(path,cancelled=lambda:False):
    """Official Xunlei object digest: SHA1 of the ordered block SHA1 digests."""
    import hashlib
    size=path.stat().st_size;block_size=0x40000
    while size/block_size>0x200 and block_size<0x200000:block_size*=2
    result=hashlib.sha1()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(block_size),b''):
            if cancelled():raise Cancelled()
            result.update(hashlib.sha1(data).digest())
    return result.hexdigest()


MAX_RANGE = 16*1024**2
MAX_WORKERS = 32


def fetch_original(url, directory, name, expected, progress, cancelled, headers=None, checksums=None, range_size=0, workers=3, prefer_chunks=False, renew_url=None):
    """Bounded original transfer, with safe redirects, resume and hash validation."""
    import hashlib, time
    name = safe_name(name); expected = int(expected or 0)
    range_size = min(max(int(range_size or 0), 0), MAX_RANGE)
    if prefer_chunks and expected >= 8*1024**2 and not range_size:
        range_size = 4*1024**2
    partial = directory / (name + '.part'); target = directory / name
    budget = min(LIMIT, max(0, shutil.disk_usage(directory).free - 512*1024**2))
    if expected > budget: raise ValueError('原文件超过可用空间或单文件10GB上限')
    if range_size and expected*2 > max(0,shutil.disk_usage(directory).free-512*1024**2):
        raise ValueError('原文件分段与合并所需的主机空间不足')
    size = 0
    transport_md5 = None
    resume_validator = None
    source_gcid=str((checksums or {}).get('gcid','')).lower()
    if not re.fullmatch(r'[0-9a-f]{40}',source_gcid):source_gcid=''
    has_source_hash = bool(source_gcid) or any(re.fullmatch(pattern, str((checksums or {}).get(k, '')))
                          for k, pattern in (('md5', r'[0-9a-fA-F]{32}'), ('sha1', r'[0-9a-fA-F]{40}')))
    last_report = 0.0
    # This checkpoint belongs only to this invocation. Existing files from an
    # unrelated request are never accepted as resumable chunks.
    checkpoint = {}
    try:
        attempts=6 if range_size else 3
        for attempt in range(attempts):
            if cancelled(): raise Cancelled()
            conn = None; response = None
            # Appending bytes needs either the same strong object validator or
            # a whole-file hash. Otherwise restart to avoid mixing revisions.
            if size and not (resume_validator or transport_md5 or has_source_hash):
                partial.unlink(missing_ok=True)
                size = 0
            try:
                end = min(size + range_size, expected)-1 if range_size and expected else None
                conn, response, final = open_public(url, headers, size, end) if end is not None else open_public(url, headers, size)
                if prefer_chunks and range_size:
                    # Cloud CDNs may throttle long responses but deliver small
                    # original ranges quickly. Servers without range/version
                    # support retain the verified full-stream fallback.
                    if response.status == 200:
                        range_size = 0
                    elif response.status == 206 and not (
                            (response.getheader('ETag') or '').startswith('"')
                            or response.getheader('Content-MD5') or has_source_hash):
                        range_size = 0
                        continue
                content_type = (response.getheader('Content-Type') or '').split(';')[0].lower()
                if content_type in ('text/html','application/xhtml+xml') or (content_type == 'application/json' and not name.lower().endswith('.json')):
                    raise ValueError('平台返回登录或错误页面，没有提供原文件')
                length = int(response.getheader('Content-Length') or 0)
                current_validator = response.getheader('ETag') or ''
                if size and response.status == 206 and resume_validator and current_validator != resume_validator:
                    raise ValueError('续传时原文件版本发生变化，未标记完成')
                if response.status == 200 and current_validator.startswith('"'):
                    resume_validator = current_validator
                # Xunlei's edge Content-MD5 may identify a storage object rather
                # than the delivered original bytes. When its official GCID is
                # available, verify that stronger source identity at completion.
                if not source_gcid and (response.status == 200 or (range_size and response.status == 206 and not size)) and response.getheader('Content-MD5'):
                    import base64
                    value=response.getheader('Content-MD5')
                    # Baidu's bounded original response uses whole-object hex MD5;
                    # other original servers commonly use RFC base64 Content-MD5.
                    if re.fullmatch(r'[0-9a-fA-F]{32}',value):transport_md5=value.lower()
                    else:
                        try: transport_md5 = base64.b64decode(value,validate=True).hex()
                        except ValueError: raise ValueError('原文件服务器校验头格式无效') from None
                    if len(transport_md5)!=32: raise ValueError('原文件服务器校验头格式无效')
                if response.status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.getheader('Content-Range') or '')
                    if not match or int(match[1]) != size: raise ValueError('续传范围无效，原文件未标记完成')
                    total = int(match[3])
                else:
                    size = 0; total = length or expected
                if expected and total and total != expected: raise ValueError('原文件大小与分享列表不一致，未标记完成')
                if total > budget: raise ValueError('原文件超过可用空间')
                if not expected: expected = total
                if range_size and expected and not (response.status==200 and expected<=range_size):
                    # Baidu original URLs require bounded requests; a full GET can
                    # return 403 even when the same authorized URL accepts ranges.
                    if response.status != 206 or response.getheader('Content-Range') != f'bytes {size}-{end}/{expected}':
                        raise ValueError('原文件服务器未确认小块分段范围，未标记完成')
                    validator = response.getheader('ETag')
                    getattr(response,'close',lambda:None)(); conn.close(); conn = None
                    _chunked_original(final, headers, partial, expected, validator,
                                      progress, cancelled, range_size, workers, renew_url, checkpoint)
                    size = expected
                    break
                # Four bounded ranges for large immutable originals. A server that
                # ignores ranges or changes validators falls back to one full stream.
                if not size and total >= 8*1024**2 and total*2 <= max(0,shutil.disk_usage(directory).free-512*1024**2) and (transport_md5 or (response.getheader('ETag') or '').startswith('"') or has_source_hash):
                    validator = response.getheader('ETag')
                    getattr(response,'close',lambda:None)(); conn.close(); conn = None
                    try:
                        _parallel_original(final, headers, partial, total, validator, progress, cancelled)
                        size = total
                        break
                    except Cancelled: raise
                    except (ValueError,OSError,http.client.HTTPException):
                        partial.unlink(missing_ok=True)
                        conn, response, final = open_public(url, headers)
                        if response.status!=200 or int(response.getheader('Content-Length') or 0) not in (0,total):
                            raise ValueError('分段回退时原文件大小变化，未标记完成')
                        if validator and response.getheader('ETag')!=validator:
                            raise ValueError('分段回退时原文件版本变化，未标记完成')
                        if (response.getheader('Content-Type') or '').split(';')[0] in ('text/html','application/json'):
                            raise ValueError('平台返回错误页面')
                        size = 0
                with partial.open('ab' if size else 'wb', buffering=1024*1024) as stream:
                    while True:
                        if cancelled(): raise Cancelled()
                        block = response.read(256*1024)
                        if not block: break
                        size += len(block)
                        if size > budget or (expected and size > expected): raise ValueError('原文件传输大小异常')
                        stream.write(block)
                        now=time.monotonic()
                        if now-last_report>=.25:
                            stream.flush();progress(size);last_report=now
                progress(size)
                if expected and size != expected: raise ConnectionError('文件传输中断')
                if not size: raise ValueError('平台返回空文件')
                break
            except TransferHTTPError as error:
                if attempt == attempts-1: raise
                if error.status == 403 and renew_url:
                    updated_url,updated_headers=renew_url()
                    if updated_url == url: raise
                    url,headers=updated_url,updated_headers
                elif error.status in (429,500,502,503,504):
                    if range_size and error.status in (429,503):workers=max(1,int(workers)//2)
                    time.sleep(max(error.retry_after,1+attempt*2))
                else: raise
                size = partial.stat().st_size if partial.exists() else 0
            except (OSError, http.client.HTTPException):
                size = partial.stat().st_size if partial.exists() else 0
                if attempt == attempts-1: raise ValueError('原文件传输中断，自动续传失败，未标记完成') from None
                for _ in range(4):
                    if cancelled(): raise Cancelled()
                    time.sleep(.25)
            finally:
                if response is not None:getattr(response,'close',lambda:None)()
                if conn: conn.close()
        wanted={}
        if transport_md5:wanted['md5']=transport_md5
        for alg,pattern in [('md5',r'[0-9a-fA-F]{32}'),('sha1',r'[0-9a-fA-F]{40}')]:
            value=str((checksums or {}).get(alg,'')).lower()
            if re.fullmatch(pattern,value):
                if alg in wanted and wanted[alg]!=value:raise ValueError('来源校验值不一致，未标记完成')
                wanted[alg]=value
        hashes={alg:hashlib.new(alg) for alg in wanted}
        if hashes:
            with partial.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    if cancelled():raise Cancelled()
                    for h in hashes.values():h.update(block)
            if any(hashes[k].hexdigest()!=v for k,v in wanted.items()):raise ValueError('原文件哈希校验未通过，未标记完成')
        if source_gcid and xunlei_gcid(partial,cancelled)!=source_gcid:
            raise ValueError('迅雷原文件GCID校验未通过，未标记完成')
        os.replace(partial,target)
        return target
    finally:
        partial.unlink(missing_ok=True)
        for piece in checkpoint.get('pieces', []): piece.unlink(missing_ok=True)


def _parallel_original(url, headers, partial, total, validator, progress, cancelled):
    """Strict ranges, identical object version, bounded memory, ordered merge."""
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    stop=threading.Event();lock=threading.Lock();counts=[0]*4;last=[0.0]
    pieces=[partial.with_name(partial.name+'.'+str(i)) for i in range(4)]
    def transfer(i):
        start=total*i//4;end=total*(i+1)//4-1;wanted=end-start+1
        for attempt in range(2):
            if cancelled():raise Cancelled()
            if stop.is_set():raise ValueError('分段下载已停止')
            conn=None;r=None
            try:
                conn,r,_=open_public(url,headers,start,end)
                expected='bytes '+str(start)+'-'+str(end)+'/'+str(total)
                if r.status!=206 or r.getheader('Content-Range')!=expected or int(r.getheader('Content-Length') or 0) not in (0,wanted):
                    raise ValueError('服务器未正确支持分段范围')
                if validator and r.getheader('ETag')!=validator:raise ValueError('原文件版本发生变化')
                if (r.getheader('Content-Type') or '').split(';')[0] in ('text/html','application/json'):raise ValueError('分段返回错误页面')
                size=0
                with pieces[i].open('wb',buffering=1024*1024) as f:
                    while True:
                        if cancelled():raise Cancelled()
                        if stop.is_set():raise ValueError('分段下载已停止')
                        data=r.read(256*1024)
                        if not data:break
                        size+=len(data)
                        if size>wanted:raise ValueError('分段大小异常')
                        f.write(data)
                        with lock:
                            counts[i]=size;now=time.monotonic()
                            if now-last[0]>=.25:
                                f.flush();progress(sum(counts));last[0]=now
                if size!=wanted:raise ConnectionError('分段未完整传输')
                return
            except (OSError,http.client.HTTPException):
                if attempt:stop.set();raise
                with lock:counts[i]=0
            except BaseException:
                stop.set();raise
            finally:
                if r is not None:r.close()
                if conn:conn.close()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(transfer,i) for i in range(4)]
            for task in futures:task.result()
        with partial.open('wb',buffering=1024*1024) as merged:
            for piece in pieces:
                with piece.open('rb') as f:
                    while True:
                        if cancelled():raise Cancelled()
                        block=f.read(1024*1024)
                        if not block:break
                        merged.write(block)
                piece.unlink()
        if partial.stat().st_size!=total:raise ValueError('分段合并后大小异常')
        progress(total)
    finally:
        stop.set()
        for piece in pieces:piece.unlink(missing_ok=True)


def _chunked_original(url, headers, partial, total, validator, progress, cancelled,
                      chunk_size=4*1024**2, workers=3, renew_url=None, checkpoint=None):
    """Bounded original ranges, with interruption and signed-URL recovery.

    Each range must identify the same object and exact requested byte offsets.
    Permission errors stop the transfer; transient errors retry with backoff.
    """
    import threading, time
    from concurrent.futures import ThreadPoolExecutor
    count=(total+chunk_size-1)//chunk_size
    pieces=[partial.with_name(partial.name+'.chunk-'+str(i)) for i in range(count)]
    version=(total,chunk_size,validator)
    resumed=bool(checkpoint and checkpoint.get('version'))
    if resumed and checkpoint['version']!=version:
        raise ValueError('重试时原文件版本发生变化，未标记完成')
    if checkpoint is not None:
        checkpoint.update(version=version,pieces=pieces)
    if not resumed:
        for piece in pieces:piece.unlink(missing_ok=True)
    received=[0]*count;lock=threading.Lock();stop=threading.Event();cursor=[0];last=[0.0]
    ceiling=min(max(int(workers),1),MAX_WORKERS);limit=[ceiling];calm=[0]
    if resumed:
        for index,piece in enumerate(pieces):
            if piece.exists():
                size=piece.stat().st_size
                if size>min(chunk_size,total-index*chunk_size):raise ValueError('已接收分段长度异常')
                received[index]=size
    current=[url,headers];renew_lock=threading.Lock();renewals=[0];failure=[None]
    def abort(error):
        with lock:
            if failure[0] is None:failure[0]=error
            stop.set()
    def stopped():
        return failure[0] or ValueError('原文件分段下载已停止')
    def renew(observed):
        with renew_lock:
            if current[0] != observed: return
            try:
                if not renew_url or renewals[0]>=16:raise TransferHTTPError(403)
                if cancelled():raise Cancelled()
                updated_url,updated_headers=renew_url()
                if updated_url==observed:raise TransferHTTPError(403)
            except BaseException as error:abort(error);raise
            current[:]=[updated_url,updated_headers];renewals[0]+=1
    def report():
        now=time.monotonic()
        if now-last[0]>=.25:
            progress(sum(received));last[0]=now
    def throttled():
        # Halve active connections on 429/503; grow back one at a time later.
        with lock:
            limit[0]=max(1,limit[0]//2);calm[0]=0
    def settled():
        with lock:
            calm[0]+=1
            if calm[0]>=8 and limit[0]<ceiling:limit[0]+=1;calm[0]=0
    def worker(slot):
        while True:
            if slot>=limit[0]:
                with lock:
                    if cursor[0]>=count:return
                if cancelled():raise Cancelled()
                if stop.is_set():raise stopped()
                time.sleep(.25);continue
            with lock:
                if cursor[0]>=count:return
                index=cursor[0];cursor[0]+=1
            start=index*chunk_size;end=min(start+chunk_size,total)-1;wanted=end-start+1
            size=received[index]
            if size==wanted:continue
            for attempt in range(6):
                if cancelled():raise Cancelled()
                if stop.is_set():raise stopped()
                conn=None;r=None
                try:
                    with renew_lock:active_url,active_headers=current
                    conn,r,_=open_public(active_url,active_headers,start+size,end)
                    if r.status!=206 or r.getheader('Content-Range')!=f'bytes {start+size}-{end}/{total}':
                        raise ValueError('原文件分段范围不一致，未标记完成')
                    if int(r.getheader('Content-Length') or 0) not in (0,wanted-size):
                        raise ValueError('原文件分段长度不一致，未标记完成')
                    if validator and r.getheader('ETag')!=validator:
                        raise ValueError('原文件版本发生变化，未标记完成')
                    if (r.getheader('Content-Type') or '').split(';')[0] in ('text/html','application/json'):
                        raise ValueError('平台未提供原文件分段')
                    with pieces[index].open('ab' if size else 'wb',buffering=1024*1024) as f:
                        while size<wanted:
                            if cancelled():raise Cancelled()
                            if stop.is_set():raise stopped()
                            data=r.read(min(256*1024,wanted-size))
                            if not data:raise ConnectionError('原文件分段传输中断')
                            f.write(data);size+=len(data)
                            with lock:received[index]=size;report()
                        if r.read(1):raise ValueError('原文件分段超出请求长度')
                    settled()
                    break
                except TransferHTTPError as error:
                    if attempt==5:abort(error);raise
                    if error.status==403 and renew_url:renew(active_url)
                    elif error.status in (429,500,502,503,504):
                        if error.status in (429,503):throttled()
                        # Respect server backoff and retain already checked bytes.
                        for _ in range(int(max(error.retry_after,1+attempt*2)*4)):
                            if cancelled():raise Cancelled()
                            if stop.is_set():raise stopped()
                            time.sleep(.25)
                    else:abort(error);raise
                except (OSError,http.client.HTTPException) as error:
                    size=pieces[index].stat().st_size if pieces[index].exists() else 0
                    with lock:received[index]=size
                    if size==wanted:break
                    if attempt==5:abort(error);raise
                    time.sleep(min(1+attempt,5))
                except BaseException as error:
                    abort(error);raise
                finally:
                    if r is not None:r.close()
                    if conn:conn.close()
    try:
        with ThreadPoolExecutor(max_workers=ceiling) as pool:
            tasks=[pool.submit(worker,slot) for slot in range(ceiling)]
            for task in tasks:task.result()
        with partial.open('wb',buffering=1024*1024) as merged:
            for index,piece in enumerate(pieces):
                if piece.stat().st_size!=min(chunk_size,total-index*chunk_size):
                    raise ValueError('原文件分段未完整接收')
                with piece.open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):
                        if cancelled():raise Cancelled()
                        merged.write(block)
                piece.unlink()
        if partial.stat().st_size!=total:raise ValueError('原文件分段合并后大小不一致')
        progress(total)
    finally:
        stop.set()
        if checkpoint is None:
            for piece in pieces:piece.unlink(missing_ok=True)


class CloudResult(list):
    def __init__(self,paths,note=''):
        super().__init__(paths);self.note=note
