"""List public profile/playlist videos without downloading their media."""
from __future__ import annotations
import re
import hashlib
import http.cookiejar
import time
from urllib.parse import urlsplit
import yt_dlp

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
    if _host(host, 'douyin.com'):
        return path.startswith('/user/')
    if _host(host, 'tiktok.com'):
        return bool(re.fullmatch(r'/@[^/]+', path))
    if _host(host, 'youtube.com'):
        return bool(re.match(r'^/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)(?:/|$)', path)) or path == '/playlist'
    if host == 'space.bilibili.com':
        return True
    if _host(host, 'instagram.com'):
        return bool(re.fullmatch(r'/[^/]+(?:/reels)?', path))
    if _host(host,'xiaohongshu.com'):return bool(re.fullmatch(r'/user/profile/[a-f0-9]{24}',path))
    if _host(host,'kuaishou.com'):return bool(re.fullmatch(r'/profile/[A-Za-z0-9]+',path))
    if _host(host,'dailymotion.com'):return bool(re.fullmatch(r'/(?:user/|playlist/)?[^/]+',path)) and path not in ('/video','/embed','/search')
    if _host(host,'twitch.tv'):return bool(re.fullmatch(r'/[^/]+(?:/(?:videos|clips|collections))?',path)) and path not in ('/directory','/search')
    if _host(host,'weibo.com'):return bool(re.fullmatch(r'/u/\d+',path))
    if _host(host,'vimeo.com'):return bool(re.fullmatch(r'/(?:user\d+|[A-Za-z][\w-]*)(?:/videos)?',path) or re.match(r'^/(?:showcase|album|channels)/[^/]+$',path))
    if _host(host,'nicovideo.jp'):return bool(re.match(r'^/(?:user/\d+(?:/(?:video|mylist/\d+|series/\d+))?|mylist/\d+|series/\d+)$',path))
    if _host(host,'ted.com'):return path.startswith('/playlists/')
    if _host(host,'archive.org'):return bool(re.fullmatch(r'/details/[^/]+',path))
    if _host(host,'youku.com'):return path.startswith('/show/') or path.startswith('/u/')
    if _host(host,'iqiyi.com'):return path.startswith('/a_')
    if _host(host,'iq.com'):return path.startswith('/album/')
    if host=='v.qq.com':return bool(re.fullmatch(r'/x/cover/[\w]+\.html',path))
    if _host(host,'acfun.cn'):return bool(re.fullmatch(r'/u/\d+',path))
    if _host(host,'mgtv.com'):return bool(re.fullmatch(r'/h/\d+\.html',path))
    if _host(host,'ixigua.com'):return bool(re.fullmatch(r'/home/[^/]+',path))
    if _host(host,'toutiao.com'):return bool(re.fullmatch(r'/c/user/(?:token/)?[^/]+',path))
    if host=='haokan.baidu.com':return path.startswith('/author/')
    if _host(host,'facebook.com'):return bool(re.fullmatch(r'/[^/]+/(?:videos|reels)',path))
    if _host(host,'x.com') or _host(host,'twitter.com'):return bool(re.fullmatch(r'/[^/]+(?:/media)?',path))
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

