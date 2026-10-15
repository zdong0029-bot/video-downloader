"""局域网视频下载器 —— FastAPI 后端。

家里人在同一个路由器下打开 http://<本机IP>:8000 就能用，
粘贴链接 -> 服务端下载 -> 浏览器取走文件。他们不需要装任何东西，也不需要配代理：
去 TikTok 取视频这一步走的是本机的网络（含本机代理），
从本机传到手机那一步是纯局域网。
"""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
import link_identity
import stats

import yt_dlp
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
from common import Cancelled

import douyin
import symphony
import file_download
import baidu_backend
import cloud_backend
import cloud_cleanup
import task_history
import relay_sync
import video_catalog
import xhs_images
import video_accounts, wechat_channels, platform_extractors
import media_parts, media_tools, tiktok_images

ROOT = Path(__file__).parent
DOWNLOADS = ROOT / "downloads"
STATIC = ROOT / "static"
COOKIES = ROOT / "cookies.txt"      # 可选：抖音等站点需要
DOWNLOADS.mkdir(exist_ok=True)

# 同时下载的任务数。家用场景不需要开太大，避免把上行带宽和代理打满。
MAX_WORKERS = 2
_slots = threading.Semaphore(MAX_WORKERS)
# 单视频/图文链接解析+下载很小，放在独立的通道里并发跑（默认 6 个），
# 不再和网盘大文件抢那 2 个通道；可用环境变量 DL_VIDEO_WORKERS 调整。
VIDEO_WORKERS = max(1, int(os.environ.get("DL_VIDEO_WORKERS", "6") or 6))
_video_slots = threading.Semaphore(VIDEO_WORKERS)
_douyin_slots = threading.Semaphore(3)   # 抖音每个都要开一个浏览器，限 3 个
_tool_slots = threading.Semaphore(1)     # 批量工具吃 CPU，一次一个任务（ffmpeg 自己多线程）
# 同一个网盘账号一次只跑一个分享任务（并行不会更快，还会抢令牌、互相限速）。
# 在占用总通道之前排队，排队的网盘任务不再挡住视频任务。
_cloud_queues: dict[str, threading.Lock] = {}
_cloud_running: dict[str, str] = {}


def _cloud_queue_key(url: str) -> str:
    try:
        return cloud_backend.provider(url) or ""
    except Exception:  # noqa: BLE001
        return ""


@dataclass
class FileItem:
    name: str
    size: int = 0
    done: bool = False
    entry_id: str = ""
    folder: str = ""   # 网盘分享里的原目录（相对路径），用于“保存到文件夹”还原结构
    label: str = ""    # 网盘里的原文件名（磁盘文件名带防重名前缀）


@dataclass
class Job:
    id: str
    url: str
    kind: Literal["symphony", "douyin", "web", "file", "cloud", "collection", "tool"]
    status: Literal["queued", "running", "done", "error", "partial", "cancelled"] = "queued"
    title: str = ""
    message: str = ""
    progress: float = 0.0          # 0~1，当前文件
    total: int = 0                 # 素材总数
    finished: int = 0
    failed: int = 0
    files: list[FileItem] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    owner: str = ""          # 提交这个任务的设备标识，用于隔离各设备的记录
    completed_at: float = 0.0
    catalog_id: str = ""
    current_entry: str = ""
    failed_entries: dict = field(default_factory=dict)
    owner_label: str = ""    # 人类可读的设备名，只给管理员看
    cloud_note: str = ""
    batch_id: str = ""
    entries: list[dict] = field(default_factory=list, repr=False)
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancelled(self) -> bool:
        return self.stop.is_set()

    def public(self) -> dict[str, Any]:
        relay = relay_sync.enabled()
        return {
            "id": self.id,
            "url": self.url,
            "kind": self.kind,
            "batch_id": self.batch_id,
            "status": self.status,
            "title": self.title,
            "message": self.message,
            "progress": round(self.progress, 4),
            "total": self.total,
            "finished": self.finished,
            "failed": self.failed,
            "created": self.created,
            "completed_at": self.completed_at,
            "expires_at": self.completed_at + task_history.RETENTION if self.completed_at else None,
            "catalog_id": self.catalog_id,
            "current_entry": self.current_entry,
            "failed_entries": self.failed_entries,
            "selected_entries": [e["id"] for e in self.entries],
            "owner": (self.owner_label + " · " + self.owner[:6]) if self.owner_label else self.owner[:8],
            "files": [{"name": f.name, "size": f.size, "entry_id": f.entry_id, **({"folder": f.folder, "label": f.label} if f.folder or f.label else {}), **({"relay": relay_sync.status(self.id, f.name)} if relay else {})} for f in self.files if f.done],
        }


JOBS: dict[str, Job] = {}
_lock = threading.Lock()

META = "meta.json"
HIDDEN = DOWNLOADS / ".hidden.json"


def _load_hidden() -> dict[str, list[str]]:
    """被"移出列表"的文件。这些文件还在磁盘上，只是不在网页上显示。"""
    try:
        return json.loads(HIDDEN.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


_hidden_lock = threading.RLock()

def _save_hidden(data: dict[str, list[str]]) -> None:
    temporary = HIDDEN.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, HIDDEN)
    except OSError:
        raise HTTPException(500, "无法保存移除记录，请重试") from None

def _hide(job_id: str, names: list[str]) -> None:
    with _hidden_lock:
        data = _load_hidden()
        data[job_id] = sorted(set(data.get(job_id, [])) | set(names))
        _save_hidden(data)

def _unhide_all(job_id: str) -> None:
    with _hidden_lock:
        data = _load_hidden()
        if data.pop(job_id, None) is not None:
            _save_hidden(data)


