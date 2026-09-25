"""局域网视频下载器 —— FastAPI 后端。

家里人在同一个路由器下打开 http://<本机IP>:8000 就能用，
粘贴链接 -> 服务端下载 -> 浏览器取走文件。他们不需要装任何东西，也不需要配代理：
去 TikTok 取视频这一步走的是本机的网络（含本机代理），
从本机传到手机那一步是纯局域网。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yt_dlp
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import symphony

ROOT = Path(__file__).parent
DOWNLOADS = ROOT / "downloads"
STATIC = ROOT / "static"
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
    kind: Literal["symphony", "web"]
    status: Literal["queued", "running", "done", "error", "partial"] = "queued"
    title: str = ""
    message: str = ""
    progress: float = 0.0          # 0~1，当前文件
    total: int = 0                 # 素材总数
    finished: int = 0
    failed: int = 0
    files: list[FileItem] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    owner: str = ""          # 提交这个任务的账号，用于隔离各人的记录

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
            "owner": self.owner,
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


def _save_hidden(data: dict[str, list[str]]) -> None:
    try:
        HIDDEN.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _hide(job_id: str, names: list[str]) -> None:
    data = _load_hidden()
    cur = set(data.get(job_id, []))
    cur.update(names)
    data[job_id] = sorted(cur)
    _save_hidden(data)


def _unhide_all(job_id: str) -> None:
    data = _load_hidden()
    if data.pop(job_id, None) is not None:
        _save_hidden(data)


def _save_meta(job: Job) -> None:
    """把任务信息落盘。不存的话，服务一重启（开机自启那次也算）列表就空了，
    磁盘上的视频全变成看不见的孤儿文件，既占空间又找不回来。"""
    try:
        (DOWNLOADS / job.id / META).write_text(json.dumps({
            "id": job.id, "url": job.url, "kind": job.kind,
            "title": job.title, "created": job.created, "owner": job.owner,
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
        )
        job.files = [FileItem(p.name, p.stat().st_size, True) for p in sorted(files)]
        job.total = job.finished = len(job.files)
        job.progress = 1.0
        job.message = f"共 {len(job.files)} 个"
        JOBS[job.id] = job


# ----------------------------------------------------------------- 下载逻辑

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
        job.message = f"({i}/{len(assets)}) {asset.name}"
        job.progress = 0.0
        try:
            path = symphony.download_asset(
                asset, out, on_progress=lambda p: setattr(job, "progress", p)
            )
            job.files.append(FileItem(path.name, path.stat().st_size, True))
            job.finished += 1
        except Exception as e:  # noqa: BLE001
            job.failed += 1
            job.message = f"{asset.name} 失败：{e}"


def _run_web(job: Job) -> None:
    out = _job_dir(job.id)

    def hook(d: dict) -> None:
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


def _worker(job: Job) -> None:
    with _slots:
        job.status = "running"
        try:
            if job.kind == "symphony":
                _run_symphony(job)
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
        except Exception as e:  # noqa: BLE001
            job.status = "error"
            job.message = str(e)

        if job.files:
            _save_meta(job)


# ----------------------------------------------------------------- API

app = FastAPI(title="局域网视频下载器")

_restore_jobs()

COOKIE = "sid"


def current_user(sid: str | None = Cookie(default=None)) -> auth.User:
    u = auth.user_of_token(sid)
    if not u:
        raise HTTPException(401, "请先登录")
    return u


def admin_only(user: auth.User = Depends(current_user)) -> auth.User:
    if not user.is_admin:
        raise HTTPException(403, "只有管理员能操作")
    return user


def _owned(job: Job, user: auth.User) -> bool:
    """管理员能看所有人的；普通用户只能看自己的。
    owner 为空是登录功能上线前留下的历史记录，只对管理员可见。"""
    return user.is_admin or job.owner == user.name


def _get_job(job_id: str, user: auth.User) -> Job:
    job = JOBS.get(job_id)
    if not job or not _owned(job, user):
        # 不区分"不存在"和"不是你的"，避免被人探测别人有哪些任务
        raise HTTPException(404, "任务不存在")
    return job


# ----------------------------------------------------------------- 登录

class Creds(BaseModel):
    username: str
    password: str


@app.get("/api/me")
def me(sid: str | None = Cookie(default=None)) -> dict:
    """前端启动时先问这个：要不要显示初始化页 / 登录页 / 主界面。"""
    if not auth.has_users():
        return {"setup": True, "user": None}
    u = auth.user_of_token(sid)
    if not u:
        return {"setup": False, "user": None}
    return {"setup": False, "user": {"name": u.name, "is_admin": u.is_admin}}


@app.post("/api/setup")
def setup(body: Creds, response: Response) -> dict:
    """首次使用：创建第一个管理员账号。已有账号后这个接口就关闭了。"""
    if auth.has_users():
        raise HTTPException(400, "已经初始化过了")
    try:
        auth.create_user(body.username, body.password, is_admin=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    token = auth.start_session(body.username.strip())
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=auth.SESSION_DAYS * 86400)
    return {"ok": True}


@app.post("/api/login")
def login(body: Creds, response: Response) -> dict:
    u = auth.authenticate(body.username, body.password)
    if not u:
        raise HTTPException(401, "用户名或密码不对")
    token = auth.start_session(u.name)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=auth.SESSION_DAYS * 86400)
    return {"user": {"name": u.name, "is_admin": u.is_admin}}


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
def list_users(user: auth.User = Depends(admin_only)) -> list[dict]:
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
def add_user(body: NewUser, user: auth.User = Depends(admin_only)) -> dict:
    try:
        auth.create_user(body.username, body.password, body.is_admin)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.post("/api/users/{name}/password")
def change_password(name: str, body: NewPassword,
                    user: auth.User = Depends(admin_only)) -> dict:
    try:
        auth.set_password(name, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.delete("/api/users/{name}")
def remove_user(name: str, user: auth.User = Depends(admin_only)) -> dict:
    if name == user.name:
        raise HTTPException(400, "不能删除自己")
    try:
        auth.delete_user(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


class NewJob(BaseModel):
    url: str


def _kind_of(url: str) -> Literal["symphony", "web"] | None:
    if symphony.extract_share_id(url):
        return "symphony"
    if url.startswith(("http://", "https://")):
        return "web"
    return None


@app.post("/api/jobs")
def create_job(body: NewJob, user: auth.User = Depends(current_user)) -> dict:
    """支持一次粘贴多个链接：按空白字符（换行、空格）切开，逐个建任务。

    并发数由 MAX_WORKERS 控制，多出来的排队跑，不会一次性把带宽和代理打满。
    """
    raw = body.url or ""
    items = [s.strip().strip('"').strip("'") for s in raw.split()]
    items = [s for s in items if s]
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
        if kind is None:
            rejected.append(item)
            continue

        job = Job(id=uuid.uuid4().hex[:12], url=item, kind=kind, owner=user.name)
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
def list_jobs(user: auth.User = Depends(current_user)) -> list[dict]:
    with _lock:
        jobs = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
    return [j.public() for j in jobs if _owned(j, user)]


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, purge: bool = False,
               user: auth.User = Depends(current_user)) -> dict:
    """移除整个任务。purge=False 只清列表，视频留在电脑上。"""
    _get_job(job_id, user)          # 不是自己的就 404
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


@app.get("/api/files/{job_id}/{name}")
def get_file(job_id: str, name: str, user: auth.User = Depends(current_user)):
    _get_job(job_id, user)
    return FileResponse(_safe_file(job_id, name), filename=name,
                        media_type="application/octet-stream")


@app.delete("/api/files/{job_id}/{name}")
def delete_file(job_id: str, name: str, purge: bool = False,
                user: auth.User = Depends(current_user)) -> dict:
    """从列表移除一个文件。

    purge=False（默认）：只是不再显示，文件留在电脑上。
    purge=True：把电脑上的文件也删掉，不可恢复。
    """
    _get_job(job_id, user)
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


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
