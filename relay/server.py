"""Mainland relay: resumable authenticated uploads in, short-lived signed byte-identical downloads out.

Environment: RELAY_UPLOAD_TOKEN, RELAY_SIGNING_KEY (required), RELAY_ALLOWED_ORIGIN (comma list),
RELAY_DATA (default /var/lib/vd-relay), RELAY_RETENTION_DAYS (7), RELAY_MAX_BYTES (64 GiB).
Run: uvicorn server:app --host 127.0.0.1 --port 8080   ·   cleanup only: python server.py --cleanup
"""
import asyncio, email.utils, hashlib, hmac, json, mimetypes, os, re, shutil, sys, time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

DATA = Path(os.environ.get("RELAY_DATA", "/var/lib/vd-relay"))
RETENTION = float(os.environ.get("RELAY_RETENTION_DAYS", "7")) * 86400
MAX_BYTES = int(os.environ.get("RELAY_MAX_BYTES", str(64 * 1024**3)))
PART_RETENTION = 2 * 86400
READ = 256 * 1024
_SEGMENT = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,255}$")
_locks: dict[str, asyncio.Lock] = {}

def _secret(name):
    value = os.environ.get(name, "")
    if len(value) < 16: raise HTTPException(503, f"{name} is not configured")
    return value.encode()

def sign(key: str, exp: int, secret: bytes) -> str:
    return hmac.new(secret, f"{key}\n{exp}".encode(), hashlib.sha256).hexdigest()

def safe_key(key: str) -> str:
    parts = key.split("/")
    if len(key) > 1024 or len(parts) > 8 or any(p in ("", ".", "..") or not _SEGMENT.match(p) for p in parts):
        raise HTTPException(400, "invalid key")
    root = (DATA / "files").resolve(); target = (root / key).resolve()
    try: target.relative_to(root)
    except ValueError: raise HTTPException(400, "invalid key") from None
    return key

def paths(key):
    return DATA / "files" / key, DATA / "parts" / (key + ".part"), DATA / "meta" / (key + ".meta.json")

def meta(key):
    final, _, info = paths(key)
    try: saved = json.loads(info.read_text("utf-8"))
    except (OSError, ValueError): saved = {}
    st = final.stat()
    if saved.get("size") != st.st_size: saved = {}
    tag = saved.get("sha256", "")[:40] or f"{st.st_size:x}-{st.st_mtime_ns:x}"
    return st, saved.get("sha256", ""), f'"{tag}"'

def _authorize(request: Request):
    header = request.headers.get("authorization", "")
    if not hmac.compare_digest(header.encode(), b"Bearer " + _secret("RELAY_UPLOAD_TOKEN")):
        raise HTTPException(401, "bad token", headers={"WWW-Authenticate": "Bearer"})

def _lock(key): return _locks.setdefault(key, asyncio.Lock())

app = FastAPI(title="video-downloader relay", docs_url=None, redoc_url=None, openapi_url=None)
_origins = [o.strip().rstrip("/") for o in os.environ.get("RELAY_ALLOWED_ORIGIN", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "HEAD"], allow_headers=["Range", "If-Range"],
                   expose_headers=["ETag", "Content-Range", "Content-Length", "Content-Disposition", "Accept-Ranges"], max_age=7200)

@app.get("/healthz")
def health():
    usage = shutil.disk_usage(DATA if DATA.exists() else "/")
    return {"ok": True, "free_bytes": usage.free}

# ------------------------------------------------------------------ upload (home PC only)
def _state(key):
    final, part, _ = paths(key)
    if final.is_file():
        st, digest, _ = meta(key)
        return {"Upload-Offset": str(st.st_size), "Upload-Complete": "1", "X-Content-Sha256": digest}
    return {"Upload-Offset": str(part.stat().st_size if part.is_file() else 0), "Upload-Complete": "0"}

@app.head("/upload/{key:path}")
def upload_state(key: str, request: Request):
    _authorize(request); return Response(headers=_state(safe_key(key)))

_RANGE_IN = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")

@app.put("/upload/{key:path}")
async def upload_chunk(key: str, request: Request):
    _authorize(request); key = safe_key(key)
    m = _RANGE_IN.match(request.headers.get("content-range", ""))
    if not m: raise HTTPException(400, "Content-Range: bytes start-end/total required")
    start, end, total = map(int, m.groups())
    if end < start or end >= total or total > MAX_BYTES: raise HTTPException(400 if total <= MAX_BYTES else 413, "bad range")
    final, part, _ = paths(key)
    async with _lock(key):
        if final.is_file(): return JSONResponse({"complete": True}, 409, headers=_state(key))
        offset = part.stat().st_size if part.is_file() else 0
        if start != offset: return JSONResponse({"detail": "offset mismatch", "offset": offset}, 409, headers=_state(key))
        if start == 0 and shutil.disk_usage(DATA).free < total + 512 * 1048576: raise HTTPException(507, "not enough disk space")
        part.parent.mkdir(parents=True, exist_ok=True)
        wanted, got = end - start + 1, 0
        with open(part, "ab") as out:
            try:
                async for data in request.stream():
                    got += len(data)
                    if got > wanted: break
                    out.write(data)
            except Exception:
                got = -1
            if got != wanted:
                out.truncate(start); raise HTTPException(400, "chunk length does not match Content-Range")
            out.flush(); await asyncio.to_thread(os.fsync, out.fileno())
    return Response(headers=_state(key))

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1048576), b""): h.update(block)
    return h.hexdigest()

