"""List public profile/playlist videos without downloading their media."""
from __future__ import annotations
import re
import hashlib
import http.cookiejar
import time
from urllib.parse import urlsplit,parse_qs
import yt_dlp
from common import Cancelled

class CatalogLogger:
    """Do not call a failed pagination request a complete native playlist."""
    def __init__(self): self.pagination_failed=False
    def debug(self, message): pass
    def error(self, message): self.pagination_failed=True
    def warning(self, message):
        text=str(message).lower()
        if any(word in text for word in ('giving up', 'unable to download', 'failed to download', 'incomplete', 'same page')):
            self.pagination_failed=True

HOSTS = ('youtube.com', 'youtu.be', 'bilibili.com', 'b23.tv', 'douyin.com',
         'iesdouyin.com', 'tiktok.com', 'vimeo.com', 'dailymotion.com',
         'instagram.com', 'facebook.com', 'x.com', 'twitter.com',
         'reddit.com', 'weibo.com', 'weibo.cn', 'xiaohongshu.com', 'ixigua.com', 'twitch.tv',
         'nicovideo.jp', 'nico.ms', 'ted.com', 'archive.org', 'youku.com', 'iqiyi.com',
         'iq.com', 'mgtv.com', 'qq.com', 'acfun.cn', 'kuaishou.com', 'toutiao.com',
         'baidu.com', 'cctv.com', 'cntv.cn', 'sohu.com', 'zhihu.com', 'rumble.com', 'streamable.com')

def _host(host, domain):
    return host == domain or host.endswith('.' + domain)

def platform_url(url):
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower()
        return (p.scheme in ('http', 'https') and not p.username and not p.password
                and p.port in (None, 80, 443)
                and any(host == h or host.endswith('.' + h) for h in HOSTS))
    except ValueError:
        return False

def is_profile(url):
    if not platform_url(url):
        return False
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    host, path = (p.hostname or '').lower(), p.path.rstrip('/')
    query = parse_qs(p.query)
    if host=='channels.weixin.qq.com':return path=='/web/pages/home'
    if _host(host, 'douyin.com'):
        return bool(re.fullmatch(r'/user/[^/]+', path)) and not query.get('modal_id')
    if _host(host, 'tiktok.com'):
        return bool(re.fullmatch(r'/@[^/]+', path))
    if _host(host, 'youtube.com'):
        return bool(re.fullmatch(r'/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)(?:/(?:videos|shorts|streams|featured|playlists))?', path)) or (path == '/playlist' and bool(query.get('list')))
    if host == 'space.bilibili.com':
        return bool(re.fullmatch(r'/\d+(?:/(?:video|upload/video|channel/detail|lists))?',path))
    if _host(host, 'instagram.com'):
        return bool(re.fullmatch(r'/[^/]+(?:/reels)?', path)) and path.split('/')[1].lower() not in ('p','reel','reels','stories','explore','accounts','direct','about','developer','legal')
    if _host(host,'xiaohongshu.com'):return bool(re.fullmatch(r'/user/profile/[a-f0-9]{24}',path))
    if _host(host,'kuaishou.com'):return bool(re.fullmatch(r'/profile/[A-Za-z0-9]+',path))
    if _host(host,'dailymotion.com'):return bool(re.fullmatch(r'/(?:user/|playlist/)?[^/]+',path)) and path not in ('/video','/embed','/search')
    if _host(host,'twitch.tv'):return host!='clips.twitch.tv' and bool(re.fullmatch(r'/[^/]+(?:/(?:videos|clips|collections))?',path)) and path not in ('/directory','/search','/videos','/downloads','/settings','/login')
    if _host(host,'weibo.com'):return bool(re.fullmatch(r'/u/\d+',path))
    if _host(host,'vimeo.com'):return bool(re.fullmatch(r'/(?:user\d+|[A-Za-z][\w-]*)(?:/videos)?',path) or re.match(r'^/(?:showcase|album|channels)/[^/]+$',path))
    if _host(host,'nicovideo.jp'):return bool(re.match(r'^/(?:user/\d+(?:/(?:video|mylist/\d+|series/\d+))?|mylist/\d+|series/\d+)$',path))
    if _host(host,'ted.com'):return path.startswith('/playlists/')
    if _host(host,'archive.org'):return bool(re.fullmatch(r'/details/[^/]+',path))
    if _host(host,'youku.com'):
        from youku_profile import author_uid
        return path.startswith('/show/') or bool(author_uid(url))
    if _host(host,'iqiyi.com'):return path.startswith('/a_')
    if _host(host,'iq.com'):return path.startswith('/album/')
    if host=='v.qq.com':return bool(re.fullmatch(r'/x/cover/[\w]+\.html',path))
    if _host(host,'acfun.cn'):return bool(re.fullmatch(r'/u/\d+',path))
    if _host(host,'mgtv.com'):return bool(re.fullmatch(r'/h/\d+\.html',path))
    if _host(host,'ixigua.com'):return bool(re.fullmatch(r'/home/[^/]+',path))
    if _host(host,'toutiao.com'):return bool(re.fullmatch(r'/c/user/(?:token/)?[^/]+',path))
    if host=='haokan.baidu.com':return path.startswith('/author/')
    if _host(host,'facebook.com'):return bool(re.fullmatch(r'/[^/]+/(?:videos|reels)',path))
    if _host(host,'x.com') or _host(host,'twitter.com'):return bool(re.fullmatch(r'/[^/]+(?:/media)?',path)) and path.split('/')[1].lower() not in ('home','i','explore','search','settings','login','signup','notifications','messages','tos','privacy')
    if _host(host,'reddit.com'):return bool(re.fullmatch(r'/(?:r|user)/[^/]+',path))
    if _host(host,'rumble.com'):return path.startswith(('/c/','/user/'))
    if _host(host,'zhihu.com'):return path.startswith('/people/')
    if _host(host,'sohu.com'):return host=='my.tv.sohu.com' and path.endswith('/index.shtml')
    if _host(host,'cctv.com'):return path.endswith('/index.shtml')
    return False