def resolve(url, limit=0, cookie_options=None, on_update=None):
    if not platform_url(url):
        raise ValueError('请提供视频平台的主页或合集链接')
    parts = urlsplit(url)
    host=parts.hostname or ''
    if _host(host,'xiaohongshu.com') or _host(host,'kuaishou.com') or _host(host,'acfun.cn'):
        from profile_browser import resolve as browser_resolve
        platform='xiaohongshu' if _host(host,'xiaohongshu.com') else 'kuaishou' if _host(host,'kuaishou.com') else 'acfun'
        return browser_resolve(url,platform,limit,cookie_options,on_update)
    special = 'haokan' if host=='haokan.baidu.com' else 'mgtv' if _host(host,'mgtv.com') else 'cctv' if _host(host,'cctv.com') else 'iqiyi' if _host(host,'iqiyi.com') and parts.path.startswith('/a_') else 'sohu' if host=='my.tv.sohu.com' and parts.path.startswith(('/us/','/pl/')) else None
    if special:
        from profile_browser import resolve as browser_resolve
        return browser_resolve(url,special,limit,cookie_options,on_update)
    if _host(host,'douyin.com'):
        return _douyin(url, limit, cookie_options, on_update)
    if _host(host,'instagram.com'):
        url=re.sub(r'/reels/?$', '/', url.split('?')[0]);parts=urlsplit(url)
    sources = [url]
    if (parts.hostname or '').endswith('youtube.com') and is_profile(url) and parts.path.rstrip('/') != '/playlist':
        # A channel homepage includes uploads, Shorts and recorded public live streams.
        base = re.sub(r'/(videos|shorts|streams)/?$', '', url.split('?')[0]).rstrip('/')
        sources = [base + '/' + tab for tab in ('videos', 'shorts', 'streams')]
    opts = {'extract_flat': 'in_playlist', 'skip_download': True, 'noplaylist': False,
            'playlistend': limit or None, 'quiet': True, 'no_warnings': True,
            'socket_timeout': 15, 'retries': 2, 'extractor_retries': 2,
            'lazy_playlist': True}
    opts.update(cookie_options or {})
    entries, seen, incomplete, title = [], set(), False, '主页视频'
    with yt_dlp.YoutubeDL(opts) as ydl:
        import platform_extractors
        platform_extractors.install(ydl)
        for source in sources:
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
                for item in raw:
                    if limit and len(entries) >= limit:
                        break
                    entry = normalize_entry(item, len(entries) + 1, source)
                    if entry and entry['url'] not in seen:
                        seen.add(entry['url'])
                        entries.append(entry)
                        if on_update and (len(entries) <= 3 or len(entries) % 20 == 0):
                            on_update(entries, title)
            except Exception as error:
                text = str(error).lower()
                empty_tab = 'tab' in text and any(t in text for t in ('does not have', 'has no', 'not found', 'no uploads'))
                if len(sources) > 1 and empty_tab:
                    continue
                if not entries:
                    if _host(host,'tiktok.com'):
                        from profile_browser import resolve as browser_resolve
                        return browser_resolve(url,'tiktok',limit,cookie_options,on_update)
                    if host=='space.bilibili.com':
                        from profile_browser import resolve as browser_resolve
                        return browser_resolve(url,'bilibili',limit,cookie_options,on_update)
                    raise
                incomplete = True
    if not entries:
        if host=='v.qq.com' or host=='space.bilibili.com':
            from profile_browser import resolve as browser_resolve
            return browser_resolve(url,'tencent' if host=='v.qq.com' else 'bilibili',limit,cookie_options,on_update)
        raise ValueError('没有识别到可下载视频；此平台可能需要登录或暂不支持主页列表')
    return {'title': title, 'entries': entries,
            'limited': bool((limit and len(entries) >= limit) or incomplete)}

def _douyin(url, limit, cookie_options=None, on_update=None):
    """Use the page's own signed post requests and public video links."""
    import douyin
    from playwright.sync_api import sync_playwright
    entries, seen = [], set()
    state = {'more': None, 'responses': 0}
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
            data = resp.json()
            state['responses'] += 1
            state['more'] = bool(data.get('has_more'))
            for a in data.get('aweme_list') or []:
                add(a)
            if on_update:
                on_update(entries, '抖音主页')
        except Exception:
            pass
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled', '--mute-audio'])
        try:
            ctx = browser.new_context(user_agent=douyin.UA, locale='zh-CN', viewport={'width': 1440, 'height': 900})
            cookie_path = (cookie_options or {}).get('cookiefile')
            if cookie_path:
                jar = http.cookiejar.MozillaCookieJar(cookie_path)
                try:
                    jar.load(ignore_discard=True, ignore_expires=False)
                    cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path or '/',
                                'secure': bool(c.secure), 'expires': c.expires or -1}
                               for c in jar if 'douyin.com' in c.domain or 'iesdouyin.com' in c.domain]
                    if cookies:
                        ctx.add_cookies(cookies)
                except (OSError, http.cookiejar.LoadError):
                    pass
            page = ctx.new_page()
            page.on('response', response)
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            deadline, stale, old = time.monotonic() + 60, 0, 0
            while time.monotonic() < deadline and (not limit or len(entries) < limit):
                page.wait_for_timeout(1000)
                for a in page.locator('a[href*="/video/"]').evaluate_all(
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
                    deadline = time.monotonic() + 60
                    if on_update:
                        on_update(entries, '抖音主页')
                if entries and (stale >= 12 or (state['responses'] and state['more'] is False)):
                    break
                page.mouse.wheel(0, 2200)
            title = re.sub(r'\s*[-–]\s*抖音.*$', '', page.title()) or '抖音主页'
        finally:
            browser.close()
    if not entries:
        raise ValueError('抖音主页未返回公开视频，可能需要登录或平台验证；单条视频下载仍可使用')
    return {'title': title, 'entries': entries, 'limited': bool((limit and len(entries) >= limit) or state['more'] is not False)}
