"""Optional push of finished originals to the mainland relay; friends then fetch signed relay URLs.

Disabled unless RELAY_BASE_URL, RELAY_UPLOAD_TOKEN and RELAY_SIGNING_KEY are all set
(environment, or web/data/relay.json with keys base_url, upload_token, signing_key).
Uploads go direct (system/home proxy bypassed), in resumable chunks, and are verified by SHA-256.
"""
import hashlib, hmac, json, os, queue, threading, time
from pathlib import Path
from urllib.parse import quote
import requests
import task_history

CONFIG = Path(__file__).parent / "data" / "relay.json"
CHUNK = 8 * 1048576
LINK_TTL = 6 * 3600
MAX_FAILURES = 8          # consecutive attempts without any progress before marking failed
_lock = threading.RLock()
_state: dict[tuple[str, str], dict] = {}
_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
_paths: dict[tuple[str, str], tuple[Path, str]] = {}
_workers: list[threading.Thread] = []
_sleep = time.sleep

_cfg_cache = [0.0, None]

def config():
    if _cfg_cache[1] is not None and time.monotonic() - _cfg_cache[0] < 5: return _cfg_cache[1]
    saved = {}
    try: saved = json.loads(CONFIG.read_text("utf-8"))
    except (OSError, ValueError): pass
    pick = lambda env, key, default="": os.environ.get(env) or saved.get(key) or default
    cfg = {"base": pick("RELAY_BASE_URL", "base_url").rstrip("/"), "token": pick("RELAY_UPLOAD_TOKEN", "upload_token"),
           "signing": pick("RELAY_SIGNING_KEY", "signing_key"), "ca": pick("RELAY_CA_BUNDLE", "ca_bundle"),
           "workers": max(1, min(2, int(pick("RELAY_CONCURRENCY", "concurrency", "1"))))}
    _cfg_cache[:] = [time.monotonic(), cfg]; return cfg

def reload(): _cfg_cache[:] = [0.0, None]

def enabled(cfg=None):
    cfg = cfg or config(); return bool(cfg["base"] and cfg["token"] and cfg["signing"])

def key_for(owner, job_id, name):
    # The device cookie value is a credential; only a one-way tag of it goes into URLs.
    tag = hashlib.sha256(owner.encode()).hexdigest()[:16] if owner else "anon"
    return f"jobs/{tag}/{job_id}/{name}"

def signed_url(key, ttl=LINK_TTL, now=None, cfg=None):
    cfg = cfg or config(); exp = int((now or time.time()) + ttl)
    sig = hmac.new(cfg["signing"].encode(), f"{key}\n{exp}".encode(), hashlib.sha256).hexdigest()
    return cfg["base"] + "/f/" + quote(key, safe="/") + f"?exp={exp}&sig={sig}", exp

# ------------------------------------------------------------------ persisted status (beside task history)
def _db():
    ctx = task_history.connection(); db = ctx.__enter__()
    db.execute("CREATE TABLE IF NOT EXISTS relay_files (job_id TEXT, name TEXT, payload TEXT, updated REAL, PRIMARY KEY (job_id, name))")
    return ctx, db

def _save(job_id, name):
    row = _state.get((job_id, name))
    ctx, db = _db()
    try:
        if row is None: db.execute("DELETE FROM relay_files WHERE job_id=? AND name=?", (job_id, name))
        else: db.execute("INSERT OR REPLACE INTO relay_files VALUES (?,?,?,?)", (job_id, name, json.dumps(row, ensure_ascii=False), time.time()))
        db.execute("DELETE FROM relay_files WHERE updated < ?", (time.time() - task_history.RETENTION,))
    finally: ctx.__exit__(None, None, None)

def _load():
    ctx, db = _db()
    try: rows = db.execute("SELECT job_id, name, payload FROM relay_files WHERE updated >= ?", (time.time() - task_history.RETENTION,)).fetchall()
    finally: ctx.__exit__(None, None, None)
    return {(j, n): json.loads(p) for j, n, p in rows}

