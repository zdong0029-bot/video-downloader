"""TikTok Symphony Creative Studio 分享链接下载引擎（Get-SymphonyVideos.ps1 的 Python 移植）。

接口要点：
  GET https://ads.tiktok.com/CreativeOne/FactoryCue/SymphonyShareLink/GetShareLink
      ?share_id=<UUID>&aid=585599&app_name=creative_aio_client&device_platform=web
  公开接口，不需要登录态，但要带常规浏览器 UA 和 ads.tiktok.com 的 Referer。
  成功时 BaseResp.StatusCode == 0。分享链接 30 天过期。

两个容易踩的坑：
  1. 一个素材会对应 5 个 URL（1 个 previewLink + 4 个 playInfos 多码率转码流），
     内容相同只是画质不同。只取 previewLink，否则同一个视频会下 5 遍。
  2. 同一批 prompt 生成的素材 name 完全相同（AIOId 也相同），只有 vid 唯一。
     只按 name 命名会让后面的素材互相覆盖，用户会静默丢视频。
"""

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = "https://ads.tiktok.com/CreativeOne/FactoryCue/SymphonyShareLink/GetShareLink"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Referer": "https://ads.tiktok.com/",
}

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SymphonyError(Exception):
    pass


@dataclass
class Asset:
    name: str
    vid: str
    url: str
    width: int = 0
    height: int = 0
    duration: float = 0.0
    filename: str = field(default="")

    def __post_init__(self):
        if not self.filename:
            safe = _ILLEGAL.sub("_", self.name or "asset").strip() or "asset"
            safe = safe[:80]
            tag = f"_{self.vid[-8:]}" if self.vid and len(self.vid) >= 8 else ""
            self.filename = f"{safe}{tag}.mp4"


def extract_share_id(text: str) -> str | None:
    m = UUID_RE.search(text or "")
    return m.group(0) if m else None


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    # urlopen 默认读取 http_proxy / https_proxy 环境变量，本机代理会自动生效
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def list_assets(share_link: str) -> list[Asset]:
    """解析分享链接，返回素材列表。不下载。"""
    share_id = extract_share_id(share_link)
    if not share_id:
        raise SymphonyError("无法从输入中识别出分享码（应为 UUID 格式）")

    params = urllib.parse.urlencode({
        "share_id": share_id,
        "aid": "585599",
        "app_name": "creative_aio_client",
        "device_platform": "web",
    })

    last_err = None
    for attempt in range(3):
        try:
            raw = _get(f"{API}?{params}")
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 2:
                raise SymphonyError(f"接口请求失败：{e}") from e
            time.sleep(2 * (attempt + 1))
    else:  # pragma: no cover
        raise SymphonyError(f"接口请求失败：{last_err}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SymphonyError("接口返回的不是有效 JSON，链接可能已失效") from e

    base = data.get("BaseResp") or {}
    if base.get("StatusCode") not in (0, None):
        raise SymphonyError(
            f"接口返回错误：{base.get('StatusMessage') or base.get('StatusCode')}"
        )

    drafts = ((data.get("share_link") or {}).get("draft_infos")) or []
    assets: list[Asset] = []

    for i, d in enumerate(drafts, 1):
        vi = d.get("videoInfo") or {}
        url = d.get("previewLink")

        if not url:
            # 没有 previewLink 时退而取分辨率最高的码流
            plays = vi.get("playInfos") or []
            if plays:
                best = max(plays, key=lambda p: int(p.get("width") or 0))
                url = best.get("playUrl") or best.get("backupUrl")

        if not url:
            continue  # 图片素材或尚未渲染完成

        assets.append(Asset(
            name=d.get("name") or f"asset_{i}",
            vid=d.get("vid") or "",
            url=url,
            width=int(vi.get("width") or 0),
            height=int(vi.get("height") or 0),
            duration=float(vi.get("duration") or 0),
        ))

    if not assets:
        raise SymphonyError("没有解析到可下载内容，链接可能已过期（分享链接 30 天有效）或无效")

    return assets


def download_asset(asset: Asset, out_dir: Path, on_progress=None, retries: int = 5) -> Path:
    """下载单个素材，校验 MP4 文件头。返回最终路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / asset.filename
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    tmp = dest.with_suffix(".mp4.part")
    last_err = None

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(asset.url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if on_progress and total:
                            on_progress(got / total)

            # 校验第 5-8 字节是否为 ftyp，避免把错误页面当成视频存下来
            with open(tmp, "rb") as f:
                head = f.read(12)
            if len(head) < 12 or head[4:8] != b"ftyp":
                raise SymphonyError("下载的文件不是有效 MP4（可能是错误页面）")

            tmp.replace(dest)
            return dest

        except Exception as e:  # noqa: BLE001
            last_err = e
            tmp.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(min(2 * attempt, 10))

    raise SymphonyError(f"下载失败：{last_err}")
