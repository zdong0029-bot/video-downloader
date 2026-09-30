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

ROOT = Path(__file__).parent
DOWNLOADS = ROOT / "downloads"
STATIC = ROOT / "static"
COOKIES = ROOT / "cookies.txt"      # 可选：抖音等站点需要
DOWNLOADS.mkdir(exist_ok=True)

# 同时下载的任务数。家用场景不需要开太大，避免把上行带宽和代理打满。
MAX_WORKERS = 2
_slots = threading.Semaphore(MAX_WORKERS)


@dataclass
class FileItem:
    name: str
    size: int = 0
    done: bool = False


@dataclass
class Job:
    id: str
    url: str
    kind: Literal["symphony", "douyin", "web", "file", "cloud"]
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
    owner_label: str = ""    # 人类可读的设备名，只给管理员看
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancelled(self) -> bool:
        return self.stop.is_set()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "message": self.message,
            "progress": round(self.progress, 4),
            "total": self.total,
            "finished": self.finished,
            "failed": self.failed,
            "created": self.created,
            "owner": (self.owner_label + " · " + self.owner[:6]) if self.owner_label else self.owner[:8],
            "files": [{"name": f.name, "size": f.size} for f in self.files if f.done],
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
    try:
        (DOWNLOADS / job.id / META).write_text(json.dumps({
            "id": job.id, "url": job.url, "kind": job.kind,
            "title": job.title, "created": job.created, "owner": job.owner, "owner_label": job.owner_label,
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

        junk = {".part", ".ytdl", ".tmp", ".temp", ".json"}
        on_disk = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() not in junk]
        if not on_disk:
            shutil.rmtree(d, ignore_errors=True)   # 空目录（多半是失败的任务）直接清掉
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
        )
        job.files = [FileItem(p.name, p.stat().st_size, True) for p in sorted(files)]
        job.total = job.finished = len(job.files)
        job.progress = 1.0
        job.message = f"共 {len(job.files)} 个"
        JOBS[job.id] = job


# ----------------------------------------------------------------- 下载逻辑

def _firefox_profile() -> bool:
    """有没有可用的 Firefox 配置文件。没装 Firefox 时直接跳过，
    否则 yt-dlp 会因为找不到 cookie 库而让整个任务失败。"""
    base = Path(os.environ.get("APPDATA", "")) / "Mozilla/Firefox/Profiles"
    if not base.is_dir():
        return False
    return any((p / "cookies.sqlite").is_file() for p in base.iterdir() if p.is_dir())


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
    video = douyin.resolve(job.url)

    job.title = video.title
    job.message = f"{video.width}x{video.height}"
    out = _job_dir(job.id)
    path = out / douyin.safe_filename(video)

    douyin.download(video, path, on_progress=lambda p: setattr(job, "progress", p),
                    should_stop=job.cancelled)
    job.files.append(FileItem(path.name, path.stat().st_size, True))
    job.finished = 1


def _run_web(job: Job) -> None:
    out = _job_dir(job.id)

    def hook(d: dict) -> None:
        # yt-dlp 没有取消 API，只能在进度回调里抛异常把它打断
        if job.cancelled():
            raise Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job.progress = min(1.0, (d.get("downloaded_bytes") or 0) / total)
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
        "merge_output_format": "mp4",
        "noplaylist": True,
        "windowsfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }

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
    elif _firefox_profile():
        opts["cookiesfrombrowser"] = ("firefox", None, None, None)

    job.total = 1
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(job.url, download=True)
        job.title = info.get("title") or job.url

    # 以磁盘上的成品为准。中途失败会留下 .part / .ytdl，那些不是成品，
    # 不能算成功——否则一个下到一半的大文件会被报成"下载完成"。
    junk = {".part", ".ytdl", ".tmp", ".temp", ".json"}
    got = [p for p in out.iterdir() if p.is_file() and p.suffix.lower() not in junk]
    if not got:
        raise RuntimeError("下载结束但没有产出文件")
    for p in got:
        job.files.append(FileItem(p.name, p.stat().st_size, True))
    job.finished = len(got)


def _drop_empty_cancelled(job: Job) -> None:
    """取消且一个文件都没下到的任务，从列表和磁盘上一并清掉，不留空壳。"""
    job.status = "cancelled"
    job.message = "已取消"
    with _lock:
        JOBS.pop(job.id, None)
    shutil.rmtree(DOWNLOADS / job.id, ignore_errors=True)


def _worker(job: Job) -> None:
    # 排队等位时也要能取消——30 个任务里只有 2 个在跑，其余都卡在这里
    if job.cancelled():
        _drop_empty_cancelled(job)
        return

    with _slots:
        if job.cancelled():
            _drop_empty_cancelled(job)
            return
        job.status = "running"
        try:
            if job.kind == "symphony":
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
                if file_download.provider(job.url) != "百度网盘":
                    raise ValueError("该网盘尚未接入，请勿当作视频链接下载")
                job.message = "正在解析百度文件，等待下载通道…"
                def cloud_progress(items, paths):
                    total = sum(int(item.get("size", 0)) for item in items)
                    current = sum(p.stat().st_size for p in paths if p.is_file() and p.name not in (META, ".cloud-incomplete") and not p.name.endswith(".BaiduPCS-Go-downloading"))
                    job.total = len(items)
                    job.progress = min(current / total, 0.99) if total else 0
                    job.message = f"已写入 {current / 1048576:.1f} MB / {total / 1048576:.1f} MB（完成后校验原文件大小）"
                cloud_dir = _job_dir(job.id)
                (cloud_dir / ".cloud-incomplete").touch()
                paths = baidu_backend.run(job.url, "", cloud_dir, cloud_progress, job.cancelled)
                job.files = [FileItem(p.name, p.stat().st_size, True) for p in paths]
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
                job.message = f"完成 {job.finished} 个"
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

        if job.files:
            _save_meta(job)
            if job.kind == "cloud" and job.status == "done" and (DOWNLOADS / job.id / META).is_file():
                (DOWNLOADS / job.id / ".cloud-incomplete").unlink(missing_ok=True)
        elif job.status == "cancelled":
            # 一个文件都没下到的取消任务，直接从列表里拿掉，不留空壳
            with _lock:
                JOBS.pop(job.id, None)
            shutil.rmtree(DOWNLOADS / job.id, ignore_errors=True)


# ----------------------------------------------------------------- API

app = FastAPI(title="局域网视频下载器")

_restore_jobs()

COOKIE = "sid"        # 管理员会话
DEVICE = "did"        # 设备/浏览器标识


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
    mode: Literal["auto", "file"] = "auto"


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
        if file_download.provider(u) == "百度网盘":
            from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
            parts = urlsplit(u)
            query = parse_qsl(parts.query, keep_blank_values=True)
            # Associate only the text following this link, before the next link.
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            code = re.search(r'(?:提取码|提取碼|访问码)\s*[:：]?\s*([A-Za-z0-9]{4})(?![A-Za-z0-9])', text[m.end():end])
            if code and not any(k == "pwd" and v for k, v in query):
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

    # 同一批里重复粘贴的只建一个任务
    seen: set[str] = set()
    created: list[dict] = []
    rejected: list[str] = []

    for item in items:
        if item in seen:
            continue
        seen.add(item)

        kind = _kind_of(item)
        if body.mode == "file" and kind != "cloud":
            kind = "file"
        if kind is None:
            rejected.append(item)
            continue

        job = Job(id=uuid.uuid4().hex[:12], url=item, kind=kind, owner=me.device, owner_label=_device_label(ua))
        with _lock:
            JOBS[job.id] = job
        threading.Thread(target=_worker, args=(job,), daemon=True).start()
        created.append(job.public())

    if not created:
        raise HTTPException(
            400,
            "没有识别出可用链接。请粘贴 Symphony 分享链接/UUID，"
            "或以 http(s):// 开头的视频链接，一行一个。",
        )

    return {"created": created, "rejected": rejected}


@app.get("/api/jobs")
def list_jobs(scope: str = "browser", me: Identity = Depends(current_identity)) -> list[dict]:
    with _lock:
        jobs = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
    return [j.public() for j in jobs if j.owner == me.device or (scope == "all" and me.is_admin)]


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

    if purge:
        shutil.rmtree(DOWNLOADS / job_id, ignore_errors=True)
        _unhide_all(job_id)
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
                        content_disposition_type="inline" if inline and Path(name).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav"} else "attachment")


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
    else:
        _hide(job_id, [name])

    job = JOBS.get(job_id)
    if job:
        job.files = [f for f in job.files if f.name != name]
        job.finished = len(job.files)
        if not job.files and job.status in ("done", "partial", "error"):
            with _lock:
                JOBS.pop(job_id, None)
            # 只有在确实清理了文件的情况下才删目录；
            # 否则目录里还躺着用户要留的视频，删了就真没了
            if purge:
                shutil.rmtree(DOWNLOADS / job_id, ignore_errors=True)
                _unhide_all(job_id)
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

@app.post('/api/admin/cloud-accounts/activate')
def cloud_account_activate(body: CloudSelection, me: Identity = Depends(admin_only)):
    try: cloud_accounts.activate(body.id)
    except ValueError as error: raise HTTPException(409,str(error))
    return {'ok':True}

class CloudConnect(BaseModel):
    provider: Literal['baidu','ali','quark']

@app.post('/api/admin/cloud-accounts/connect')
def cloud_account_connect(body: CloudConnect, request: Request, me: Identity = Depends(admin_only)):
    global _cloud_connector
    if not request.client or request.client.host not in ('127.0.0.1','::1'):
        raise HTTPException(403,'请在主机打开 http://127.0.0.1:8000/cloud-accounts.html 扫码连接')
    with _cloud_connector_lock:
        if _cloud_connector is not None and _cloud_connector.poll() is None:
            raise HTTPException(409,'已有授权窗口打开，请先完成或关闭该窗口')
        import sys
        pythonw=Path(sys.executable).with_name('pythonw.exe')
        _cloud_connector=subprocess.Popen([str(pythonw),str(ROOT/'connect_cloud.py'),body.provider],cwd=str(ROOT),creationflags=subprocess.CREATE_NO_WINDOW)
    return {'ok':True,'message':'主机已打开官方授权窗口，请扫码后点击保存账号'}

app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