def normalize_entry(entry, index, source_url=''):
    if not isinstance(entry, dict) or entry.get('_type') in ('playlist', 'url_transparent_playlist', 'url_playlist'):
        return None
    url = entry.get('webpage_url') or entry.get('url') or ''
    if source_url and url.split('?')[0].rstrip('/') == source_url.split('?')[0].rstrip('/'):
        url = entry.get('url') or ''
        if _host(urlsplit(source_url).hostname or '', 'weibo.com') and entry.get('display_id'):
            uid=urlsplit(source_url).path.rstrip('/').split('/')[-1]
            url='https://weibo.com/'+uid+'/'+str(entry['display_id'])
    ie = (entry.get('ie_key') or entry.get('extractor_key') or '').lower()
    if not url.startswith(('http://', 'https://')) and ie == 'youtube':
        url = 'https://www.youtube.com/watch?v=' + (entry.get('id') or url)
    if not platform_url(url):
        return None
    if entry.get('availability') in ('private', 'premium_only', 'subscriber_only', 'needs_auth'):
        return None
    thumbs = entry.get('thumbnails') or []
    thumb = entry.get('thumbnail') or (thumbs[-1].get('url', '') if thumbs else '')
    if not str(thumb).startswith('https://'):
        thumb = ''
    return {'id': hashlib.sha256(url.encode()).hexdigest()[:16], 'url': url,
            'title': str(entry.get('title') or entry.get('id') or '视频 ' + str(index))[:500],
            'thumbnail': thumb, 'duration': entry.get('duration') or 0}

