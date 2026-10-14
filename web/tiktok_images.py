"""TikTok photo (slideshow) posts: save the original images and the post's music.

yt-dlp has no extractor for /photo/ links, so this reuses its TikTok web-data
reader (same impersonation and cookies) and downloads the image URLs the page lists.
"""
import re
from pathlib import Path

from common import Cancelled

_PHOTO = re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w.-]*/photo/(\d+)", re.I)


def supported(url: str) -> bool:
    return bool(_PHOTO.match(url or ""))


def _safe(text: str, limit: int = 60) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text or "").strip()
    return (text[:limit].strip() or "TikTok图文")


def download(ydl, url: str, out: Path, progress, cancelled):
    m = _PHOTO.match(url)
    post_id = m.group(1)
    video_url = url.split("?")[0].replace("/photo/", "/video/")
    ie = ydl.get_info_extractor("TikTok")
    data, _status = ie._extract_web_data_and_status(video_url, post_id)
    images = ((data or {}).get("imagePost") or {}).get("images") or []
    if not images:
        raise ValueError("TikTok 未返回这条图文的图片，可能已删除或需要登录")
    desc = (data.get("desc") or "").strip()
    author = ((data.get("author") or {}).get("uniqueId") or "").strip()
    base = f"{author} - {_safe(desc)} [{post_id}]" if author else f"{_safe(desc)} [{post_id}]"
    paths: list[Path] = []
    received = 0
    for i, image in enumerate(images, 1):
        if cancelled():
            raise Cancelled()
        urls = ((image.get("imageURL") or {}).get("urlList") or [])
        body = None
        for u in urls:
            try:
                body = ydl.urlopen(u).read()
                if body:
                    break
            except Exception:  # noqa: BLE001 - try the next CDN mirror
                body = None
        if not body:
            raise ValueError(f"TikTok 第 {i} 张图片下载失败")
        ext = ".png" if body[:4] == b"\x89PNG" else ".webp" if body[8:12] == b"WEBP" else ".jpg"
        p = out / f"{base} {i:02d}{ext}"
        p.write_bytes(body)
        paths.append(p)
        received += len(body)
        progress(i, len(images), received)
    music = ((data.get("music") or {}).get("playUrl") or "")
    if music:
        try:
            body = ydl.urlopen(music).read()
            if body:
                p = out / f"{base}.audio.mp3"
                p.write_bytes(body)
                paths.append(p)
        except Exception:  # noqa: BLE001 - music is optional
            pass
    return desc or "TikTok 图文", paths