def _update(job_id, name, persist=True, **fields):
    with _lock:
        row = _state.setdefault((job_id, name), {}); row.update(fields)
        if persist: _save(job_id, name)
        return dict(row)

def status(job_id, name):
    """Small dict for the job list; None when the feature is disabled."""
    if not enabled(): return None
    row = _state.get((job_id, name))
    if not row: return {"state": "none"}
    out = {"state": row.get("state", "pending")}
    if out["state"] == "uploading" and row.get("size"): out["progress"] = int(row.get("sent", 0) * 100 / row["size"])
    return out

def link(job_id, name, path: Path, owner):
    """Signed relay URL when the stored copy matches the local original, else progress (and enqueue)."""
    row = _state.get((job_id, name))
    st = path.stat()
    if row and row.get("state") == "ready" and row.get("size") == st.st_size and row.get("mtime") == st.st_mtime_ns:
        url, exp = signed_url(row["key"])
        return {"state": "ready", "url": url, "expires": exp, "size": row["size"], "sha256": row["sha256"]}
    if not row or row.get("state") in ("failed", "ready") or row.get("size") not in (None, st.st_size):
        enqueue(job_id, name, path, owner, force=True); row = _state.get((job_id, name)) or {}
    out = {"state": row.get("state", "pending")}
    if out["state"] == "uploading" and row.get("size"): out["progress"] = int(row.get("sent", 0) * 100 / row["size"])
    if row.get("error"): out["error"] = row["error"]
    return out

# ------------------------------------------------------------------ queue
def enqueue(job_id, name, path: Path, owner, force=False):
    if not enabled(): return False
    k = (job_id, name)
    with _lock:
        row = _state.get(k)
        if row and row.get("state") in ("pending", "uploading") and k in _paths: return False
        if row and row.get("state") == "ready" and not force: return False
        _paths[k] = (Path(path), owner)
        _update(job_id, name, state="pending", key=key_for(owner, job_id, name), error="", sent=row.get("sent", 0) if row else 0)
    start(); _queue.put(k); return True

def discard(job_id, name=None):
    """Forget local status and best-effort delete the relay copy (used when originals are purged)."""
    with _lock:
        keys = [k for k in _state if k[0] == job_id and (name is None or k[1] == name)]
        rows = [(k, _state.pop(k)) for k in keys]
        for k in keys: _paths.pop(k, None); _save(*k)
    cfg = config()
    if not enabled(cfg): return
    def run():
        for _, row in rows:
            try: _session(cfg).delete(cfg["base"] + "/upload/" + quote(row["key"], safe="/"), headers=_auth(cfg), timeout=30)
            except (requests.RequestException, KeyError): pass
    threading.Thread(target=run, daemon=True).start()

def start():
    cfg = config()
    if not enabled(cfg): return
    with _lock:
        _workers[:] = [w for w in _workers if w.is_alive()]
        while len(_workers) < cfg["workers"]:
            w = threading.Thread(target=_worker, daemon=True, name="relay-sync"); w.start(); _workers.append(w)

def restore(known_path):
    """At startup: load statuses and resume unfinished/failed uploads. known_path(job_id, name) -> (Path, owner) or None."""
    if not enabled(): return
    with _lock: _state.update(_load())
    for (job_id, name), row in list(_state.items()):
        found = known_path(job_id, name)
        if not found:
            with _lock: _state.pop((job_id, name), None); _save(job_id, name)
        elif row.get("state") in ("pending", "uploading", "failed"):
            enqueue(job_id, name, found[0], found[1], force=True)

def _worker():
    while True:
        k = _queue.get()
        try:
            with _lock: target = _paths.get(k)
            if target and _state.get(k, {}).get("state") == "pending": upload(k[0], k[1], *target)
        except Exception as e:  # noqa: BLE001 - a background upload must never kill the worker
            _update(*k, state="failed", error=str(e)[:200])
        finally: _queue.task_done()