def resolve(url, limit=0, cookie_options=None, on_update=None, cancelled=None):
    if not is_profile(url):
        raise ValueError('识别失败：请提供作者主页或合集链接；单个作品请使用视频图片提取')
    parts = urlsplit(url)
    host=parts.hostname or ''
    if host=='channels.weixin.qq.com' and parts.path.rstrip('/')=='/web/pages/home':
        if not parse_qs(parts.query).get('username'):
            raise ValueError('这是视频号总入口，请提供具体作者主页；推荐流不能代表某位作者的全部作品')
        raise ValueError('视频号单条下载与作者主页是不同接口；现有元宝授权未提供作者分页。需要接通微信官方作者列表会话，当前还未完成，不能把部分链接标为全部')
    if _host(host,'xiaohongshu.com') or _host(host,'kuaishou.com') or _host(host,'acfun.cn'):
        from profile_browser import resolve as browser_resolve
        platform='xiaohongshu' if _host(host,'xiaohongshu.com') else 'kuaishou' if _host(host,'kuaishou.com') else 'acfun'
        return browser_resolve(url,platform,limit,cookie_options,on_update,cancelled)
    special = 'haokan' if host=='haokan.baidu.com' else 'mgtv' if _host(host,'mgtv.com') else 'cctv' if _host(host,'cctv.com') else 'iqiyi' if _host(host,'iqiyi.com') and parts.path.startswith('/a_') else 'sohu' if host=='my.tv.sohu.com' and parts.path.startswith(('/us/','/pl/')) else None
    if special:
        if special=='cctv':return _cctv(url,limit,on_update,cancelled)
        from profile_browser import resolve as browser_resolve
        return browser_resolve(url,special,limit,cookie_options,on_update,cancelled)
    for platform in ('instagram','facebook','ixigua'):
        if _host(host,platform+'.com'):
            from profile_browser import resolve as browser_resolve
            return browser_resolve(url,'xigua' if platform=='ixigua' else platform,limit,cookie_options,on_update,cancelled)
    if _host(host,'youku.com'):
        from youku_profile import author_uid,resolve as youku_resolve
        if author_uid(url):return youku_resolve(url,limit,cookie_options,on_update,cancelled)
    if _host(host,'douyin.com'):
        return _douyin(url, limit, cookie_options, on_update, cancelled)
    if _host(host,'instagram.com'):
        url=re.sub(r'/reels/?$', '/', url.split('?')[0]);parts=urlsplit(url)
    sources = [url]
    if (parts.hostname or '').endswith('youtube.com') and is_profile(url) and parts.path.rstrip('/') != '/playlist':
        # A channel homepage includes uploads, Shorts and recorded public live streams.
        base = re.sub(r'/(videos|shorts|streams)/?$', '', url.split('?')[0]).rstrip('/')
        sources = [base + '/' + tab for tab in ('videos', 'shorts', 'streams')]
    logger=CatalogLogger();sources_read=[]
    opts = {'extract_flat': 'in_playlist', 'skip_download': True, 'noplaylist': False,
            'playlistend': limit or None, 'quiet': True, 'no_warnings': True,
            'socket_timeout': 15, 'retries': 2, 'extractor_retries': 2,
            'lazy_playlist': True,'logger':logger}
    opts.update(cookie_options or {})
    entries, seen, incomplete, title = [], set(), False, '主页视频'
    with yt_dlp.YoutubeDL(opts) as ydl:
        import platform_extractors
        platform_extractors.install(ydl)
        for source in sources:
            failed_before=logger.pagination_failed
            if limit and len(entries) >= limit:
                break
            try:
                info = ydl.extract_info(source, download=False)
                if not info:
                    raise ValueError('平台未返回视频列表')
                if not entries:
                    title = info.get('title') or title
                raw = info.get('entries')
                if raw is None:
                    raise ValueError('该网址未返回作者作品列表；不能把主页本身或推荐视频当作该作者作品')
                before=len(entries);exhausted=True
                for item in raw:
                    if cancelled and cancelled():raise Cancelled()
                    if limit and len(entries) >= limit:
                        exhausted=False
                        break
                    entry = normalize_entry(item, len(entries) + 1, source)
                    if entry and entry['url'] not in seen:
                        seen.add(entry['url'])
                        entries.append(entry)
                        if on_update and (len(entries) <= 3 or len(entries) % 20 == 0):
                            on_update(entries, title)
                sources_read.append({'source':source,'new_entries':len(entries)-before,'finished':exhausted})
            except Cancelled:
                raise
            except Exception as error:
                text = str(error).lower()
                empty_tab = 'tab' in text and any(t in text for t in ('does not have', 'has no', 'not found', 'no uploads'))
                if len(sources) > 1 and empty_tab:
                    logger.pagination_failed=failed_before
                    continue
                if not entries:
                    if _host(host,'tiktok.com'):
                        from profile_browser import resolve as browser_resolve
                        return browser_resolve(url,'tiktok',limit,cookie_options,on_update,cancelled)
                    if _host(host,'rumble.com'):
                        from profile_browser import resolve as browser_resolve
                        return browser_resolve(url,'rumble',limit,cookie_options,on_update,cancelled)
                    if host=='space.bilibili.com':
                        from profile_browser import resolve as browser_resolve
                        return browser_resolve(url,'bilibili',limit,cookie_options,on_update,cancelled)
                    raise
                incomplete = True
    if not entries:
        if host=='v.qq.com' or host=='space.bilibili.com':
            from profile_browser import resolve as browser_resolve
            return browser_resolve(url,'tencent' if host=='v.qq.com' else 'bilibili',limit,cookie_options,on_update,cancelled)
        raise ValueError('没有识别到可下载视频；此平台可能需要登录或暂不支持主页列表')
    limited=bool((limit and len(entries)>=limit) or incomplete or logger.pagination_failed)
    return {'title':title,'entries':entries,'limited':limited,'complete':not limited,
            'sources':sources_read,'completion_reason':'native-pagination-end' if not limited else 'unfinished-pagination',
            'note':'已遍历所有公开作品分页及频道视频、Shorts、直播回放入口' if not limited and len(sources)>1 else '分页尚未完整结束，可继续读取' if limited else ''}

