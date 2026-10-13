"""TED schema compatibility; upstream yt-dlp PR #17510 (Unlicense)."""
import re
from yt_dlp.extractor.ted import TedTalkIE as OriginalTedTalkIE
from yt_dlp.utils import int_or_none,parse_duration,str_to_int,try_get,unified_strdate,url_or_none
class TedTalkIE(OriginalTedTalkIE):
    def _real_extract(self, url):
        display_id = self._match_id(url)
        webpage = self._download_webpage(url, display_id)
        talk_info = self._search_nextjs_data(webpage, display_id)['props']['pageProps']['videoData']
        video_id = talk_info['id']
        player_data = talk_info.get('videoPlayerData') or {}

        http_url = None
        formats, subtitles = [], {}
        for format_id, resources in (player_data.get('resources') or {}).items():
            if format_id == 'hls':
                stream_url = url_or_none(try_get(resources, lambda x: x['stream']))
                if not stream_url:
                    continue
                m3u8_formats, m3u8_subs = self._extract_m3u8_formats_and_subtitles(
                    stream_url, video_id, 'mp4', m3u8_id=format_id, fatal=False)
                formats.extend(m3u8_formats)
                subtitles = self._merge_subtitles(subtitles, m3u8_subs)
                continue

            if not isinstance(resources, list):
                continue
            if format_id == 'h264':
                for resource in resources:
                    h264_url = resource.get('file')
                    if not h264_url:
                        continue
                    bitrate = int_or_none(resource.get('bitrate'))
                    formats.append({
                        'url': h264_url,
                        'format_id': f'{format_id}-{bitrate}k',
                        'tbr': bitrate,
                    })
                    if re.search(r'\d+k', h264_url):
                        http_url = h264_url
            elif format_id == 'rtmp':
                streamer = talk_info.get('streamer')
                if not streamer:
                    continue
                formats.extend({
                    'format_id': '{}-{}'.format(format_id, resource.get('name')),
                    'url': streamer,
                    'play_path': resource['file'],
                    'ext': 'flv',
                    'width': int_or_none(resource.get('width')),
                    'height': int_or_none(resource.get('height')),
                    'tbr': int_or_none(resource.get('bitrate')),
                } for resource in resources if resource.get('file'))

        if http_url:
            m3u8_formats = [f for f in formats if f.get('protocol') == 'm3u8' and f.get('vcodec') != 'none']
            for m3u8_format in m3u8_formats:
                bitrate = self._search_regex(r'(\d+k)', m3u8_format['url'], 'bitrate', default=None)
                if not bitrate:
                    continue
                bitrate_url = re.sub(r'\d+k', bitrate, http_url)
                if not self._is_valid_url(
                        bitrate_url, video_id, f'{bitrate} bitrate'):
                    continue
                f = m3u8_format.copy()
                f.update({
                    'url': bitrate_url,
                    'format_id': m3u8_format['format_id'].replace('hls', 'http'),
                    'protocol': 'http',
                })
                if f.get('acodec') == 'none':
                    del f['acodec']
                formats.append(f)

        audio_download = talk_info.get('audioDownload')
        if audio_download:
            formats.append({
                'url': audio_download,
                'format_id': 'audio',
                'vcodec': 'none',
            })

        if not formats:
            external = player_data.get('external') or {}
            service = external.get('service') or ''
            ext_url = external.get('code') if service.lower() == 'youtube' else None
            return self.url_result(ext_url or external['uri'])

        thumbnail = player_data.get('thumb') or self._og_search_property('image', webpage)
        if thumbnail:
            # trim thumbnail resize parameters
            thumbnail = thumbnail.split('?')[0]

        return {
            'id': video_id,
            'title': talk_info.get('title') or self._og_search_title(webpage),
            'uploader': talk_info.get('presenterDisplayName'),
            'thumbnail': thumbnail,
            'description': talk_info.get('description') or self._og_search_description(webpage),
            'subtitles': subtitles,
            'formats': formats,
            'duration': talk_info.get('duration') or parse_duration(self._og_search_property('video:duration', webpage)),
            'view_count': str_to_int(talk_info.get('viewedCount')),
            'upload_date': unified_strdate(talk_info.get('publishedAt')),
            'release_date': unified_strdate(talk_info.get('recordedOn')),
            'tags': try_get(player_data, lambda x: x['targeting']['tag'].split(',')),
        }


def install(ydl):
    # Credentials never leave the downloader; only official platform domains receive them.
    import video_platform_accounts as accounts
    if hasattr(ydl, 'cookiejar'):
        from http.cookiejar import Cookie
        for provider in accounts.PROVIDERS:
            try: state = accounts.load(provider)
            except (OSError, ValueError, KeyError, TypeError): continue
            if not state: continue
            for c in accounts.live_cookies(state):
                domain = c['domain']; expires = c.get('expires', -1)
                ydl.cookiejar.set_cookie(Cookie(0, c['name'], c['value'], None, False,
                    domain, domain.startswith('.'), domain.startswith('.'), c.get('path', '/'), True,
                    bool(c.get('secure')), int(expires) if expires > 0 else None, expires <= 0,
                    None, None, {'HttpOnly': c.get('httpOnly', False)}, False))
    from focused_extractors import BiliBiliIE, HaokanIE, IqiyiIE, IxiguaIE, KuaishouIE, XiaoHongShuIE
    additions = [BiliBiliIE(ydl), HaokanIE(ydl), IqiyiIE(ydl), IxiguaIE(ydl), KuaishouIE(ydl), XiaoHongShuIE(ydl), TedTalkIE(ydl)]
    for extractor in additions:
        ydl.add_info_extractor(extractor)
    # New site extractors must precede Generic, which accepts every URL.
    if hasattr(ydl, "_ies"):
        keys = [ie.ie_key() for ie in additions]
        ydl._ies = {**{key: ydl._ies[key] for key in keys}, **ydl._ies}
