"""Download images exposed by a public Xiaohongshu note, without login bypass."""
import json,re
from urllib.parse import urlsplit
from yt_dlp.utils import js_to_json
from PIL import Image
import file_download
from common import Cancelled
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36'

def supported(url):
    host=(urlsplit(url).hostname or '').lower()
    return host in ('xiaohongshu.com','www.xiaohongshu.com','xhslink.com','www.xhslink.com','xhslink.cn','www.xhslink.cn')

def parse(page, video_id=None):
    if any(marker in page for marker in ('IP存在风险', 'IP 存在风险', '300012', '请完成安全验证')):
        raise ValueError('小红书要求官方人工安全验证；请在管理员授权窗口完成验证，自动下载已停止')
    match=re.search(r'window\.__INITIAL_STATE__\s*=\s*(.*?)</script>',page,re.S)
    if not match:raise ValueError('小红书未返回公开笔记，请粘贴 App 的完整分享链接（保留链接参数）；登录或验证页面不能下载')
    state=json.loads(js_to_json(match[1]))
    notes=state.get('note',{}).get('noteDetailMap',{})
    note=(notes.get(video_id) or {}).get('note') if video_id else next((v.get('note') for v in notes.values() if isinstance(v,dict) and v.get('note')),None)
    if not note:raise ValueError('小红书笔记不可公开读取，链接可能失效或需要登录；请使用 App 的完整分享链接（保留链接参数）')
    if note.get('type')=='video':return None
    images=[]
    for image in note.get('imageList',[]):
        candidates=[v for v in image.get('infoList',[]) if v.get('url')]
        preferred=next((v for v in candidates if v.get('imageScene')=='WB_DFT'),None)
        url=(preferred or {}).get('url') or image.get('urlDefault') or (candidates[0].get('url') if candidates else None)
        if not url:raise ValueError('小红书未提供可下载的图片地址')
        if url.startswith('//'):url='https:'+url
        elif url.startswith('http://'):url='https://'+url[7:]
        host=(urlsplit(url).hostname or '').lower()
        if not any(host==v or host.endswith('.'+v) for v in ('xhscdn.com','xiaohongshu.com')):raise ValueError('小红书图片地址不是平台 CDN')
        images.append(url)
    if not images:raise ValueError('小红书笔记没有公开可下载的图片')
    if len(images)>100:raise ValueError('单篇图文暂限100张图片')
    return note.get('title') or '小红书图片',images

def download(url,out,progress,cancelled):
    headers={'User-Agent':UA,'Referer':'https://www.xiaohongshu.com/'}
    import requests,socket,ipaddress
    from urllib.parse import urljoin
    def public_page(session,address):
        # This adapter only reads the fixed official public domains, including
        # every short-link redirect. It never accepts arbitrary page targets.
        for _ in range(6):
            parsed=urlsplit(address)
            if not supported(address) or parsed.scheme not in ('http','https') or parsed.username or parsed.password or parsed.port not in (None,80,443):raise ValueError('小红书页面跳转到非官方地址，已停止')
            addresses=socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('小红书页面不能访问内网')
            with session.get(address,allow_redirects=False,stream=True,timeout=(10,25)) as response:
                if response.status_code in (301,302,303,307,308):
                    address=urljoin(address,response.headers.get('Location',''));continue
                response.raise_for_status()
                raw=bytearray()
                for block in response.iter_content(64*1024):
                    if cancelled():raise Cancelled()
                    raw.extend(block)
                    if len(raw)>8*1024**2:raise ValueError('笔记页面过大')
                return raw.decode('utf-8',errors='replace'),address
        raise ValueError('小红书页面跳转次数过多')
    result=None
    with requests.Session() as session:
        session.trust_env=False;session.headers.update(headers)
        import video_platform_accounts as accounts
        state = accounts.load('xiaohongshu')
        if state:
            session.headers['User-Agent'] = state.get('ua') or UA
            headers['User-Agent'] = session.headers['User-Agent']
            for cookie in accounts.live_cookies(state):
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie.get('path','/'), expires=int(cookie['expires']) if cookie.get('expires', -1) > 0 else None)
        page, final_url = public_page(session,url)
        match = re.search(r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})',urlsplit(final_url).path)
        note_id = match.group(1) if match else None
        if any(marker in page for marker in ('IP\u5b58\u5728\u98ce\u9669', 'IP \u5b58\u5728\u98ce\u9669', '300012', '\u8bf7\u5b8c\u6210\u5b89\u5168\u9a8c\u8bc1')):
            parse(page, note_id)  # Report the official challenge; no route switching.
        from focused_extractors import browser_snapshot, xhs_note
        if not note_id or not xhs_note(page, note_id):
            # A valid account can require its ordinary browser to read the note.
            snapshot = browser_snapshot(url, 'xiaohongshu')
            page, final_url = snapshot['html'], snapshot['url']
            match = re.search(r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})', urlsplit(final_url).path)
            note_id = match.group(1) if match else None
        result=parse(page,note_id)
        # Browser refresh owns its newer state; HTTP cannot overwrite that revision.
        if state:
            updates = {(c.domain, c.name, c.path): c for c in session.cookies}
            rotated = []
            for c in state['cookies']:
                c = dict(c); update = updates.get((c['domain'], c['name'], c.get('path', '/')))
                if update:
                    c['value'] = update.value
                    if update.expires is not None: c['expires'] = update.expires
                rotated.append(c)
            accounts.refresh('xiaohongshu', {**state, 'cookies': rotated}, state['_revision'])
    if result is None:return None
    title,urls=result;paths=[]
    for index,source in enumerate(urls):
        if cancelled():raise Cancelled()
        name=file_download.safe_name(title[:65])+f'_{index+1:02d}.jpg'
        def report(size):progress(index,len(urls),size)
        p=file_download.fetch_original(source,out,name,0,report,cancelled,headers)
        try:
            with Image.open(p) as image:
                format=image.format;image.verify()
            ext={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','GIF':'.gif'}.get(format)
            if not ext:raise ValueError('图片格式不受支持')
            if ext!=p.suffix:
                target=p.with_suffix(ext);p.replace(target);p=target
        except Exception:
            p.unlink(missing_ok=True);raise ValueError('平台图片完整性校验失败') from None
        paths.append(p);progress(index+1,len(urls),p.stat().st_size)
    return title,paths