def _cctv(url,limit,on_update=None,cancelled=None):
    """Use the actual column's public pagination and declared row count."""
    import json,time,file_download
    from urllib.parse import urlencode
    def read(source):
        conn,response,_=file_download.open_public(source,{'User-Agent':'Mozilla/5.0'})
        try:
            raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise ValueError('栏目列表响应过大')
            return raw.decode('utf-8','replace')
        finally:response.close();conn.close()
    page_html=read(url)
    match=re.search(r'\bvar\s+topicID\s*=\s*[\'\"](TOPC\d+)[\'\"]',page_html)
    if not match:raise ValueError('央视官方栏目未提供栏目编号，不能把推荐视频作为该栏目作品')
    title_match=re.search(r'<title>(.*?)</title>',page_html,re.S)
    title=re.sub('<[^>]+>','',title_match[1]).strip() if title_match else '央视栏目'
    entries=[];seen=set();page=1;rows_read=0;total=None;finished=False
    while True:
        if cancelled and cancelled():raise Cancelled()
        payload=read('https://api.cntv.cn/NewVideo/getVideoListByColumn?'+urlencode({'id':match[1],'n':20,'p':page,'serviceId':'tvcctv'}))
        json_match=re.search(r'\{.*\}',payload,re.S)
        if not json_match:raise ValueError('央视官方栏目分页未返回数据')
        data=json.loads(json_match[0]).get('data') or {}
        rows=data.get('list')
        if not isinstance(rows,list):raise ValueError('央视官方栏目分页结构变更')
        if data.get('total') is not None:total=int(data['total'])
        rows_read+=len(rows)
        for item in rows:
            link=item.get('url') or ''
            if urlsplit(link).hostname!='tv.cctv.com' or not re.fullmatch(r'/\d{4}/\d{2}/\d{2}/VIDE\w+\.shtml',urlsplit(link).path):continue
            if link in seen:continue
            seen.add(link);entry=normalize_entry({'url':link,'title':item.get('title'),'thumbnail':item.get('image')},len(entries)+1)
            if entry:entries.append(entry)
            if limit and len(entries)>=limit:break
        if on_update:on_update(entries,title)
        if not rows:
            finished=total is None or rows_read>=total
            break
        if total is not None and rows_read>=total:finished=True;break
        if limit and len(entries)>=limit:break
        page+=1;time.sleep(.4)
    return {'title':title,'entries':entries,'complete':finished,'limited':not finished,'pages':page,
            'official_rows':total,'rows_read':rows_read,'completion_reason':'official-end' if finished else 'sample-limit',
            'note':'已读取官方栏目全部公开分页并去重' if finished else '当前为抽样列表，可继续读取全部','method':'official-column-api'}