# ------------------------------------------------------------------ transfer
def _session(cfg):
    s = requests.Session(); s.trust_env = False   # never via HTTP(S)_PROXY / Windows system proxy
    s.proxies = {}; s.verify = cfg["ca"] or True
    return s

def _auth(cfg): return {"Authorization": "Bearer " + cfg["token"]}

def file_sha256(path, cached=None):
    st = path.stat()
    if cached and cached.get("size") == st.st_size and cached.get("mtime") == st.st_mtime_ns and cached.get("sha256"): return cached["sha256"]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1048576), b""): h.update(block)
    return h.hexdigest()

class _Retry(Exception): pass

def upload(job_id, name, path: Path, owner, cfg=None):
    cfg = cfg or config(); k = (job_id, name); key = key_for(owner, job_id, name)
    url = cfg["base"] + "/upload/" + quote(key, safe="/"); session = _session(cfg); auth = _auth(cfg)
    st = path.stat(); size = st.st_size
    digest = file_sha256(path, _state.get(k))
    _update(job_id, name, state="uploading", key=key, size=size, mtime=st.st_mtime_ns, sha256=digest, error="")
    failures, restarted, last_save = 0, False, 0.0
    while True:
        try:
            if path.stat().st_size != size: raise ValueError("本机原文件在同步中发生变化")
            head = session.head(url, headers=auth, timeout=30)
            if head.status_code in (401, 403): raise PermissionError("中转服务器拒绝上传令牌")
            if head.status_code >= 500: raise _Retry(f"HTTP {head.status_code}")
            head.raise_for_status()
            offset = int(head.headers.get("Upload-Offset", "0"))
            if head.headers.get("Upload-Complete") == "1":
                if offset == size and head.headers.get("X-Content-Sha256") == digest: break
                session.delete(url, headers=auth, timeout=30); raise _Retry("中转服务器上有旧版本，已请求删除")
            if offset > size:
                session.delete(url, headers=auth, timeout=30); raise _Retry("中转服务器分段大小异常，已请求删除")
            with open(path, "rb") as f:
                while offset < size:
                    f.seek(offset); data = f.read(min(CHUNK, size - offset))
                    r = session.put(url, data=data, timeout=(15, 120), headers={**auth, "Content-Type": "application/octet-stream",
                                    "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}"})
                    if r.status_code == 409: offset = int(r.headers.get("Upload-Offset", offset)); break
                    if r.status_code in (401, 403): raise PermissionError("中转服务器拒绝上传令牌")
                    if r.status_code in (413, 507): raise OSError(f"中转服务器拒收（HTTP {r.status_code}）")
                    if r.status_code != 200: raise _Retry(f"HTTP {r.status_code}")
                    offset = int(r.headers.get("Upload-Offset", offset + len(data))); failures = 0
                    _update(job_id, name, persist=time.monotonic() - last_save > 5, sent=offset)
                    if time.monotonic() - last_save > 5: last_save = time.monotonic()
            if offset < size: continue
            r = session.post(cfg["base"] + "/complete/" + quote(key, safe="/"), json={"size": size, "sha256": digest}, headers=auth, timeout=(15, 600))
            if r.status_code == 422:
                if restarted: raise ValueError("中转服务器校验 SHA-256 不一致")
                restarted = True; _update(job_id, name, sent=0); continue
            if r.status_code == 409: continue
            if r.status_code >= 500: raise _Retry(f"HTTP {r.status_code}")
            r.raise_for_status(); break
        except (requests.RequestException, _Retry) as e:
            failures += 1
            if failures >= MAX_FAILURES: raise ConnectionError(f"多次连接中转服务器失败：{e}") from None
            _update(job_id, name, persist=False, error=str(e)[:200])
            _sleep(min(300, 2 ** failures))
    return _update(job_id, name, state="ready", sent=size, error="", completed=time.time())
