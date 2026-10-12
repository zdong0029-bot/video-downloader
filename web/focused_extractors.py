"""Current official player data for public, unencrypted videos.

Browsers run in isolated contexts; no CAPTCHA, login, or DRM bypass is used.
"""
import base64
import ipaddress
import json
import re
import socket
import threading
import time
from urllib.parse import urlsplit, parse_qs, parse_qsl, urlencode, urlunsplit

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.extractor.iqiyi import IqiyiIE as OriginalIqiyiIE
from yt_dlp.extractor.ixigua import IxiguaIE as OriginalIxiguaIE
from yt_dlp.extractor.xiaohongshu import XiaoHongShuIE as OriginalXiaoHongShuIE
from yt_dlp.utils import js_to_json
from yt_dlp.utils import ExtractorError, int_or_none

BROWSERS = threading.BoundedSemaphore(2)
FAMILIES = {
    'xiaohongshu': ('xiaohongshu.com', 'xhscdn.com', 'xhslink.com', 'xhslink.cn'),
    'xigua': ('ixigua.com', 'ixiguavideo.com', 'snssdk.com', 'toutiao.com', 'toutiaostatic.com', 'byteimg.com', 'bytedance.com', 'bytedance.net', 'bytedcdn.com', 'douyin.com', 'douyinpic.com', 'douyinvod.com'),
    'haokan': ('baidu.com', 'bdstatic.com', 'bcebos.com'),
    'iqiyi': ('iqiyi.com', 'qiyi.com', 'qy.net', 'iqiyipic.com', 'iqiyinc.com', '71edge.com'),
    'kuaishou': ('kuaishou.com', 'kwai.com', 'kwaicdn.com', 'ndcimgs.com', 'kwimgs.com', 'yximgs.com', 'yximg.com', 'wsbkwai.com', 'wskwai.com', 'ksyuncdn.com'),
}

def official_url(url, families):
    try:
        p = urlsplit(url)
        return p.scheme in ('http', 'https') and not p.username and not p.password and p.port in (None, 80, 443) and any(p.hostname == h or (p.hostname or '').endswith('.' + h) for h in families)
    except ValueError:
        return False

def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0

def browser_snapshot(url, platform, cookiejar=None):
    """Let the normal official player publish its own metadata, bounded to 40s."""
    from playwright.sync_api import sync_playwright
    if not official_url(url, FAMILIES[platform]):
        raise ExtractorError('只接受该平台的官方网页地址', expected=True)
    with BROWSERS, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            import video_platform_accounts as accounts
            saved = accounts.load(platform) if platform in accounts.PROVIDERS else None
            options = {'storage_state': {'cookies': accounts.live_cookies(saved), 'origins': saved['origins']}} if saved else {}
            if saved and saved.get('ua'):
                options['user_agent'] = saved['ua']
            context = browser.new_context(**options)
            # Only the already authorized downloader cookies for this platform.
            cookies = []
            for c in cookiejar or []:
                if c.expires is not None and c.expires <= time.time(): continue
                if any(c.domain.lstrip('.') == h or c.domain.lstrip('.').endswith('.' + h) for h in FAMILIES[platform]):
                    item = {'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path or '/', 'secure': bool(c.secure)}
                    if c.expires and c.expires > time.time():
                        item['expires'] = c.expires
                    cookies.append(item)
            if cookies:
                context.add_cookies(cookies)
            checked = {}
            def route(request_route):
                address = request_route.request.url
                if not official_url(address, FAMILIES[platform]):
                    request_route.abort(); return
                host = urlsplit(address).hostname
                if host not in checked:
                    try:
                        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                        checked[host] = bool(records) and all(ipaddress.ip_address(x[4][0]).is_global for x in records)
                    except OSError:
                        checked[host] = False
                if checked[host]: request_route.continue_()
                else: request_route.abort()
            context.route('**/*', route)
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=35000)
            deadline = time.monotonic() + 12
            while True:
                if platform == 'haokan':
                    data = page.evaluate('()=>window.__PRELOADED_STATE__?.curVideoMeta || null')
                elif platform == 'iqiyi':
                    data = page.evaluate('()=>{const p=window.QiyiPlayerProphetData;return p?.dashData?{tvid:p.tvid,videoInfo:p.videoInfo,dashData:p.dashData,ua:navigator.userAgent}:null}')
                elif platform == 'xigua':
                    html = page.content()
                    data = {'html': html} if 'SSR_HYDRATED_DATA' in html or re.search(r'window\._SSR_DATA\s*=', html) else None
                elif platform == 'xiaohongshu':
                    # Serialize SSR HTML, not Vue's circular reactive objects.
                    note_id = re.search(r'/(?:explore|discovery/item)/([a-f0-9]{24})', urlsplit(page.url).path)
                    exact = page.evaluate("id=>!!window.__INITIAL_STATE__?.note?.noteDetailMap?.[id]?.note", note_id.group(1) if note_id else '')
                    data = {'html': page.content(), 'url': page.url} if exact else None
                else:
                    data = page.evaluate('()=>window.__APOLLO_STATE__ || null')
                    if data and not any('VisionVideoDetailPhoto:' in k for k in (data.get('defaultClient') or {})):
                        data = None
                body = page.locator('body').inner_text(timeout=3000)[:4000]
                if any(marker in body for marker in ('安全验证', '完成验证', 'IP存在风险', 'IP 存在风险')):
                    raise ExtractorError('平台要求在官方网页完成人工安全验证，未获取视频授权', expected=True)
                if data:
                    if saved:
                        # Reuse the existing official browser login flow. No fabricated expiry.
                        updated = context.storage_state(); updated['ua'] = page.evaluate('navigator.userAgent')
                        accounts.refresh(platform, updated, saved['_revision'])
                    return data
                if time.monotonic() >= deadline:
                    raise ExtractorError('官方网页未提供该视频的播放数据；可能需要登录、验证或链接已失效', expected=True)
                page.wait_for_timeout(800)
        finally:
            browser.close()