def _douyin(url, limit, cookie_options=None, on_update=None, cancelled=None):
    """Use the page's own signed post requests and public video links."""
    import douyin
    from playwright.sync_api import sync_playwright
    entries, seen = [], set()
    state = {'more':None,'responses':0,'cursor':None,'blocked':False}
    target=urlsplit(url).path.rstrip('/').split('/')[-1]
    def add(a):
        if not isinstance(a, dict) or not a.get('video') or (limit and len(entries) >= limit):
            return
        aid = str(a.get('aweme_id') or '')
        if not re.fullmatch(r'\d{15,25}', aid) or aid in seen:
            return
        seen.add(aid)
        video = a['video']
        covers = (video.get('cover') or {}).get('url_list') or []
        entries.append({'id': aid,
                        'url': 'https://www.douyin.com/video/' + aid,
                        'title': (a.get('desc') or aid)[:500],
                        'thumbnail': covers[0] if covers and covers[0].startswith('https://') else '',
                        'duration': float(video.get('duration') or 0) / 1000})
    def response(resp):
        if '/aweme/v1/web/aweme/post/' not in resp.url:
            return
        try:
            if parse_qs(urlsplit(resp.url).query).get('sec_user_id',[''])[0]!=target:return
            data = resp.json()
            if data.get('status_code') not in (None,0):state['blocked']=True;return
            state['responses'] += 1
            if 'has_more' in data:state['more']=bool(data['has_more'])
            state['cursor']=data.get('max_cursor')
            for a in data.get('aweme_list') or []:
                add(a)
            if on_update:
                on_update(entries, '抖音主页')
        except Exception:
            pass
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--mute-audio'])
        try:
            import video_platform_accounts as accounts
            saved=accounts.load('douyin') if 'douyin' in accounts.PROVIDERS else None
            opts={'locale':'zh-CN','viewport':{'width':1440,'height':900}}
            if saved:opts.update(storage_state={'cookies':accounts.live_cookies(saved),'origins':saved['origins']},user_agent=saved.get('ua') or douyin.UA)
            ctx = browser.new_context(**opts)
            cookie_path = (cookie_options or {}).get('cookiefile')
            if cookie_path:
                jar = http.cookiejar.MozillaCookieJar(cookie_path)
                try:
                    jar.load(ignore_discard=True, ignore_expires=False)
                    cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path or '/',
                                'secure': bool(c.secure), 'expires': c.expires or -1}
                               for c in jar if any(c.domain.lstrip('.')==h or c.domain.lstrip('.').endswith('.'+h) for h in ('douyin.com','iesdouyin.com'))]
                    if cookies:
                        ctx.add_cookies(cookies)
                except (OSError, http.cookiejar.LoadError):
                    pass
            page = ctx.new_page()
            page.on('response', response)
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            started=time.monotonic();deadline=started+3600;stale,old=0,0
            while time.monotonic() < deadline and (not limit or len(entries) < limit):
                if cancelled and cancelled():raise Cancelled()
                page.wait_for_timeout(1000)
                for a in page.locator('[data-e2e="user-post-list"] a[href*="/video/"]').evaluate_all(
                    "els=>els.map(e=>({href:e.href,title:e.innerText,img:e.querySelector('img')?.src}))"
                ):
                    m = re.search(r'/video/(\d{15,25})', a.get('href', ''))
                    if m and m[1] not in seen:
                        add({'aweme_id': m[1], 'desc': a.get('title') or m[1],
                             'video': {'cover': {'url_list': [a.get('img') or '']}}})
                if len(entries) == old:
                    stale += 1
                else:
                    stale, old = 0, len(entries)
                    if on_update:
                        on_update(entries, '抖音主页')
                if state['responses'] and state['more'] is False:
                    break
                if stale >= (60 if limit==0 else 15) and time.monotonic()-started>=40:break
                from profile_browser import advance_page
                advance_page(page)
            title = re.sub(r'\s*[-–]\s*抖音.*$', '', page.title()) or '抖音主页'
            if saved and accounts.page_authorized('douyin',page):
                fresh=ctx.storage_state();fresh['ua']=page.evaluate('navigator.userAgent');accounts.refresh('douyin',fresh,saved['_revision'])
        finally:
            browser.close()
    if not entries:
        raise ValueError('抖音主页未返回公开视频，可能需要登录或平台验证；单条视频下载仍可使用')
    limited=bool((limit and len(entries)>=limit) or state['more'] is not False)
    return {'title':title,'entries':entries,'limited':limited,'complete':not limited,'pages':state['responses'],
            'completion_reason':'official-end' if not limited else 'unfinished-pagination',
            'note':'平台分页未确认结束，保留已识别作品，可继续读取' if limited else ''}
