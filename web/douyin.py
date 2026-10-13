"""抖音解析器。

为什么不能用 yt-dlp：
    抖音给每个接口请求加了 a_bogus 签名，由它自己的 JS 在浏览器里实时计算。
    没有签名一律 403。yt-dlp 报的 "Fresh cookies are needed" 是误导性错误——
    给再新鲜的 cookie 也没用，缺的是签名不是 cookie。
    同理，f2 已停更，TikTokDownloader 明确声明不再维护签名算法。

这里的做法：
    用真实浏览器（Playwright + Chromium）打开视频页，让抖音自己的 JS 去算签名、
    发请求，我们只在旁边拦截它的详情接口响应，从返回的 JSON 里取无水印地址。
    好处是抖音改签名算法也不影响——浏览器永远算得对。

拿到的是 play_addr（播放地址，无水印），不是 download_addr（带水印的下载按钮用的）。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qs

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# 详情接口。抖音偶尔改路径，所以匹配得宽一些
DETAIL_RE = re.compile(r"/aweme/v1/web/aweme/(detail|post)/|/aweme/v1/web/general/search")

AWEME_ID_RE = re.compile(r"/video/(\d{15,25})|/note/(\d{15,25})")


class DouyinError(Exception):
    pass


@dataclass
class DouyinVideo:
    aweme_id: str
    title: str
    url: str                  # 无水印播放地址
    cover: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0


def is_douyin(url: str) -> bool:
    return bool(re.search(r"(^|\.)(douyin|iesdouyin)\.com", url or ""))


def _pick_video(aweme: dict) -> tuple[str, int, int, float]:
    """从一条作品数据里挑出最佳无水印地址。"""
    video = aweme.get("video") or {}

    # 先取最高分辨率，同分辨率再比较帧率和码率。下载保持源编码，不转码。
    best_url, best_w, best_h, best_rank = "", 0, 0, (-1, -1, -1)
    for br in (video.get("bit_rate") or []):
        pa = br.get("play_addr") or {}
        urls = pa.get("url_list") or []
        if not urls:
            continue
        rate = int(br.get("bit_rate") or 0)
        width = int(pa.get("width") or video.get("width") or 0)
        height = int(pa.get("height") or video.get("height") or 0)
        rank = (min(width, height), float(br.get("FPS") or br.get("fps") or 0), rate)
        if rank > best_rank:
            best_rank = rank
            best_url, best_w, best_h = urls[0], width, height

    if not best_url:
        pa = video.get("play_addr") or {}
        urls = pa.get("url_list") or []
        if urls:
            best_url = urls[0]
            best_w = int(video.get("width") or 0)
            best_h = int(video.get("height") or 0)

    dur = float(video.get("duration") or 0) / 1000.0
    return best_url, best_w, best_h, dur


def _scan(payload: dict, expected_id: str | None = None) -> dict | None:
    """在接口返回里找到作品对象。不同接口的包装层不一样，都扫一遍。"""
    for key in ("aweme_detail", "aweme_info"):
        if isinstance(payload.get(key), dict) and (not expected_id or str(payload[key].get('aweme_id')) == expected_id):
            return payload[key]
    for key in ("aweme_list", "data", "item_list"):
        v = payload.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and (not expected_id or str(item.get('aweme_id')) == expected_id):
                    return item
    return None


def resolve(url: str, timeout: float = 45.0, headless: bool = True) -> DouyinVideo:
    """打开抖音页面，拦截详情接口，返回无水印地址。"""
    captured: dict = {}

    def on_response(resp):
        if captured or not DETAIL_RE.search(resp.url):
            return
        try:
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            data = resp.json()
        except Exception:  # noqa: BLE001  响应体读不到就跳过，不影响其它请求
            return
        match = AWEME_ID_RE.search(url) or AWEME_ID_RE.search(page.url)
        target = (match.group(1) or match.group(2)) if match else parse_qs(urlsplit(page.url).query).get('modal_id', [''])[0]
        if not target:return
        aweme = _scan(data, target) if isinstance(data, dict) else None
        if aweme and (aweme.get("video") or {}).get("play_addr"):
            captured["aweme"] = aweme

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--mute-audio"],
        )
        try:
            import video_platform_accounts as accounts
            saved = accounts.load('douyin')
            options = {'viewport': {'width': 1440, 'height': 900}, 'locale': 'zh-CN'}
            if saved:
                options['storage_state'] = {'cookies': accounts.live_cookies(saved), 'origins': saved['origins']}
                if saved.get('ua'):options['user_agent'] = saved['ua']
            ctx = browser.new_context(**options)
            page = ctx.new_page()
            page.on("response", on_response)

            page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))

            deadline = time.time() + timeout
            while not captured and time.time() < deadline:
                page.wait_for_timeout(400)

            final_url = page.url
            page_title = page.title()
            if saved and accounts.page_authorized('douyin', page):
                fresh = ctx.storage_state();fresh['ua'] = page.evaluate('navigator.userAgent')
                accounts.refresh('douyin', fresh, saved['_revision'])
        finally:
            browser.close()

    if not captured:
        raise DouyinError(
            "没能从页面拿到视频数据。可能是链接失效、需要登录，或抖音改了接口。"
        )

    aweme = captured["aweme"]
    play_url, w, h, dur = _pick_video(aweme)
    if not play_url:
        raise DouyinError("解析到作品但没有视频地址（可能是图集而非视频）")

    aid = str(aweme.get("aweme_id") or "")
    if not aid:
        m = AWEME_ID_RE.search(final_url)
        aid = (m.group(1) or m.group(2)) if m else str(int(time.time()))

    title = (aweme.get("desc") or "").strip()
    if not title:
        title = re.sub(r"\s*-\s*抖音\s*$", "", page_title or "").strip() or aid

    cover = ""
    cov = (aweme.get("video") or {}).get("cover") or {}
    if cov.get("url_list"):
        cover = cov["url_list"][0]

    return DouyinVideo(aweme_id=aid, title=title, url=play_url,
                       cover=cover, duration=dur, width=w, height=h)


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(video: DouyinVideo) -> str:
    name = _ILLEGAL.sub("_", video.title).strip()[:80] or video.aweme_id
    return f"{name} [{video.aweme_id}].mp4"


def download(video: DouyinVideo, dest, on_progress=None, retries: int = 5,
             should_stop=None):
    """Keep the selected original; interrupted long transfers resume safely."""
    from pathlib import Path
    import file_download
    import media_quality
    from common import Cancelled

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cancelled = should_stop or (lambda: False)
    if cancelled():
        raise Cancelled()
    headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}
    conn, response, _ = file_download.open_public(video.url, headers)
    try:
        total = int(response.getheader("Content-Length") or 0)
        if (response.getheader("Content-Type") or "").split(";")[0] in ("text/html", "application/json"):
            raise DouyinError("平台未提供完整原视频")
    finally:
        response.close()
        conn.close()

    def progress(size):
        if on_progress and total:
            on_progress(min(size / total, 1.0))

    # Do not replace an existing completed file until the new original passes.
    import tempfile
    with tempfile.NamedTemporaryFile(prefix="douyin-", suffix=".mp4",
                                     dir=dest.parent, delete=False) as placeholder:
        candidate = Path(placeholder.name)
    try:
        file_download.fetch_original(video.url, dest.parent, candidate.name, total,
                                     progress, cancelled, headers)
        with candidate.open("rb") as stream:
            head = stream.read(12)
        if len(head) < 12 or head[4:8] != b"ftyp":
            raise DouyinError("下载的不是有效 MP4")
        media_quality.verify(candidate, {"width": video.width, "height": video.height,
                                        "duration": video.duration})
        if cancelled():
            raise Cancelled()
        candidate.replace(dest)
        return dest
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    import sys
    v = resolve(sys.argv[1])
    print(json.dumps({
        "id": v.aweme_id, "title": v.title,
        "size": f"{v.width}x{v.height}", "duration": round(v.duration, 1),
        "url": v.url[:120] + "...",
    }, ensure_ascii=False, indent=1))