@app.post("/complete/{key:path}")
async def complete(key: str, request: Request):
    _authorize(request); key = safe_key(key)
    try:
        body = await request.json(); size, digest = int(body["size"]), str(body["sha256"]).lower()
    except (ValueError, KeyError, TypeError): raise HTTPException(400, "size and sha256 required") from None
    if not re.fullmatch(r"[0-9a-f]{64}", digest): raise HTTPException(400, "bad sha256")
    final, part, info = paths(key)
    async with _lock(key):
        if final.is_file():
            st, saved, _ = meta(key)
            if st.st_size == size and saved == digest: return {"ok": True, "size": size, "sha256": digest}
            raise HTTPException(409, "a different file is already stored under this key")
        if not part.is_file() or part.stat().st_size != size: raise HTTPException(409, "upload incomplete", headers=_state(key))
        actual = await asyncio.to_thread(_sha256, part)
        if actual != digest:
            part.unlink(missing_ok=True)
            raise HTTPException(422, "sha256 mismatch; partial upload discarded")
        final.parent.mkdir(parents=True, exist_ok=True); info.parent.mkdir(parents=True, exist_ok=True)
        tmp = info.with_name(info.name + ".tmp")
        tmp.write_text(json.dumps({"size": size, "sha256": digest, "stored": time.time()}), "utf-8")
        os.replace(part, final); os.replace(tmp, info)
    return {"ok": True, "size": size, "sha256": digest}

@app.delete("/upload/{key:path}")
async def remove(key: str, request: Request):
    _authorize(request); key = safe_key(key)
    async with _lock(key):
        for p in paths(key): p.unlink(missing_ok=True)
    return {"ok": True}

# ------------------------------------------------------------------ signed download (friends)
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

def _disposition(name):
    fallback = re.sub(r'[^A-Za-z0-9._ -]', "_", name).strip() or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"

def _stream(path, start, length):
    with open(path, "rb") as f:
        f.seek(start)
        while length > 0:
            data = f.read(min(READ, length))
            if not data: break
            length -= len(data); yield data

@app.api_route("/f/{key:path}", methods=["GET", "HEAD"])
def fetch(key: str, request: Request, exp: int = 0, sig: str = ""):
    key = safe_key(key)
    if not hmac.compare_digest(sign(key, exp, _secret("RELAY_SIGNING_KEY")), sig.lower()): raise HTTPException(403, "bad signature")
    if exp < time.time(): raise HTTPException(403, "link expired")
    final = paths(key)[0]
    if not final.is_file(): raise HTTPException(404, "not found")
    st, digest, etag = meta(key)
    size = st.st_size
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "Last-Modified": email.utils.formatdate(st.st_mtime, usegmt=True),
               "Content-Disposition": _disposition(key.rsplit("/", 1)[-1]), "Cache-Control": "private, max-age=3600",
               "X-Content-Type-Options": "nosniff"}
    if digest: headers["X-Content-Sha256"] = digest
    media = mimetypes.guess_type(key)[0] or "application/octet-stream"
    if request.headers.get("if-none-match") in (etag, "*") and "range" not in request.headers:
        return Response(status_code=304, headers=headers)
    start, length, status = 0, size, 200
    wanted, if_range = request.headers.get("range", "").strip(), request.headers.get("if-range", "").strip()
    if wanted and (not if_range or if_range in (etag, headers["Last-Modified"])):
        m = _RANGE.match(wanted.replace(" ", ""))  # multi-range or malformed: ignore Range and send 200
        if m and m.group(1) + m.group(2):
            a, b = m.groups()
            if a: first, last = int(a), min(int(b) if b else size - 1, size - 1)
            else: first, last = max(0, size - int(b)), size - 1
            if first >= size or last < first or (not a and int(b) == 0):
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            start, length, status = first, last - first + 1, 206
            headers["Content-Range"] = f"bytes {first}-{last}/{size}"
    headers["Content-Length"] = str(length)
    if request.method == "HEAD": return Response(status_code=status, headers=headers, media_type=media)
    return StreamingResponse(_stream(final, start, length), status_code=status, headers=headers, media_type=media)

# ------------------------------------------------------------------ retention
def cleanup(now=None):
    """Delete stored files older than RETENTION and stale partial uploads. Returns removed paths."""
    now = now or time.time(); removed = []
    for sub, limit in (("files", RETENTION), ("parts", PART_RETENTION), ("meta", RETENTION)):
        root = DATA / sub
        if not root.is_dir(): continue
        for p in root.rglob("*"):
            try:
                if p.is_file() and now - p.stat().st_mtime > limit:
                    if sub == "meta" and (DATA / "files" / str(p.relative_to(root))[:-len(".meta.json")]).is_file(): continue
                    p.unlink(); removed.append(p)
            except OSError: pass
        for d in sorted((d for d in root.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
            try: d.rmdir()
            except OSError: pass
    return removed

@asynccontextmanager
async def lifespan(_app):
    for sub in ("files", "parts", "meta"): (DATA / sub).mkdir(parents=True, exist_ok=True)
    async def loop():
        while True:
            await asyncio.to_thread(cleanup); await asyncio.sleep(3600)
    task = asyncio.create_task(loop())
    try: yield
    finally: task.cancel()
app.router.lifespan_context = lifespan

if __name__ == "__main__" and "--cleanup" in sys.argv:
    print(f"removed {len(cleanup())} files")