def haokan_formats(meta, video_id):
    if str(meta.get('id')) != video_id:
        raise ExtractorError('播放数据与提交的视频不一致，已停止', expected=True)
    if meta.get('is_pay_column') or meta.get('video_status') not in (0, None):
        raise ExtractorError('该视频需要官方访问授权或已经失效', expected=True)
    formats = []
    for item in meta.get('clarityUrl') or []:
        source = item.get('url')
        if not official_url(source or '', FAMILIES['haokan']):
            continue
        dims = str(item.get('vodVideoHW') or '').split('$$')
        height, width = (int_or_none(dims[0]), int_or_none(dims[1])) if len(dims) == 2 else (None, None)
        formats.append({'url': source, 'format_id': str(item.get('key') or len(formats)), 'height': height, 'width': width, 'tbr': number(item.get('videoBps')), 'ext': 'mp4'})
    if not formats and official_url(meta.get('playurl') or '', FAMILIES['haokan']):
        formats.append({'url': meta['playurl'], 'format_id': 'official', 'ext': 'mp4'})
    if not formats:
        raise ExtractorError('平台未提供完整视频的下载地址', expected=True)
    # Deliberately ignore 6-second previewUrlHttp and 6s_play_url.
    return formats

class IxiguaIE(OriginalIxiguaIE):
    _VALID_URL = r'https?://(?:\w+\.)?ixigua\.com/(?:video/)?(?P<id>\d+)(?:[/?#]|$)'
    def _download_webpage(self, url, *args, **kwargs):
        html = super()._download_webpage(url, *args, **kwargs)
        if 'SSR_HYDRATED_DATA' not in (html or '') and not re.search(r'window\._SSR_DATA\s*=', html or ''):
            html = browser_snapshot(url, 'xigua', self._downloader.cookiejar)['html']
        return html
    def _get_json_data(self, webpage, video_id):
        # Current mobile pages contain descriptive data, but may require the App
        # for actual playback. A definition label is not a downloadable stream.
        match = re.search(r'window\._SSR_DATA\s*=\s*', webpage or '')
        if match and 'SSR_HYDRATED_DATA' not in webpage:
            try:
                state, _ = json.JSONDecoder().raw_decode(webpage[match.end():])
                detail = state['data']['storeState']['detail']['videoData']
                video = detail.get('result') or {}
            except (ValueError, TypeError, KeyError):
                raise ExtractorError('西瓜新版网页播放信息无效，尚未取得视频文件', expected=True)
            if str(video.get('gid')) != video_id:
                raise ExtractorError('西瓜新版网页信息与提交的视频不一致，已停止', expected=True)
            if detail.get('error'):
                raise ExtractorError('西瓜官方网页未授权这条视频的播放，尚未取得视频文件', expected=True)
            raise ExtractorError('西瓜新版网页只返回作品信息，未提供可验证的完整视频清晰度与播放流；当前页面要求打开官方App。此链接尚未下载，不计通过', expected=True)
        return super()._get_json_data(webpage, video_id)

class HaokanIE(InfoExtractor):
    _VALID_URL = r'https?://haokan\.baidu\.com/v\?(?:[^#]*?&)?vid=(?P<id>\d+)'
    def _real_extract(self, url):
        video_id = self._match_id(url)
        meta = browser_snapshot(url, 'haokan', self._downloader.cookiejar)
        return {'id': video_id, 'title': meta.get('title') or video_id, 'duration': number(meta.get('duration')), 'thumbnail': meta.get('poster'), 'formats': haokan_formats(meta, video_id), 'http_headers': {'Referer': url}}