def _save_meta(job: Job) -> None:
    """把任务信息落盘。不存的话，服务一重启（开机自启那次也算）列表就空了，
    磁盘上的视频全变成看不见的孤儿文件，既占空间又找不回来。"""
    task_history.remember(job)
    try:
        (DOWNLOADS / job.id / META).write_text(json.dumps({
            "id": job.id, "url": job.url, "kind": job.kind,
            "title": job.title, "created": job.created, "owner": job.owner, "owner_label": job.owner_label,
            "batch_id": job.batch_id, "cloud_note": job.cloud_note,
            "completed_at": job.completed_at, "catalog_id": job.catalog_id,
            **({"cloud_files": [f.name for f in job.files],
                "cloud_folders": {f.name: [f.folder, f.label] for f in job.files if f.folder or f.label}} if job.kind == "cloud" else {}),
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _restore_jobs() -> None:
    """启动时扫描 downloads，把之前下过的任务恢复到列表里。"""
    if not DOWNLOADS.is_dir():
        return
    hidden = _load_hidden()

    for d in DOWNLOADS.iterdir():
        if not d.is_dir():
            continue
        if (d / ".cloud-incomplete").exists():
            continue

        cloud_names = None
        if (d / META).is_file():
            try:
                saved = json.loads((d / META).read_text(encoding="utf-8"))
                if saved.get("kind") == "cloud" and "cloud_files" in saved:
                    cloud_names = set(saved["cloud_files"])
            except (OSError, ValueError): pass
        junk = {".part", ".ytdl", ".tmp", ".temp", ".json"}
        on_disk = [p for p in d.iterdir() if p.is_file() and (p.name in cloud_names if cloud_names is not None else p.suffix.lower() not in junk)]
        if not on_disk:
            # History expiry and restoration never delete media or unfinished files.
            continue

        # 被移出列表的文件仍在磁盘上，只是不再显示。目录不能删。
        skip = set(hidden.get(d.name, []))
        files = [p for p in on_disk if p.name not in skip]
        if not files:
            continue

        meta: dict[str, Any] = {}
        mp = d / META
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}

        job = Job(
            id=d.name,
            url=meta.get("url", ""),
            kind=meta.get("kind", "web"),
            status="done",
            title=meta.get("title") or f"历史下载 × {len(files)}",
            created=meta.get("created") or d.stat().st_mtime,
            owner=meta.get("owner", ""),
            owner_label=meta.get("owner_label", ""),
            batch_id=meta.get("batch_id", ""),
            cloud_note=meta.get("cloud_note", ""),
            completed_at=meta.get("completed_at") or mp.stat().st_mtime if mp.exists() else d.stat().st_mtime,
            catalog_id=meta.get("catalog_id", ""),
        )
        folders = meta.get("cloud_folders") or {}
        job.files = [FileItem(p.name, p.stat().st_size, True, folder=str((folders.get(p.name) or ["", ""])[0]), label=str((folders.get(p.name) or ["", ""])[1]))
                     for p in sorted(files, key=lambda p: (str((folders.get(p.name) or [""])[0]), p.name))]
        job.total = job.finished = len(job.files)
        job.progress = 1.0
        job.message = f"共 {len(job.files)} 个" + ("；" + job.cloud_note if job.cloud_note else "")
        JOBS[job.id] = job


def _restore_task_history():
    for row in task_history.load():
        if not task_history.retained(row["status"],row.get("completed_at",0),row["created"]): continue
        persisted=row.pop("files",[])
        job=Job(**row)
        job.files=[FileItem(**f) for f in persisted if (_safe_restore_path(job.id,f["name"])) and f["name"] not in _load_hidden().get(job.id,[])]
        if persisted and not job.files and job.status=="done":
            task_history.forget(job.id)
            continue
        if job.id in JOBS:
            present={f.name for f in JOBS[job.id].files}
            job.files=[f for f in job.files if f.name in present]
        if job.status in task_history.ACTIVE:
            job.status="cancelled";job.completed_at=time.time()
            job.message="服务重启中断了后台任务；已完成文件保留，请重新提交未完成部分"
            task_history.remember(job)
        JOBS[job.id]=job

def _safe_restore_path(job_id,name):
    base=(DOWNLOADS/job_id).resolve()
    p=(base/name).resolve()
    return p.parent==base and p.is_file() and not p.is_symlink()

# ----------------------------------------------------------------- 下载逻辑

def _firefox_profile() -> bool:
    """有没有可用的 Firefox 配置文件。没装 Firefox 时直接跳过，
    否则 yt-dlp 会因为找不到 cookie 库而让整个任务失败。"""
    base = Path(os.environ.get("APPDATA", "")) / "Mozilla/Firefox/Profiles"
    if not base.is_dir():
        return False
    return any((p / "cookies.sqlite").is_file() for p in base.iterdir() if p.is_dir())


_ff_jar = [0.0, None]
_ff_jar_lock = threading.Lock()


def _firefox_cookies():
    """Firefox cookie 读一次缓存 10 分钟。以前每个链接都重新拷贝解析 cookie 库，并发时尤其慢。"""
    import copy
    from yt_dlp.cookies import extract_cookies_from_browser, YoutubeDLCookieJar
    with _ff_jar_lock:
        if _ff_jar[1] is None or time.time() - _ff_jar[0] > 600:
            _ff_jar[1] = extract_cookies_from_browser("firefox")
            _ff_jar[0] = time.time()
        jar = YoutubeDLCookieJar()
        for c in _ff_jar[1]:
            jar.set_cookie(copy.copy(c))
        return jar


def _add_parts(job, video: Path, cover_url: str = "", body: bytes | None = None) -> None:
    """视频旁边再生成封面和音频，让前端能分别勾选。失败不影响视频本身。"""
    if job.cancelled():
        return
    if cover_url and body is None:
        try:
            if True:
                import urllib.request
                req = urllib.request.Request(cover_url, headers={"User-Agent": "Mozilla/5.0"})
                body = urllib.request.urlopen(req, timeout=20).read()
        except Exception:  # noqa: BLE001
            body = None
    names = {f.name for f in job.files}
    for p in media_parts.make_parts(video, body):
        if p.name not in names:
            job.files.append(FileItem(p.name, p.stat().st_size, True))


def _job_dir(job_id: str) -> Path:
    d = DOWNLOADS / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_symphony(job: Job) -> None:
    assets = symphony.list_assets(job.url)
    job.total = len(assets)
    job.title = f"Symphony 素材 × {len(assets)}"
    out = _job_dir(job.id)

    for i, asset in enumerate(assets, 1):
        if job.cancelled():
            raise Cancelled()
        job.message = f"({i}/{len(assets)}) {asset.name}"
        job.progress = 0.0
        try:
            path = symphony.download_asset(
                asset, out, on_progress=lambda p: setattr(job, "progress", p),
                should_stop=job.cancelled,
            )
            job.files.append(FileItem(path.name, path.stat().st_size, True))
            job.finished += 1
        except Cancelled:
            raise                      # 取消要穿透出去，不能算成这一条失败
        except Exception as e:  # noqa: BLE001
            job.failed += 1
            job.message = f"{asset.name} 失败：{e}"


def _run_douyin(job: Job) -> None:
    job.total = 1
    job.message = "正在用浏览器解析…"
    if job.cancelled():
        raise Cancelled()
    with _douyin_slots:
        video = douyin.resolve(job.url)

    job.title = video.title
    job.message = f"{video.width}x{video.height}"
    out = _job_dir(job.id)
    path = out / douyin.safe_filename(video)

    douyin.download(video, path, on_progress=lambda p: setattr(getattr(job, "progress_target", job), "progress", p),
                    should_stop=job.cancelled)
    job.files.append(FileItem(path.name, path.stat().st_size, True))
    job.finished = 1
    _add_parts(job, path, getattr(video, "cover", "") or "")


def _run_web(job: Job) -> None:
    out = _job_dir(job.id)
    if video_accounts.supported(job.url):
        job.total=1;job.message='正在解析视频号授权播放信息…'
        def channel_progress(size,total):
            job.progress=size/total if total else 0
            getattr(job,'progress_target',job).progress=job.progress
            job.message=f'视频号 · 已接收 {size/1048576:.1f} MB'
        title,path,media=wechat_channels.download(job.url,out,channel_progress,job.cancelled)
        job.title=title;job.files.append(FileItem(path.name,path.stat().st_size,True));job.finished=1
        job.cloud_note=f"视频号原播放流 {media.get('width',0)}×{media.get('height',0)}；未转码，已通过媒体校验"
        _add_parts(job, path)
        return
    if xhs_images.supported(job.url):
        def image_progress(done,total,size):
            job.total=total;job.progress=done/total if total else 0
            job.message=f"图片 {done}/{total} · 已接收 {size/1048576:.1f} MB"
        result=xhs_images.download(job.url,out,image_progress,job.cancelled)
        if result is not None:
            job.title,paths=result
            job.files.extend(FileItem(p.name,p.stat().st_size,True) for p in paths)
            job.total=job.finished=len(paths)
            job.cloud_note='图片按平台公开提供的版本保存，未放大或重新压缩'
            return
    before = {p.name for p in out.iterdir() if p.is_file()}
    progress_job = getattr(job, "progress_target", job)

    def hook(d: dict) -> None:
        # yt-dlp 没有取消 API，只能在进度回调里抛异常把它打断
        if job.cancelled():
            raise Cancelled()
        if d.get("status") == "downloading":
            job.total = max(job.total, int(d.get("info_dict", {}).get("playlist_count") or 1))
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job.progress = min(1.0, (d.get("downloaded_bytes") or 0) / total)
                progress_job.progress = job.progress
            name = d.get("info_dict", {}).get("title")
            if name:
                job.message = name
        elif d.get("status") == "finished":
            job.progress = 1.0
            job.message = "正在合并音视频…"

    opts = {
        "outtmpl": str(out / "%(uploader)s - %(title).80s [%(id)s].%(ext)s"),
        "format": "bv*+ba/b",
        # 画质排序：分辨率 -> 帧率 -> 码率。
        # yt-dlp 默认把"编码格式新"排在码率前面，在 TikTok 上会选中同样 720p
        # 但码率只有三分之一的 H.265 流，分辨率没缩水细节却被压没了，看着发糊。
        "format_sort": ["res", "fps", "tbr"],
        "format_sort_force": True,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "windowsfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "skip_unavailable_fragments": False,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    # A nonresponsive Tencent CDN must not hold the queue for ten retries
    # on every fragment. Keep full-quality selection and reject missing data.
    from urllib.parse import urlsplit
    if (urlsplit(job.url).hostname or '').lower() == 'v.qq.com':
        opts.update(retries=2, fragment_retries=2, socket_timeout=15)

    # Bilibili and iQiyi official hosts are reachable directly here; an unrelated
    # Windows proxy was refusing the CDN, before any bytes were transferred.
    if (urlsplit(job.url).hostname or '').lower() in ('www.bilibili.com','bilibili.com','m.bilibili.com','www.iqiyi.com','iqiyi.com'):
        opts.update(proxy='', retries=2, fragment_retries=2, socket_timeout=15)

    # 部分站点（B站高清、小红书、微博等）要带浏览器 cookie 才给数据。
    # 优先用手动导出的 cookies.txt；没有就尝试从 Firefox 读。
    #
    # 为什么是 Firefox 而不是 Chrome/Edge：
    #   Chrome 运行时会锁住 cookie 数据库，拷不出来；
    #   而且 Chromium 127+ 启用了应用绑定加密，即使关掉浏览器 yt-dlp 也解不开
    #   （报错 Failed to decrypt with DPAPI）。
    #   Firefox 的 cookie 是明文 SQLite，开着也能读。
    if COOKIES.is_file():
        opts["cookiefile"] = str(COOKIES)
    jar = None
    if "cookiefile" not in opts and _firefox_profile():
        try:
            jar = _firefox_cookies()
        except Exception:  # noqa: BLE001 - fall back to yt-dlp reading it itself
            opts["cookiesfrombrowser"] = ("firefox", None, None, None)

    job.total = 1
    with yt_dlp.YoutubeDL(opts) as ydl:
        if jar is not None:
            ydl.__dict__["cookiejar"] = jar
        platform_extractors.install(ydl)
        if tiktok_images.supported(job.url):
            def photo_progress(done, total, size):
                job.total = total; job.progress = done / total if total else 0
                getattr(job, "progress_target", job).progress = job.progress
                job.message = f"TikTok 图文 {done}/{total} · 已接收 {size/1048576:.1f} MB"
            job.message = "正在解析 TikTok 图文…"
            job.title, paths = tiktok_images.download(ydl, job.url, out, photo_progress, job.cancelled)
            job.files.extend(FileItem(p.name, p.stat().st_size, True) for p in paths)
            job.finished = sum(1 for p in paths if ".audio." not in p.name)
            job.total = max(job.total, job.finished)
            job.cloud_note = "图片按平台原图保存，附带图文背景音乐"
            return
        try:
            info = ydl.extract_info(job.url, download=True)
        except yt_dlp.utils.DownloadError as error:
            from urllib.parse import urlsplit
            host=(urlsplit(job.url).hostname or '').lower()
            if host.endswith('iqiyi.com') or host.endswith('iq.com'):
                raise ValueError('爱奇艺未提供当前下载器可用的视频流；当前样例未通过，会员或加密视频需使用官方授权下载') from None
            if host.endswith('weixin.qq.com'):
                raise ValueError('视频号当前分享链接未通过下载测试；需要可用的平台授权解析，不能直接按普通视频链接下载') from None
            if 'DRM' in str(error):raise ValueError('平台视频有版权加密，需要使用官方授权下载') from None
            raise
        if not info:
            raise RuntimeError("平台未返回可下载视频")
        cover_bytes = None
        if not info.get("entries") and info.get("thumbnail"):
            try:
                cover_bytes = ydl.urlopen(info["thumbnail"]).read()
            except Exception:  # noqa: BLE001 - falls back to the first frame
                cover_bytes = None
        job.title = info.get("title") or job.url
        if info.get("entries") is not None:
            job.total = max(job.total, len(list(info["entries"])))

    # 以磁盘上的成品为准。中途失败会留下 .part / .ytdl，那些不是成品，
    # 不能算成功——否则一个下到一半的大文件会被报成"下载完成"。
    junk = {".part", ".ytdl", ".tmp", ".temp", ".json"}
    got = [p for p in out.iterdir() if p.is_file() and p.suffix.lower() not in junk and p.name not in before]
    if not got:
        raise RuntimeError("下载结束但没有产出文件")
    import media_quality
    evidence = [media_quality.verify(p, info if not info.get('entries') else None) for p in got]
    job.cloud_note = '；'.join(dict.fromkeys(evidence))
    for p in got:
        job.files.append(FileItem(p.name, p.stat().st_size, True))
    job.finished = len(got)
    job.total = max(job.total, len(got))
    for p in got:
        _add_parts(job, p, body=cover_bytes if len(got) == 1 else None)


def _run_collection(job: Job) -> None:
    title = job.title
    job.total = len(job.entries)
    for i, entry in enumerate(job.entries, 1):
        if job.cancelled():
            raise Cancelled()
        job.progress = 0.0
        job.current_entry=entry["id"]
        job.message = f"({i}/{job.total}) {entry['title']}"
        child = Job(id=job.id, url=entry["url"], kind=_kind_of(entry["url"]) or "web",
                    owner=job.owner, stop=job.stop)
        child.progress_target = job
        try:
            if child.kind == "douyin":
                _run_douyin(child)
            else:
                _run_web(child)
            existing = {f.name for f in job.files}
            for f in child.files:
                if f.name not in existing:
                    f.entry_id=entry["id"]
                    job.files.append(f)
                    existing.add(f.name)
            job.finished += 1
            _save_meta(job)
        except Cancelled:
            raise
        except Exception as error:
            if isinstance(error.__cause__, Cancelled):
                raise Cancelled() from error
            job.failed += 1
            job.failed_entries[entry["id"]]=str(error)[:1000]
            job.message = f"{entry['title']} 失败：{error}"
    job.current_entry=""
    job.title = title


def _drop_empty_cancelled(job: Job) -> None:
    """取消且一个文件都没下到的任务，从列表和磁盘上一并清掉，不留空壳。"""
    job.status = "cancelled"
    job.message = "已取消"
    job.completed_at=time.time()
    task_history.remember(job)


def _worker(job: Job) -> None:
    # 排队等位时也要能取消——30 个任务里只有 2 个在跑，其余都卡在这里
    if job.cancelled():
        _drop_empty_cancelled(job)
        return
    queue_key = _cloud_queue_key(job.url) if job.kind == "cloud" else ""
    if not queue_key:
        return _worker_slot(job)
    with _lock:
        queue = _cloud_queues.setdefault(queue_key, threading.Lock())
    name = cloud_backend.NAMES.get(queue_key, "网盘") if hasattr(cloud_backend, "NAMES") else "网盘"
    while not queue.acquire(timeout=.5):
        if job.cancelled():
            _drop_empty_cancelled(job)
            return
        other = JOBS.get(_cloud_running.get(queue_key, ""))
        job.message = f"排队中：{name}账号正在下载另一个分享" + (f"（{other.title or other.url[:40]}）" if other else "") + "，完成后自动开始；可随时取消"
    try:
        _cloud_running[queue_key] = job.id
        _worker_slot(job)
    finally:
        _cloud_running.pop(queue_key, None)
        queue.release()


def _worker_slot(job: Job) -> None:
    if job.cancelled():
        _drop_empty_cancelled(job)
        return
    pool = _slots if job.kind in ("cloud", "file") else _tool_slots if job.kind == "tool" else _video_slots
    with pool:
        if job.cancelled():
            _drop_empty_cancelled(job)
            return
        job.status = "running"
        try:
            if job.kind == "tool":
                _run_tool(job)
            elif job.kind == "collection":
                _run_collection(job)
            elif job.kind == "symphony":
                _run_symphony(job)
            elif job.kind == "douyin":
                _run_douyin(job)
            elif job.kind == "file":
                job.total = 1
                def progress(size, total, name):
                    job.title = name
                    job.progress = min(size / total, 0.999) if total else 0
                    job.message = f"已下载 {size / 1048576:.1f} MB" + (f" / {total / 1048576:.1f} MB" if total else "（总大小未知）")
                path = file_download.download(job.url, _job_dir(job.id), progress, job.cancelled)
                job.files.append(FileItem(path.name, path.stat().st_size, True))
                job.finished = 1
            elif job.kind == "cloud":
                job.title = file_download.provider(job.url) + "分享文件"
                if not cloud_backend.provider(job.url):
                    raise ValueError("此网盘暂不支持，请使用页面列出的官方分享链接")
                job.message = "正在读取分享目录并准备原文件（文件多时需要一两分钟）…"
                cloud_rate=[time.monotonic(),0,0.0]
                def cloud_progress(items, paths, received=None):
                    total = sum(int(item.get("size", 0)) for item in items)
                    current = sum(p.stat().st_size for p in paths if p.is_file() and p.name not in (META, ".cloud-incomplete") and not p.name.endswith(".BaiduPCS-Go-downloading"))
                    if received is not None: current=max(0,int(received))
                    now=time.monotonic();elapsed=now-cloud_rate[0]
                    if elapsed>=.5 and current>=cloud_rate[1]:
                        instant=(current-cloud_rate[1])/elapsed
                        cloud_rate[2]=instant if not cloud_rate[2] else .35*instant+.65*cloud_rate[2]
                        cloud_rate[0]=now;cloud_rate[1]=current
                    speed=cloud_rate[2]
                    rate=f" · {speed/1048576:.2f} MB/s" if speed else ""
                    if speed and total>current:
                        seconds=int((total-current)/speed)
                        rate+=f" · 预计剩余 {seconds//60}分{seconds%60}秒"
                    job.total = len(items)
                    job.progress = min(current / total, 0.99) if total else 0
                    job.message = f"已写入 {current / 1048576:.1f} MB / {total / 1048576:.1f} MB（完成后校验原文件）{rate}" if total else f"已写入 {current / 1048576:.1f} MB（官方快传总大小未知）{rate}"
                cloud_dir = _job_dir(job.id)
                (cloud_dir / ".cloud-incomplete").touch()
                paths = cloud_backend.run(job.url, "", cloud_dir, cloud_progress, job.cancelled)
                job.cloud_note = getattr(paths, "note", "")
                folders = getattr(paths, "folders", {}) or {}
                job.files = [FileItem(p.name, p.stat().st_size, True, folder=(folders.get(p.name) or ("", ""))[0], label=(folders.get(p.name) or ("", ""))[1]) for p in paths]
                job.total = job.finished = len(paths)
            else:
                _run_web(job)

            if job.failed and job.finished:
                job.status = "partial"
                job.message = f"完成 {job.finished} 个，失败 {job.failed} 个"
            elif job.failed:
                job.status = "error"
                job.message = job.message or "全部失败"
            else:
                job.status = "done"
                job.message = f"完成 {job.finished} 个" + ("；" + job.cloud_note if job.cloud_note else "")
                job.progress = 1.0
        except Cancelled:
            # 已经下好的保留，没下的不再继续，列表里也不显示未完成的部分
            job.status = "cancelled"
            job.progress = 0.0
            job.message = (f"已取消，保留已下载的 {len(job.files)} 个"
                           if job.files else "已取消")
        except Exception as e:  # noqa: BLE001
            # yt-dlp 会把我们抛的 Cancelled 包进 DownloadError，要认出来
            if isinstance(e.__cause__, Cancelled) or "Cancelled" in repr(e):
                job.status = "cancelled"
                job.progress = 0.0
                job.message = (f"已取消，保留已下载的 {len(job.files)} 个"
                               if job.files else "已取消")
            else:
                job.status = "error"
                job.message = str(e)

        job.completed_at=time.time()
        if job.kind != "tool" or job.url != "tool:upload":
            done_files = [f for f in job.files if f.done]
            stats.add_usage("profile" if job.kind == "collection" else stats.category_of(job.kind),
                            links=1 if job.kind == "tool" else 0, files=len(done_files), size=sum(f.size for f in done_files))
        task_history.remember(job)
        if job.files:
            _save_meta(job)
            if job.kind == "cloud" and job.status == "done" and (DOWNLOADS / job.id / META).is_file():
                (DOWNLOADS / job.id / ".cloud-incomplete").unlink(missing_ok=True)
            if job.status in ("done", "partial", "cancelled"): _relay_enqueue(job)


def _relay_enqueue(job: Job) -> None:
    """Optional mainland relay copy for faster public delivery; no-op unless configured."""
    if not relay_sync.enabled(): return
    for f in job.files:
        if f.done and _safe_restore_path(job.id, f.name): relay_sync.enqueue(job.id, f.name, DOWNLOADS / job.id / f.name, job.owner)


def _relay_known(job_id: str, name: str):
    job = JOBS.get(job_id)
    if job and any(f.name == name and f.done for f in job.files) and _safe_restore_path(job_id, name):
        return DOWNLOADS / job_id / name, job.owner
    return None


# ----------------------------------------------------------------- API

app = FastAPI(title="局域网视频下载器")

_restore_jobs()
_restore_task_history()
for _old_job in JOBS.values():
    task_history.remember(_old_job)
relay_sync.restore(_relay_known)

COOKIE = "sid"        # 管理员会话
DEVICE = "did"        # 设备/浏览器标识


@app.middleware("http")
async def _count_visitors(request: Request, call_next):
    """每个浏览器每天只记一次访客（见 stats.py）；只看 /api/ 请求，静态资源不算。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/") and request.cookies.get(DEVICE):
        try:
            admin = auth.user_of_token(request.cookies.get(COOKIE))
            stats.record_visit(request.cookies.get(DEVICE, ""), request.headers.get("user-agent", ""), bool(admin and admin.is_admin))
        except Exception:  # noqa: BLE001
            pass
    return response


@dataclass
class Identity:
    """谁在用。

    普通使用不需要登录：每个浏览器第一次访问时发一个 did cookie，
    之后它只看得到自己提交的任务。换台电脑、换个浏览器就是另一个身份，
    各看各的，天然避免了几个人共用一个列表互相干扰。
    管理员登录后能看到全部，并标出每条是哪台设备提交的。
    """
    device: str
    is_admin: bool = False
    admin_name: str = ""


def _local_ips() -> set[str]:
    """本机所有 IP。

    用户平时是用 http://192.168.0.14:8000 访问的，不是 localhost，
    只认 127.0.0.1 的话他自己反而看不到"打开文件夹"按钮。
    """
    ips = {"127.0.0.1", "::1", "localhost"}
    try:
        host = socket.gethostname()
        ips.add(socket.gethostbyname(host))
        for info in socket.getaddrinfo(host, None):
            ips.add(info[4][0])
    except OSError:
        pass
    return ips


LOCAL_IPS = _local_ips()


def is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in LOCAL_IPS


def _device_label(ua: str) -> str:
    """从 User-Agent 猜一个人类看得懂的名字，给管理员区分设备用。"""
    ua = ua or ""
    if "Android" in ua:
        os_name = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        os_name = "iPhone/iPad"
    elif "Mac OS X" in ua:
        os_name = "Mac"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = "未知系统"

    if "Edg/" in ua:
        br = "Edge"
    elif "Firefox/" in ua:
        br = "Firefox"
    elif "MicroMessenger" in ua:
        br = "微信"
    elif "Chrome/" in ua:
        br = "Chrome"
    elif "Safari/" in ua:
        br = "Safari"
    else:
        br = "浏览器"
    return f"{br} · {os_name}"


def current_identity(
    response: Response,
    sid: str | None = Cookie(default=None),
    did: str | None = Cookie(default=None),
) -> Identity:
    admin = auth.user_of_token(sid)
    if not did:
        did = secrets.token_urlsafe(16)
        response.set_cookie(DEVICE, did, httponly=True, samesite="lax",
                            max_age=3650 * 86400)
    return Identity(device=did,
                    is_admin=bool(admin and admin.is_admin),
                    admin_name=admin.name if admin else "")


def admin_only(me: Identity = Depends(current_identity)) -> Identity:
    if not me.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return me


def _owned(job: Job, me: Identity) -> bool:
    return me.is_admin or job.owner == me.device


def _get_job(job_id: str, me: Identity) -> Job:
    job = JOBS.get(job_id)
    if not job or not _owned(job, me):
        # 不区分"不存在"和"不是你的"，避免被人探测别人有哪些任务
        raise HTTPException(404, "任务不存在")
    return job


# ----------------------------------------------------------------- 登录

class Creds(BaseModel):
    username: str
    password: str


@app.get("/api/me")
def me(request: Request, me: Identity = Depends(current_identity)) -> dict:
    """前端启动时问这个。普通使用不需要登录，直接就能下载。"""
    host = (request.client.host if request.client else "") or ""
    return {
        "device": me.device[:8],
        "is_admin": me.is_admin,
        "admin_name": me.admin_name,
        "has_admin": auth.has_users(),
        # 只有在服务端本机打开时才显示"打开文件夹"——
        # 家里人用手机看这个页面，点了也只会打开我这台电脑的资源管理器，没意义
        "local": is_local(request),
    }


@app.post("/api/admin/login")
def admin_login(body: Creds, response: Response) -> dict:
    """管理员登录，登录后能看到所有设备的记录。"""
    if not auth.has_users():
        # 还没设过管理员密码，第一次登录就是设置
        try:
            auth.create_user(body.username, body.password, is_admin=True)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        u = auth.authenticate(body.username, body.password)
    else:
        u = auth.authenticate(body.username, body.password)
    if not u or not u.is_admin:
        raise HTTPException(401, "用户名或密码不对")

    token = auth.start_session(u.name)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=auth.SESSION_DAYS * 86400)
    return {"name": u.name}


@app.post("/api/logout")
def logout(response: Response, sid: str | None = Cookie(default=None)) -> dict:
    if sid:
        auth.end_session(sid)
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ----------------------------------------------------------------- 账号管理

class NewUser(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class NewPassword(BaseModel):
    password: str


@app.get("/api/users")
def list_users(me: Identity = Depends(admin_only)) -> list[dict]:
    users = auth.load_users()
    counts: dict[str, int] = {}
    for j in JOBS.values():
        counts[j.owner] = counts.get(j.owner, 0) + 1
    return [
        {"name": n, "is_admin": bool(u.get("is_admin")),
         "created": u.get("created", 0), "jobs": counts.get(n, 0)}
        for n, u in sorted(users.items())
    ]


@app.post("/api/users")
def add_user(body: NewUser, me: Identity = Depends(admin_only)) -> dict:
    try:
        auth.create_user(body.username, body.password, body.is_admin)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.post("/api/users/{name}/password")
def change_password(name: str, body: NewPassword,
                    me: Identity = Depends(admin_only)) -> dict:
    try:
        auth.set_password(name, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.delete("/api/users/{name}")
def remove_user(name: str, me: Identity = Depends(admin_only)) -> dict:
    if name == me.admin_name:
        raise HTTPException(400, "不能删除自己")
    try:
        auth.delete_user(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


class NewJob(BaseModel):
    url: str
    mode: Literal["auto", "media", "file", "profile", "cloud"] = "auto"


_URL_RE = re.compile(r"https?://[^\s一-鿿，。、；：！？（）【】「」…]+")


def _extract_links(text: str) -> list[str]:
    """从粘贴的内容里把链接挑出来。

    抖音、小红书这类 App 的分享是一整段文案，比如
      7.53 复制打开抖音，看看【某某的作品】 https://v.douyin.com/xxxx/ 复制此链接…
    按空格逐段校验的话，除了链接以外十几段都会被判成"无法识别"，
    用户看到一堆失败提示会以为出错了。这里直接用正则把链接抠出来，
    中文标点不算 URL 的一部分，末尾的标点也去掉。
    """
    links: list[str] = []
    seen: set[str] = set()

    matches = list(_URL_RE.finditer(text or ""))
    for index, m in enumerate(matches):
        u = m.group(0).rstrip(".,;:!?)）】」》\"'")
        if cloud_backend.provider(u):
            from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
            parts = urlsplit(u)
            query = parse_qsl(parts.query, keep_blank_values=True)
            # Associate only the text following this link, before the next link.
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            code = re.search(r'(?:提取码|提取碼|访问码|訪問碼|密码|密碼)\s*[:：]?\s*([A-Za-z0-9]{4})(?![A-Za-z0-9])', text[m.end():end])
            if code and not any(k in ("pwd","password") and v for k, v in query):
                query = [(k, v) for k, v in query if k != "pwd"]
                query.append(("pwd", code[1]))
                u = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        if u not in seen:
            seen.add(u)
            links.append(u)

    # 没有 http 链接时，再看看是不是直接贴的 Symphony 分享码（裸 UUID）
    if not links:
        for tok in (text or "").split():
            tok = tok.strip().strip("\"'")
            if symphony.extract_share_id(tok) and tok not in seen:
                seen.add(tok)
                links.append(tok)

    return links


def _kind_of(url: str) -> Literal["symphony", "douyin", "web", "file", "cloud"] | None:
    if file_download.provider(url):
        return "cloud"
    if file_download.is_file(url):
        return "file"
    if symphony.extract_share_id(url):
        return "symphony"
    # 抖音要走浏览器解析，yt-dlp 算不出它的 a_bogus 签名
    if douyin.is_douyin(url):
        return "douyin"
    if url.startswith(("http://", "https://")):
        return "web"
    return None



# Profile previews are separate from download jobs: no media is downloaded until selected.
_catalogs: dict[str, dict] = {}
_catalog_lock = threading.RLock()
_catalog_slots = threading.Semaphore(2)
_catalog_store = ROOT / 'data' / 'catalogs.dpapi'
_catalog_saved_at = 0.0

def _catalog_checkpoint():
    """Keep signed work links and visitor ownership private on this host."""
    import cloud_accounts
    global _catalog_saved_at
    _catalog_saved_at=time.monotonic()
    try:
        _catalog_store.parent.mkdir(parents=True, exist_ok=True)
        rows=[c for c in _catalogs.values() if task_history.retained(c["status"] if c["status"]!="loading" else "running",c.get("completed_at",0),c["created"])]
        payload=cloud_accounts.crypt(json.dumps(rows,ensure_ascii=False).encode('utf-8'))
        temp=_catalog_store.with_suffix('.tmp');temp.write_bytes(payload);temp.replace(_catalog_store)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Could not checkpoint private creator lists')

def _catalog_restore():
    if not _catalog_store.is_file():return
    import cloud_accounts
    try:
        for c in json.loads(cloud_accounts.crypt(_catalog_store.read_bytes(),True)):
            if not task_history.retained('running' if c['status']=='loading' else c['status'],c.get('completed_at',0),c['created']):continue
            if not video_catalog.is_profile(c['url']):
                c.update(entries=[],status='error',completed_at=c.get('completed_at') or time.time(),limited=False,complete=False,title='链接识别失败',message='旧版把单个作品误作主页识别，已停止展示错误列表。请到视频图片提取重新提交该作品链接。')
            if c['status'] in ('loading','queued'):
                c.update(status='error',completed_at=time.time(),limited=True,complete=False,message='上次读取中断，已保留作品；可继续读取全部')
            _catalogs[c['id']]=c
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Could not restore private creator lists')

_catalog_restore()

def _catalog_public(c):
    return {k: v for k, v in c.items() if k not in ("owner", "batch_id", "_version")}

def _catalog_worker(cid):
    with _catalog_slots:
        with _catalog_lock:
            c = _catalogs.get(cid)
        if not c:
            return
        with _catalog_lock:
            c.update(status="loading",message="正在识别主页素材…",_version=c.get("_version",0)+1)
        try:
            options = {}
            if COOKIES.is_file():
                options["cookiefile"] = str(COOKIES)
            elif _firefox_profile():
                options["cookiesfrombrowser"] = ("firefox", None, None, None)
            def update(entries, title):
                with _catalog_lock:
                    if _catalogs.get(cid) is not c:raise Cancelled()
                    known={e['id']:dict(e) for e in c['entries']}
                    known.update((e['id'],dict(e)) for e in entries)
                    c.update(entries=list(known.values()),title=title,_version=c.get('_version',0)+1,
                             message=f"已识别 {len(known)} 个，继续读取主页…可先勾选下载")
                    if time.monotonic()-_catalog_saved_at>=30:_catalog_checkpoint()
            def cancelled():
                with _catalog_lock:return _catalogs.get(cid) is not c
            last_error = None
            for attempt in range(2):
                try:
                    result = video_catalog.resolve(c["url"], c["limit"], options, update,cancelled)
                    break
                except Cancelled:
                    return
                except Exception as error:
                    last_error = error
                    if attempt:
                        raise
                    with _catalog_lock:
                        c['_version']=c.get('_version',0)+1
                        c["message"] = f"已识别 {len(c['entries'])} 个，正在自动重试解析…"
                    time.sleep(2)
            with _catalog_lock:
                if _catalogs.get(cid) is not c:return
                known={e['id']:e for e in c['entries']};known.update((e['id'],e) for e in result['entries'])
                if result['limited']:result['entries']=list(known.values())
                note = "（平台未确认列表结束，可能仍有未返回作品）" if result["limited"] else "（平台列表已读取结束）"
                detail = result.get('note') or ''
                c.update(result, status="done", completed_at=time.time(), message=f"识别到 {len(result['entries'])} 个作品{note}，请勾选下载" + (f"；{detail}" if detail else ''))
                c['_version']=c.get('_version',0)+1
                _catalog_checkpoint()
        except Exception as error:
            with _catalog_lock:
                if _catalogs.get(cid) is not c:return
                c.update(status="error", completed_at=time.time(), title=c["title"] if c["entries"] else "主页识别失败",
                         message=('识别失败：'+str(error))[:1500],limited=True,complete=False,_version=c.get('_version',0)+1)
                _catalog_checkpoint()

def _start_catalog(url, me, batch_id="", limit=0):
    if not video_catalog.is_profile(url):
        raise HTTPException(400, "识别失败：这不是主页或合集链接。单个视频、图片请使用“视频图片提取”。")
    with _catalog_lock:
        now = time.time()
        for cid in list(_catalogs):
            if not task_history.retained("running" if _catalogs[cid]["status"]=="loading" else _catalogs[cid]["status"],_catalogs[cid].get("completed_at",0),_catalogs[cid]["created"]):
                _catalogs.pop(cid, None)

        if len(_catalogs) >= 100:
            raise HTTPException(429, "主页列表暂时繁忙，请稍后再试")
        c = {"id": uuid.uuid4().hex[:12], "url": url, "owner": me.device,
             "batch_id": batch_id, "created": now, "limit": limit,
             "title": "主页素材", "status": "queued", "completed_at": 0,
             "message": "排队识别主页，只读取列表，不自动下载", "entries": [], "limited": False}
        _catalogs[c["id"]] = c
        _catalog_checkpoint()
    threading.Thread(target=_catalog_worker, args=(c["id"],), daemon=True).start()
    return _catalog_public(c)

def _workspace_url(url: str, request: Request) -> bool:
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    hosts = {request.url.hostname, 'video-downloader.tail49ff9c.ts.net', '127.0.0.1', 'localhost'}
    return parsed.hostname in hosts and parsed.path.rstrip('/') in {'', '/index.html', '/cloud-accounts.html'}

WORKSPACE_LINK_ERROR = '这是下载工作台的网址，请直接在浏览器打开；下载框请粘贴视频、主页或网盘分享链接。'

class CatalogRequest(BaseModel):
    url: str
    limit: int = 0

@app.post("/api/catalogs")
def create_catalog(body: CatalogRequest, request: Request, me: Identity = Depends(current_identity)):
    links = _extract_links(body.url)
    if not links:raise HTTPException(400,"请输入主页或合集链接")
    if len(links)>100:raise HTTPException(400,"一次最多提交100个主页")
    if body.limit not in (0,100,200):raise HTTPException(400,"请选择完整主页或分段读取")
    created=[];rejected=[]
    for url in links:
        if _workspace_url(url, request):
            rejected.append({'url':url,'reason':WORKSPACE_LINK_ERROR});continue
        try:created.append(_start_catalog(url,me,limit=body.limit))
        except HTTPException as error:rejected.append({"url":url,"reason":error.detail})
    return {"catalogs":created,"rejected":rejected}


def _get_catalog(cid, me):
    c = _catalogs.get(cid)
    if not c or c["owner"] != me.device:
        raise HTTPException(404, "主页列表不存在或已过期，请重新识别")
    return c

@app.get("/api/catalogs")
def list_catalogs(request:Request,response:Response,me: Identity = Depends(current_identity)):
    with _catalog_lock:
        rows=[c for c in sorted(_catalogs.values(),key=lambda c:c['created'],reverse=True) if c['owner']==me.device and task_history.retained('running' if c['status']=='loading' else c['status'],c.get('completed_at',0),c['created'])]
        version=[(c['id'],c.get('_version',0),c['status'],len(c['entries'])) for c in rows]
        etag='"'+hashlib.sha256(json.dumps(version).encode()).hexdigest()+'"'
        headers={'ETag':etag,'Cache-Control':'private, no-cache','Vary':'Cookie'}
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
        response.headers.update(headers)
        return [_catalog_public(c) for c in rows]

@app.post('/api/catalogs/{cid}/continue')
def continue_catalog(cid:str,me:Identity=Depends(current_identity)):
    with _catalog_lock:
        c=_get_catalog(cid,me)
        if c['status'] in ('loading','queued'):raise HTTPException(409,'该主页已在读取中')

        c.update(limit=0,status='queued',completed_at=0,complete=False,message='继续读取全部公开作品，已有勾选和作品保留',_version=c.get('_version',0)+1)
    threading.Thread(target=_catalog_worker,args=(cid,),daemon=True).start()
    return {'ok':True}

@app.delete("/api/catalogs/{cid}")
def remove_catalog(cid: str, me: Identity = Depends(current_identity)):
    with _catalog_lock:
        _get_catalog(cid, me)
        _catalogs.pop(cid)
        _catalog_checkpoint()
    return {"ok": True}

class CatalogSelection(BaseModel):
    ids: list[str]

@app.post("/api/catalogs/{cid}/download")
def download_catalog(cid: str, body: CatalogSelection, request: Request,
                     me: Identity = Depends(current_identity)):
    with _catalog_lock:
        c = _get_catalog(cid, me)
        if not c["entries"]:
            raise HTTPException(409, "主页列表尚未识别完成")
        known = {e["id"]: e for e in c["entries"]}
        ids = list(dict.fromkeys(body.ids))
        if not ids or any(i not in known for i in ids):
            raise HTTPException(400, "请勾选当前主页列表中的视频")
        entries = [dict(known[i]) for i in ids]
        job = Job(id=uuid.uuid4().hex[:12], url=c["url"], kind="collection",
                  title=f"{c['title']} · 已选 {len(entries)} 个", total=len(entries),
                  owner=me.device, owner_label=_device_label(request.headers.get("user-agent", "")),
                  batch_id=c["batch_id"], entries=entries, catalog_id=cid)
    with _lock:
        JOBS[job.id] = job
    task_history.remember(job)
    threading.Thread(target=_worker, args=(job,), daemon=True).start()
    return {"created": [job.public()]}

@app.post("/api/jobs")
def create_job(body: NewJob, request: Request,
               me: Identity = Depends(current_identity)) -> dict:
    """支持一次粘贴多个链接：按空白字符（换行、空格）切开，逐个建任务。

    并发数由 MAX_WORKERS 控制，多出来的排队跑，不会一次性把带宽和代理打满。
    """
    ua = request.headers.get("user-agent", "")
    raw = body.url or ""
    items = _extract_links(raw)
    if not items:
        raise HTTPException(400, "请输入链接")
    if all(_workspace_url(item, request) for item in items):
        raise HTTPException(400, WORKSPACE_LINK_ERROR)

    # 同一批里重复粘贴的只建一个任务
    seen: set[str] = set()
    created: list[dict] = []
    rejected: list[str] = []
    catalogs: list[dict] = []
    existing: list[dict] = []
    batch_id = uuid.uuid4().hex[:16] if len(items) > 1 else ""

    for item in items:
        ident = link_identity.video_key(item)
        if ident in seen:
            continue
        seen.add(ident)
        if _workspace_url(item, request):
            rejected.append(item);continue

        if body.mode == "media" and video_catalog.is_profile(item):
            rejected.append(item);continue
        if body.mode == "profile" or (body.mode == "auto" and video_catalog.is_profile(item)):
            try:catalogs.append(_start_catalog(item, me, batch_id))
            except HTTPException:rejected.append(item)
            continue
        kind = _kind_of(item)
        if body.mode == "media" and kind in ("cloud", "file") and not re.search(r'\.(?:mp4|mov|webm|m4v|mkv|jpe?g|png|webp|gif)(?:[?#]|$)', item, re.I):
            rejected.append(item);continue
        if body.mode == "cloud" and not cloud_backend.provider(item):
            rejected.append(item)
            continue
        if body.mode == "file" and kind != "cloud":
            kind = "file"
        if kind is None:
            rejected.append(item)
            continue

        with _lock:
            # 同一个人重复提交还在排队/下载中的同一网盘/文件链接：直接返回原任务，不再占第二个通道
            same = next((j for j in JOBS.values() if kind in ("cloud", "file") and j.owner == me.device and j.url == item and j.status in ("queued", "running") and not j.cancelled()), None)
        if same:
            existing.append(same.public())
            continue
        job = Job(id=uuid.uuid4().hex[:12], url=item, kind=kind, owner=me.device, owner_label=_device_label(ua), batch_id=batch_id)
        with _lock:
            JOBS[job.id] = job
        task_history.remember(job)
        threading.Thread(target=_worker, args=(job,), daemon=True).start()
        created.append(job.public())

    if not created and not catalogs and existing:
        return {"created": [], "existing": existing, "rejected": rejected, "catalogs": []}
    if not created and not catalogs:
        raise HTTPException(
            400,
            "识别失败：当前页面不接受这个链接类型。视频图片提取请提供单个作品；主页请使用主页 / 合集页面。" if body.mode == "media" else "没有识别出可用链接。请粘贴 Symphony 分享链接/UUID，"
            "或以 http(s):// 开头的视频链接，一行一个。",
        )

    for cat in ("video", "cloud"):
        stats.add_usage(cat, links=sum(1 for j in created if stats.category_of(j.get("kind", "")) == cat))
    stats.add_usage("profile", links=len(catalogs))
    return {"created": created, "existing": existing, "rejected": rejected, "catalogs": catalogs}


@app.get("/api/jobs")
def list_jobs(request: Request,response: Response,scope: str = "browser", me: Identity = Depends(current_identity)):
    with _lock:
        jobs = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
    rows=[j.public() for j in jobs if (j.owner == me.device or (scope == "all" and me.is_admin)) and task_history.retained(j.status,j.completed_at,j.created)]
    fingerprint=json.dumps([me.device,me.is_admin,scope,rows],sort_keys=True,separators=(',',':'))
    etag='"'+hashlib.sha256(fingerprint.encode()).hexdigest()+'"'
    headers={'ETag':etag,'Cache-Control':'private, no-cache','Vary':'Cookie'}
    if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
    response.headers.update(headers)
    return rows



@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, me: Identity = Depends(current_identity)) -> dict:
    """取消一个任务。已下载的保留，没下的不再下。"""
    job = _get_job(job_id, me)
    job.stop.set()
    return {"ok": True}


@app.post("/api/jobs/cancel-all")
def cancel_all(scope: str = "browser", me: Identity = Depends(current_identity)) -> dict:
    """一次取消自己所有正在下载和排队中的任务。
    批量贴错 30 个链接时，一个个点太痛苦。"""
    n = 0
    with _lock:
        jobs = list(JOBS.values())
    for j in jobs:
        if (j.owner == me.device or (scope == "all" and me.is_admin)) and j.status in ("queued", "running"):
            j.stop.set()
            n += 1
    return {"cancelled": n}


class RemoveFiles(BaseModel):
    names: list[str]
    purge: bool = False

@app.post("/api/jobs/{job_id}/remove-files")
def remove_files(job_id: str, body: RemoveFiles, me: Identity = Depends(current_identity)):
    names = list(dict.fromkeys(body.names))
    if not names or len(names) > 1000:
        raise HTTPException(400, "请选择 1 到 1000 个视频")
    with _lock:
        job = _get_job(job_id, me)
        known = {f.name for f in job.files if f.done}
        if any(n not in known for n in names):
            raise HTTPException(409, "列表已变化，请刷新后重试")
        removed, failed = [], []
        if body.purge:
            # Validate every path before making any destructive change.
            paths = [(n, _safe_file(job_id, n)) for n in names]
            for name, path in paths:
                try:
                    path.unlink(missing_ok=True)
                    removed.append(name)
                    relay_sync.discard(job_id, name)
                except OSError:
                    failed.append(name)
        else:
            _hide(job_id, names)  # One atomic write; videos remain untouched.
            removed = names
        gone = set(removed)
        job.files = [f for f in job.files if f.name not in gone]
        job.finished = sum(f.done for f in job.files)
        if not job.files and job.status not in ("queued", "running"):
            JOBS.pop(job_id, None)
            task_history.forget(job_id)
        else:
            _save_meta(job)
        return {"removed": removed, "failed": failed,
                "job": job.public() if job_id in JOBS else None}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, purge: bool = False,
               me: Identity = Depends(current_identity)) -> dict:
    """移除整个任务。purge=False 只清列表，视频留在电脑上。"""
    _get_job(job_id, me)          # 不是自己的就 404
    with _lock:
        job = JOBS.pop(job_id, None)
    if not job:
        raise HTTPException(404, "任务不存在")

    task_history.forget(job_id)
    if purge:
        shutil.rmtree(DOWNLOADS / job_id, ignore_errors=True)
        _unhide_all(job_id)
        relay_sync.discard(job_id)
    else:
        _hide(job_id, [f.name for f in job.files])
    return {"ok": True}


def _safe_file(job_id: str, name: str) -> Path:
    # 防目录穿越：解析后必须仍在该任务目录内。
    # 用 relative_to 而不是字符串前缀比较——前缀比较会把 downloads/abcd
    # 误判成在 downloads/abc 里面（"abcd" 确实以 "abc" 开头）。
    base = (DOWNLOADS / job_id).resolve()
    target = (base / name).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(404, "文件不存在") from None
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    return target


@app.post("/api/reveal/{job_id}/{name}")
def reveal_file(job_id: str, name: str, request: Request,
                me: Identity = Depends(current_identity)) -> dict:
    """在资源管理器里打开文件所在目录并选中它。

    只允许本机调用：文件在服务端这台电脑上，别的设备点了也只会
    在服务端弹出窗口，对点的人毫无意义，还会打扰到电脑前的人。
    """
    if not is_local(request):
        raise HTTPException(403, "只能在运行下载器的这台电脑上使用")

    _get_job(job_id, me)
    path = _safe_file(job_id, name)
    try:
        # /select 后面不能有空格，否则 explorer 会把整个盘根目录打开
        subprocess.Popen(["explorer", f"/select,{path}"])
    except OSError as e:
        raise HTTPException(500, f"打不开资源管理器：{e}") from e
    return {"ok": True}


@app.get("/api/files/{job_id}/{name}")
def get_file(job_id: str, name: str, inline: bool = False, me: Identity = Depends(current_identity)):
    _get_job(job_id, me)
    return FileResponse(_safe_file(job_id, name), filename=name,
                        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                        content_disposition_type="inline" if inline and Path(name).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".jpg", ".jpeg", ".png", ".webp", ".gif"} else "attachment")


# iPhone 快捷指令没有本站 cookie：发 2 小时有效的签名链接（优先国内中转，未同步完则走主机）
_SIGN_KEY = secrets.token_bytes(32)
SIGNED_TTL = 7200


def _sign(job_id: str, name: str, exp: int) -> str:
    import hmac
    return hmac.new(_SIGN_KEY, f"{job_id}\n{name}\n{exp}".encode(), hashlib.sha256).hexdigest()[:32]


class ToolRequest(BaseModel):
    op: Literal["trim", "convert", "md5", "audio"]
    items: list[dict]
    params: dict = {}


TOOL_NAMES = {"trim": "批量剪辑", "convert": "格式转换", "md5": "修改 MD5", "audio": "视频转音频"}


def _run_tool(job: Job) -> None:
    out = _job_dir(job.id)
    p = getattr(job, "tool_params", {}) or {}
    sources = getattr(job, "tool_sources", [])
    job.total = len(sources)
    notes = []
    for i, src in enumerate(sources, 1):
        if job.cancelled():
            raise Cancelled()
        job.progress = (i - 1) / max(1, len(sources))
        job.message = f"({i}/{len(sources)}) {src.name}"
        try:
            if job.url == "tool:trim":
                res = media_tools.trim(src, out, head=float(p.get("head") or 0), tail=float(p.get("tail") or 0),
                                       start=p.get("start"), end=p.get("end"))
            elif job.url == "tool:convert":
                res = media_tools.convert(src, out, str(p.get("format") or "mp4"))
            elif job.url == "tool:md5":
                res = media_tools.remd5(src, out)
                notes.append(f"{res.name}：{media_tools.md5_of(src)[:8]}… → {media_tools.md5_of(res)[:8]}…")
            else:
                res = media_tools.to_audio(src, out, str(p.get("format") or "mp3"))
            job.files.append(FileItem(res.name, res.stat().st_size, True))
            job.finished += 1
        except Exception as error:  # noqa: BLE001
            job.failed += 1
            job.failed_entries[src.name] = str(error)[:500]
            job.message = f"{src.name} 失败：{error}"
    job.progress = 1.0
    if notes:
        job.cloud_note = "MD5 已改变（画面音质不变）"


@app.post("/api/tools")
def run_tool(body: ToolRequest, request: Request, me: Identity = Depends(current_identity)) -> dict:
    if not body.items:
        raise HTTPException(400, "请先勾选文件")
    if len(body.items) > 200:
        raise HTTPException(400, "一次最多 200 个文件")
    sources: list[Path] = []
    for item in body.items:
        job_id, name = str(item.get("job_id", "")), str(item.get("name", ""))
        _get_job(job_id, me)
        sources.append(_safe_file(job_id, name))
    media = media_parts.VIDEO_EXT | {".m4a", ".mp3", ".opus", ".aac", ".wav"}
    if body.op in ("trim", "convert", "audio"):
        sources = [s for s in sources if s.suffix.lower() in (media_parts.VIDEO_EXT if body.op != "audio" else media)]
        if not sources:
            raise HTTPException(400, "选中的文件里没有视频")
    if body.op == "convert" and str(body.params.get("format", "")).lower() not in media_tools.VIDEO_FORMATS:
        raise HTTPException(400, "请选择目标格式")
    if body.op == "audio" and str(body.params.get("format", "mp3")).lower() not in media_tools.AUDIO_FORMATS:
        raise HTTPException(400, "请选择音频格式")
    if body.op == "trim":
        try:
            vals = [float(body.params.get(k) or 0) for k in ("head", "tail")]
        except (TypeError, ValueError):
            raise HTTPException(400, "剪辑秒数要填数字") from None
        if min(vals) < 0 or not any(vals) and body.params.get("end") in (None, ""):
            raise HTTPException(400, "请填写要去掉的片头或片尾秒数")
    label = TOOL_NAMES[body.op]
    extra = {"convert": " → " + str(body.params.get("format", "")).upper(), "audio": " → " + str(body.params.get("format", "mp3")).upper()}.get(body.op, "")
    job = Job(id=uuid.uuid4().hex[:12], url="tool:" + body.op, kind="tool", owner=me.device,
              owner_label=_device_label(request.headers.get("user-agent", "")),
              title=f"{label}{extra} · {len(sources)} 个文件")
    job.tool_sources = sources
    job.tool_params = dict(body.params)
    with _lock:
        JOBS[job.id] = job
    task_history.remember(job)
    threading.Thread(target=_worker, args=(job,), daemon=True).start()
    return {"created": [job.public()]}


UPLOAD_EXT = media_parts.VIDEO_EXT | {".m4a", ".mp3", ".opus", ".aac", ".wav", ".flac"}
UPLOAD_MAX = 8 * 1024 ** 3


@app.post("/api/tools/upload")
async def tool_upload(request: Request, name: str, job: str = "", me: Identity = Depends(current_identity)) -> dict:
    """批量工具页：浏览器把本地视频一个个原样传上来（请求体就是文件本身），放进同一个“上传素材”任务里。"""
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(name or "").name).strip(" .")[:150]
    if not clean or Path(clean).suffix.lower() not in UPLOAD_EXT:
        raise HTTPException(400, "只支持视频或音频文件（mp4 / mov / mkv / webm / m4a / mp3 等）")
    if job:
        target = _get_job(job, me)
        if target.url != "tool:upload":
            raise HTTPException(400, "上传任务不对")
        if len(target.files) >= 200:
            raise HTTPException(400, "一次最多 200 个文件")
    else:
        target = Job(id=uuid.uuid4().hex[:12], url="tool:upload", kind="tool", status="running", owner=me.device,
                     owner_label=_device_label(request.headers.get("user-agent", "")), title="上传的素材")
        with _lock:
            JOBS[target.id] = target
    folder = _job_dir(target.id)
    stem, ext, n = Path(clean).stem, Path(clean).suffix, 1
    while (folder / clean).exists() or any(f.name == clean for f in target.files):
        n += 1
        clean = f"{stem} ({n}){ext}"
    part = folder / (clean + ".uploading")
    size = 0
    try:
        with part.open("wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > UPLOAD_MAX:
                    raise HTTPException(413, "单个文件不能超过 8 GB")
                fh.write(chunk)
        if not size:
            raise HTTPException(400, "文件是空的")
        part.replace(folder / clean)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    target.files.append(FileItem(clean, size, True))
    target.total = target.finished = len(target.files)
    target.status = "done"
    target.progress = 1.0
    target.title = f"上传的素材 · {len(target.files)} 个文件"
    target.message = f"已上传 {len(target.files)} 个"
    target.completed_at = time.time()
    _save_meta(target)
    return {"job_id": target.id, "name": clean, "size": size}


@app.get("/api/admin/stats")
def admin_stats(days: int = 14, me: Identity = Depends(admin_only)) -> dict:
    return stats.summary(days)


class ShortcutItems(BaseModel):
    items: list[dict]


@app.post("/api/shortcut-links")
def shortcut_links(body: ShortcutItems, me: Identity = Depends(current_identity)) -> dict:
    if not body.items or len(body.items) > 200:
        raise HTTPException(400, "请选择 1 到 200 个文件")
    urls = []
    for item in body.items:
        job_id, name = str(item.get("id", "")), str(item.get("name", ""))
        job = _get_job(job_id, me)
        path = _safe_file(job_id, name)
        if not any(f.name == name and f.done for f in job.files):
            raise HTTPException(404, "文件不存在")
        relay = relay_sync.link(job_id, name, path, job.owner) if relay_sync.enabled() else {}
        if relay.get("state") == "ready" and relay.get("url"):
            urls.append(relay["url"]); continue
        exp = int(time.time()) + SIGNED_TTL
        urls.append(f"/api/signed/{quote(job_id, safe='')}/{quote(name, safe='')}?e={exp}&s={_sign(job_id, name, exp)}")
    return {"urls": urls}


@app.get("/api/signed/{job_id}/{name}")
def signed_file(job_id: str, name: str, e: int = 0, s: str = ""):
    import hmac
    if e < time.time() or not hmac.compare_digest(s, _sign(job_id, name, e)):
        raise HTTPException(403, "链接已过期，请回网页重新点击")
    job = JOBS.get(job_id)
    if not job or not any(f.name == name and f.done for f in job.files):
        raise HTTPException(404, "文件不存在")
    return FileResponse(_safe_file(job_id, name), filename=name, headers={"Cache-Control": "private, no-store"},
                        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream")


@app.get("/api/files/{job_id}/{name}/link")
def file_link(job_id: str, name: str, me: Identity = Depends(current_identity)) -> dict:
    """Short-lived relay URL for the same original (same ownership checks as get_file)."""
    job = _get_job(job_id, me)
    path = _safe_file(job_id, name)
    fallback = f"/api/files/{quote(job_id, safe='')}/{quote(name, safe='')}"
    if not relay_sync.enabled() or not any(f.name == name and f.done for f in job.files):
        return {"state": "disabled", "fallback": fallback}
    return {**relay_sync.link(job_id, name, path, job.owner), "fallback": fallback}


@app.delete("/api/files/{job_id}/{name}")
def delete_file(job_id: str, name: str, purge: bool = False,
                me: Identity = Depends(current_identity)) -> dict:
    """从列表移除一个文件。

    purge=False（默认）：只是不再显示，文件留在电脑上。
    purge=True：把电脑上的文件也删掉，不可恢复。
    """
    _get_job(job_id, me)
    path = _safe_file(job_id, name)
    if purge:
        path.unlink(missing_ok=True)
        relay_sync.discard(job_id, name)
    else:
        _hide(job_id, [name])

    job = JOBS.get(job_id)
    if job:
        job.files = [f for f in job.files if f.name != name]
        job.finished = len(job.files)
        if not job.files and job.status in ("done", "partial", "error"):
            with _lock:
                JOBS.pop(job_id, None)
                task_history.forget(job_id)
            # 只有在确实清理了文件的情况下才删目录；
            # 否则目录里还躺着用户要留的视频，删了就真没了
            if purge:
                shutil.rmtree(DOWNLOADS / job_id, ignore_errors=True)
                _unhide_all(job_id)
        else:
            _save_meta(job)
    return {"ok": True}



# Thumbnails are derived files; originals are never rewritten.
_thumb_lock = threading.Lock()
THUMBS = ROOT / "data" / "thumbnails"

@app.get("/api/thumb/{job_id}/{name}")
def thumbnail(job_id: str, name: str, me: Identity = Depends(current_identity)):
    job = _get_job(job_id, me)
    if not any(f.name == name and f.done for f in job.files):
        raise HTTPException(404, "文件不存在")
    source = _safe_file(job_id, name)
    stat = source.stat()
    digest = hashlib.sha256(f"{source}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    target = THUMBS / (digest + ".jpg")
    with _thumb_lock:
        if not target.exists():
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                matches = list((Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages").glob("yt-dlp.FFmpeg*/ffmpeg*/bin/ffmpeg.exe"))
                ffmpeg = str(matches[0]) if matches else None
            if not ffmpeg:
                raise HTTPException(503, "未找到缩略图组件")
            THUMBS.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(source),
                                "-frames:v", "1", "-vf", "scale=160:-2", str(target)],
                               check=True, timeout=30, capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except (OSError, subprocess.SubprocessError):
                target.unlink(missing_ok=True)
                raise HTTPException(422, "无法生成视频缩略图") from None
    return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# Resolve fresh CDN addresses only for files owned by this browser (or admin).
_direct_cache = {}
_direct_lock = threading.Lock()

@app.get("/api/direct/{job_id}/{name}")
def direct_source(job_id: str, name: str, response: Response,
                  me: Identity = Depends(current_identity)):
    response.headers["Cache-Control"] = "no-store"
    job = _get_job(job_id, me)
    if not any(f.name == name and f.done for f in job.files):
        raise HTTPException(404, "文件不存在")
    _safe_file(job_id, name)
    if job.kind != "symphony":
        return {"available": False, "reason": "该来源暂不支持平台直连"}
    with _direct_lock:
        entry = _direct_cache.get(job_id)
    if not entry or entry[0] < time.monotonic():
        try:
            assets = symphony.list_assets(job.url)
        except Exception:
            return {"available": False, "reason": "分享链接暂不可用，使用主机文件"}
        sources = {a.filename: a.url for a in assets}
        with _direct_lock:
            # Bound memory and never persist signed CDN addresses.
            if len(_direct_cache) >= 128:
                _direct_cache.clear()
            _direct_cache[job_id] = (time.monotonic() + 120, sources)
    else:
        sources = entry[1]
    url = sources.get(name, "")
    from urllib.parse import urlsplit
    if urlsplit(url).scheme != "https":
        return {"available": False, "reason": "无可用的 HTTPS 平台地址"}
    return {"available": True, "url": url}

import cloud_accounts
_cloud_connector = None
_cloud_connector_lock = threading.Lock()

@app.get('/api/admin/cloud-accounts')
def cloud_account_list(me: Identity = Depends(admin_only)):
    cloud_accounts.import_existing()
    return cloud_accounts.listing()

class CloudSelection(BaseModel):
    id: str

@app.get('/api/cloud/status')
def cloud_status():
    return cloud_accounts.public_status()

@app.get('/api/admin/cloud-transfers')
def cloud_transfer_list(me: Identity = Depends(admin_only)):
    return cloud_cleanup.listing()

@app.post('/api/admin/cloud-transfers/retry')
def cloud_transfer_retry(body: CloudSelection, me: Identity = Depends(admin_only)):
    try:
        result=cloud_cleanup.retry(body.id)
        if result['ok']:
            job=JOBS.get(body.id)
            if job:
                job.cloud_note=result['message'];job.message=f"完成 {job.finished} 个；"+job.cloud_note;_save_meta(job)
        return result
    except (ValueError,OSError):
        raise HTTPException(409,'未满足清理条件：请确认完整文件仍在主机、原转存账号有效且没有进行中的下载') from None

@app.post('/api/admin/cloud-accounts/check')
def cloud_account_check(body: CloudSelection, me: Identity = Depends(admin_only)):
    try: return cloud_accounts.verify(body.id)
    except ValueError as error: raise HTTPException(409, str(error))
    except Exception: raise HTTPException(409, '账号检测暂时失败，请检查主机网络后重试')

@app.post('/api/admin/cloud-accounts/activate')
def cloud_account_activate(body: CloudSelection, me: Identity = Depends(admin_only)):
    try: cloud_accounts.activate(body.id)
    except ValueError as error: raise HTTPException(409,str(error))
    return {'ok':True}

class CloudConnect(BaseModel):
    provider: Literal['baidu','ali','quark','uc','pan123','tianyi','mobile','xunlei']

@app.post('/api/admin/cloud-accounts/connect')
def cloud_account_connect(body: CloudConnect, request: Request, me: Identity = Depends(admin_only)):
    global _cloud_connector
    if not request.client or request.client.host not in ('127.0.0.1','::1'):
        raise HTTPException(403,'请在主机打开 http://127.0.0.1:8000/ 的网盘账号区域扫码连接')
    with _cloud_connector_lock:
        if _cloud_connector is not None and _cloud_connector.poll() is None:
            raise HTTPException(409,'已有授权窗口打开，请先完成或关闭该窗口')
        import sys
        pythonw=Path(sys.executable).with_name('pythonw.exe')
        _cloud_connector=subprocess.Popen([str(pythonw),str(ROOT/'connect_cloud.py'),body.provider],cwd=str(ROOT),creationflags=subprocess.CREATE_NO_WINDOW)
    return {'ok':True,'message':'主机已打开官方授权窗口，请扫码后点击保存账号'}


@app.get('/api/admin/video-accounts')
def video_account_status(me: Identity = Depends(admin_only)):
    return video_accounts.status()


@app.get('/api/admin/video-accounts/qr')
def video_account_qr(me: Identity = Depends(admin_only)):
    if not video_accounts.status().get('qr_available'):raise HTTPException(404,'二维码未生成或已过期，请重新连接')
    return FileResponse(video_accounts.STORE.parent/'qr-login.png',media_type='image/png',headers={'Cache-Control':'no-store'})

@app.post('/api/admin/video-accounts/check')
def video_account_check(me: Identity = Depends(admin_only)):
    try:return video_accounts.check()
    except ValueError as error:raise HTTPException(409,str(error)) from None
    except Exception:raise HTTPException(409,'视频号官方接口暂时无法连接，请稍后重试') from None

@app.post('/api/admin/video-accounts/connect')
def video_account_connect(request: Request,me: Identity = Depends(admin_only)):
    global _cloud_connector
    if not request.client or request.client.host not in ('127.0.0.1','::1'):
        raise HTTPException(403,'请在下载器主机原首页扫码连接视频号')
    with _cloud_connector_lock:
        if _cloud_connector is not None and _cloud_connector.poll() is None:
            raise HTTPException(409,'已有授权窗口，请先完成或关闭')
        import sys
        pythonw=Path(sys.executable).with_name('pythonw.exe')
        _cloud_connector=subprocess.Popen([str(pythonw),str(ROOT/'connect_yuanbao_qr.py')],cwd=str(ROOT),creationflags=subprocess.CREATE_NO_WINDOW)
    return {'ok':True,'message':'正在生成元宝官方二维码，请在本页扫码，并在微信确认登录'}


@app.get('/api/admin/platform-accounts')
def platform_account_status(me: Identity = Depends(admin_only)):
    import video_platform_accounts as accounts
    return {'accounts': [accounts.status(provider) for provider in accounts.PROVIDERS]}

class VideoPlatformConnectBody(BaseModel):
    provider: str

@app.post('/api/admin/platform-accounts/connect')
def platform_account_connect(body: VideoPlatformConnectBody, request: Request, me: Identity = Depends(admin_only)):
    import video_platform_accounts as accounts
    global _cloud_connector
    if body.provider not in accounts.PROVIDERS:
        raise HTTPException(400, '不支持的账号类型')
    if not request.client or request.client.host not in ('127.0.0.1', '::1'):
        raise HTTPException(403, '请在下载器主机的原首页连接官方账号')
    with _cloud_connector_lock:
        if _cloud_connector is not None and _cloud_connector.poll() is None:
            raise HTTPException(409, '已有授权窗口，请先完成或关闭')
        import sys
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        _cloud_connector = subprocess.Popen([str(pythonw), str(ROOT/'connect_video_platform.py'), body.provider], cwd=str(ROOT), creationflags=subprocess.CREATE_NO_WINDOW)
    return {'ok': True, 'message': '已打开官方网页。请完成登录和验证，然后点击小窗口保存授权。'}

from compressed_static import PrecompressedStaticFiles
app.mount("/", PrecompressedStaticFiles(directory=STATIC, html=True), name="static")
