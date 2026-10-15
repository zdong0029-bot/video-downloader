"""Identify the same video behind different share links.

Instagram, TikTok, YouTube and others append per-share tracking parameters
(?srtk=, ?cplk=, ?igsh=, ?si= ...), so one reel pasted twice looks like two
different links. video_key() returns a stable key so a batch only downloads it once.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

_ID_PATTERNS = [
    (r"(?:^|\.)instagram\.com$", r"/(?:[^/]+/)?(reels?|p|tv)/([A-Za-z0-9_-]+)", "ig"),
    (r"(?:^|\.)instagram\.com$", r"/stories/[^/]+/(\d+)", "igs"),
    (r"(?:^|\.)tiktok\.com$", r"/(?:video|photo)/(\d+)", "tt"),
    (r"(?:^|\.)douyin\.com$", r"/(?:video|note)/(\d+)", "dy"),
    (r"(?:^|\.)youtube\.com$", r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{6,})", "yt"),
    (r"^youtu\.be$", r"/([A-Za-z0-9_-]{6,})", "yt"),
    (r"(?:^|\.)(?:twitter|x)\.com$", r"/status(?:es)?/(\d+)", "x"),
    (r"(?:^|\.)bilibili\.com$", r"/video/(BV[A-Za-z0-9]+|av\d+)", "bili"),
    (r"(?:^|\.)xiaohongshu\.com$", r"/(?:explore|discovery/item)/([0-9a-f]{16,})", "xhs"),
    (r"(?:^|\.)facebook\.com$", r"/(?:reel|videos)/(\d+)", "fb"),
]
_TRACKING = re.compile(r"^(utm_\w+|igsh|igshid|srtk|cplk|xtok|si|feature|is_from_webapp|sender_device|"
                       r"share_\w+|_r|_t|u_code|did|xsec_source|from|spm_id_from|vd_source|t|ref|fbclid|gclid)$", re.I)


def video_key(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    if host.startswith("www.") or host.startswith("m."):
        host = host.split(".", 1)[1]
    path = p.path or "/"
    for host_re, path_re, tag in _ID_PATTERNS:
        if re.search(host_re, host):
            m = re.search(path_re, path)
            if m:
                return tag + ":" + m.groups()[-1]
    query = parse_qsl(p.query, keep_blank_values=True)
    if re.search(r"(?:^|\.)youtube\.com$", host) and path.rstrip("/") == "/watch":
        v = dict(query).get("v")
        if v:
            return "yt:" + v
    kept = sorted((k, v) for k, v in query if not _TRACKING.match(k))
    return host + path.rstrip("/") + ("?" + urlencode(kept) if kept else "")