def public_iqiyi_rows(snapshot):
    info = snapshot.get('videoInfo') or {}
    if info.get('payMark') or info.get('isLock') or info.get('downloadAllowed') is False:
        raise ExtractorError('该视频需要官方登录、会员或版权授权，普通下载未启用', expected=True)
    data = (snapshot.get('dashData') or {}).get('data') or {}
    rows = []
    for row in (data.get('program') or {}).get('video') or []:
        if row.get('isPreview') or row.get('is_preview'):
            continue
        # ml is the current player's already provided full HLS URL.
        source = row.get('ml') or row.get('m3u8Url')
        if source and official_url(source, FAMILIES['iqiyi']):
            rows.append(row)
    if not rows:
        raise ExtractorError('官方播放器没有提供完整的普通视频流；不下载会员试看或 DRM 内容', expected=True)
    return rows

class IqiyiIE(OriginalIqiyiIE):
    def _real_extract(self, url):
        snapshot = browser_snapshot(url, 'iqiyi', self._downloader.cookiejar)
        info = snapshot.get('videoInfo') or {}; video_id = str(snapshot.get('tvid') or info.get('tvId'))
        rows = public_iqiyi_rows(snapshot)
        headers = {'Referer': url, 'User-Agent': snapshot.get('ua') or self._downloader.params.get('http_headers', {}).get('User-Agent', '')}
        # Only ordinary clear HLS streams. No keys are taken from the player.
        formats = []
        for row in rows:
            source = row.get('ml') or row.get('m3u8Url')
            manifest = self._download_webpage(source, video_id, headers=headers)
            if not manifest.startswith('#EXTM3U') or re.search(r'#EXT-X-KEY:.*METHOD=(?!NONE)', manifest):
                continue
            lines = manifest.splitlines()
            segments = [line.strip() for line in lines if line.strip() and not line.startswith('#')]
            if not segments or len(segments) > 10000 or any(not official_url(u, FAMILIES['iqiyi']) for u in segments):
                continue
            def dispatch(segment):
                if urlsplit(segment).hostname != 'data.video.iqiyi.com':
                    return segment
                parsed = urlsplit(segment)
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                # ms is delivered by this exact official player response.
                # Empty qd_uid / qd_k are significant; never discard them.
                for key, value in parse_qsl(row.get('ms') or '', keep_blank_values=True):
                    query.setdefault(key, value)
                segment = urlunsplit(parsed._replace(query=urlencode(query)))
                # Without the player's JSON-dispatch mode, this official
                # endpoint serves the same clear TS fragment directly.
                return segment
            resolved = {segment: dispatch(segment) for segment in segments}
            normalized = '\n'.join(resolved.get(line.strip(), line) for line in lines)
            inline = 'data:application/vnd.apple.mpegurl;base64,' + base64.b64encode(normalized.encode()).decode()
            bid = int_or_none(row.get('bid')) or 0
            height = {100: 210, 200: 360, 300: 540, 500: 720, 600: 1080}.get(bid)
            formats.append({'url': inline, 'protocol': 'm3u8_native', 'format_id': str(bid), 'ext': 'mp4', 'height': height, 'fps': number(row.get('fr')), 'tbr': number(row.get('vsize')) * 8 / max(number(row.get('duration')), 1) / 1000, 'http_headers': headers})
        if not formats:
            raise ExtractorError('播放器只提供加密或受限内容，未获取普通视频流', expected=True)
        return {'id': video_id, 'title': info.get('title') or video_id, 'formats': formats, 'duration': max(number(v.get('duration')) for v in rows), 'thumbnail': info.get('imageUrl')}

def kuaishou_formats(photo, state, video_id):
    if str(photo.get('id')) != video_id:
        raise ExtractorError('快手播放信息与视频 ID 不一致', expected=True)
    def ref(value):
        for _ in range(10):
            if isinstance(value, dict) and value.get('type') == 'id': value = state.get(value.get('id'))
            elif isinstance(value, dict) and 'json' in value: value = value['json']
            else: break
        return value or {}
    formats = []
    for codec, manifest in [('h264', photo.get('manifest')), ('h265', photo.get('manifestH265'))]:
        manifest = ref(manifest)
        if not isinstance(manifest, dict): continue
        for adaptation in manifest.get('adaptationSet') or []:
            adaptation = ref(adaptation)
            for rep in adaptation.get('representation') or []:
                rep = ref(rep); sources = ref(rep.get('url'))
                if isinstance(sources, str): sources = [sources]
                elif not isinstance(sources, list): sources = []
                for source in sources:
                    if official_url(source, FAMILIES['kuaishou']):
                        formats.append({'url': source, 'format_id': codec + '-' + str(rep.get('id') or len(formats)), 'width': int_or_none(rep.get('width')), 'height': int_or_none(rep.get('height')), 'fps': number(rep.get('frameRate')), 'tbr': number(rep.get('avgBitrate')), 'ext': 'mp4'})
    if not formats:
        for field in ('photoUrl', 'photoH265Url'):
            if official_url(photo.get(field) or '', FAMILIES['kuaishou']): formats.append({'url': photo[field], 'format_id': field, 'ext': 'mp4'})
    return formats

class KuaishouIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?kuaishou\.com/short-video/(?P<id>[A-Za-z0-9]+)'
    def _real_extract(self, url):
        video_id = self._match_id(url)
        data = browser_snapshot(url, 'kuaishou', self._downloader.cookiejar)
        state = data.get('defaultClient') or {}; photo = state.get('VisionVideoDetailPhoto:' + video_id) or {}
        formats = kuaishou_formats(photo, state, video_id)
        if not formats: raise ExtractorError('快手未提供视频流，请在官方网页确认登录和安全验证', expected=True)
        return {'id': video_id, 'title': photo.get('caption') or video_id, 'duration': number(photo.get('duration')) / 1000, 'formats': formats, 'http_headers': {'Referer': 'https://www.kuaishou.com/'}}

def xhs_note(page, note_id):
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.*?)</script>', page, re.S)
    if not match: return None
    try:
        state = json.loads(js_to_json(match[1]))
    except (ValueError, TypeError): return None
    return (((state.get('note') or {}).get('noteDetailMap') or {}).get(note_id) or {}).get('note')

def xhs_media_formats(note):
    """New official mediaV2 separates HEVC picture and AAC sound."""
    try:
        media = json.loads(note.get('video', {}).get('mediaV2') or '{}')
    except (ValueError, TypeError):
        return []
    if not isinstance(media, dict): return []
    if int_or_none((media.get('video') or {}).get('drm_type')):
        raise ExtractorError('小红书视频需要官方版权授权，未启用普通下载', expected=True)
    formats = []
    for audio, streams in ((False, media.get('stream') or {}), (True, media.get('audio_stream') or {})):
        for codec, rows in streams.items():
            for index, row in enumerate(rows):
                if not isinstance(row, dict): continue
                for backup, source in enumerate([row.get('master_url')] + (row.get('backup_urls') or [])):
                    if not official_url(source or '', ('xhscdn.com', 'xiaohongshu.com')): continue
                    info = {'url': source, 'format_id': f'{codec}-{index}-{backup}', 'ext': 'm4a' if audio else 'mp4',
                            'filesize': int_or_none(row.get('size')), 'source_preference': -backup,
                            'vcodec': 'none' if audio else {'EF4': 'h264', 'EF5': 'hevc'}.get(codec, row.get('video_codec')),
                            'acodec': row.get('audio_codec') or 'none', 'tbr': number(row.get('avg_bitrate')) / 1000,
                            'duration': number(row.get('audio_duration') if audio else row.get('duration')) / 1000}
                    if not audio:
                        info.update(width=int_or_none(row.get('width')), height=int_or_none(row.get('height')), fps=number(row.get('fps')))
                    else:
                        info.update(abr=number(row.get('audio_bitrate')) / 1000, asr=int_or_none(row.get('audio_samplerate')))
                    formats.append(info)
    return formats

class XiaoHongShuIE(OriginalXiaoHongShuIE):
    def _download_webpage(self, *args, **kwargs):
        page = super()._download_webpage(*args, **kwargs)
        url = args[0] if args else kwargs.get('url_or_request', '')
        if isinstance(url, str) and official_url(url, FAMILIES['xiaohongshu']) and '/login' not in urlsplit(url).path:
            expected_id = re.search(r'/(?:explore|discovery/item)/([a-f0-9]{24})', urlsplit(url).path)
            # A requests-only page may be a login redirect even with valid saved auth.
            if expected_id and not xhs_note(page or '', expected_id.group(1)):
                page = browser_snapshot(url, 'xiaohongshu', self._downloader.cookiejar)['html']
        if page and 'window.__INITIAL_STATE__' in page:
            self._note_page = page
        return page
    def _real_extract(self, url):
        self._note_page = None
        result = super()._real_extract(url)
        if self._note_page:
            state = self._search_json(r'window\.__INITIAL_STATE__\s*=', self._note_page, 'initial state', result['id'], transform_source=js_to_json)
            note = ((state.get('note') or {}).get('noteDetailMap', {}).get(result['id']) or {}).get('note') or {}
            formats = xhs_media_formats(note)
            if formats:
                originals = [f for f in result.get('formats', []) if f.get('format_id') == 'direct']
                result['formats'] = formats + originals
        result['http_headers'] = {'Referer': 'https://www.xiaohongshu.com/'}
        return result
