"""Daily usage numbers for the admin dashboard.

Visitors: one browser = one visitor per calendar day (local time of this PC).
The site already gives every browser a long-lived random "did" cookie, so the
same phone or computer opening the page 50 times in a day still counts once;
a phone and a computer are two visitors. Admin-logged-in browsers are counted
separately so your own testing does not inflate the numbers.

Downloads: links submitted (by category) and files actually finished + bytes.
Only hashes of device ids are stored. Everything lives in data/usage-stats.sqlite.
"""
import hashlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

PATH = Path(__file__).parent / "data" / "usage-stats.sqlite"
ONLINE_WINDOW = 300          # “当前在线”：5 分钟内有请求
_lock = threading.Lock()
_seen: dict[tuple[str, str], int] = {}   # (day, device_hash) -> admin flag already written
_last: dict[str, tuple[float, str]] = {}  # device_hash -> (last request time, device type)
CATEGORIES = ("video", "cloud", "profile", "tool")


def today(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))


@contextmanager
def connection():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(PATH, timeout=15)
    db.execute("CREATE TABLE IF NOT EXISTS visits (day TEXT, device TEXT, kind TEXT, admin INTEGER, first REAL, last REAL, hits INTEGER, PRIMARY KEY (day, device))")
    db.execute("CREATE TABLE IF NOT EXISTS devices (device TEXT PRIMARY KEY, first_day TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT, category TEXT, links INTEGER DEFAULT 0, files INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, PRIMARY KEY (day, category))")
    try:
        with db:
            yield db
    finally:
        db.close()


def device_type(ua: str) -> str:
    ua = ua or ""
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua):
        return "tablet"
    if any(k in ua for k in ("iPhone", "Android", "Mobile", "HarmonyOS")):
        return "mobile"
    return "desktop"


def _hash(device: str) -> str:
    return hashlib.sha256(("vd-stats:" + device).encode()).hexdigest()[:20]


def record_visit(device: str, ua: str, admin: bool, now: float | None = None) -> None:
    """Call on every API request; writes to disk at most once per device per day (plus admin upgrade)."""
    if not device:
        return
    now = now if now is not None else time.time()
    day, h, kind = today(now), _hash(device), device_type(ua)
    with _lock:
        _last[h] = (now, kind)
        prev = _seen.get((day, h))
        if prev is not None and (prev or not admin):
            return
        _seen[(day, h)] = int(admin)
        if len(_seen) > 50000:
            for k in [k for k in _seen if k[0] != day]:
                _seen.pop(k, None)
    try:
        with connection() as db:
            db.execute("INSERT INTO visits VALUES (?,?,?,?,?,?,1) ON CONFLICT(day, device) DO UPDATE SET admin=MAX(admin, excluded.admin), last=excluded.last, hits=hits+1",
                       (day, h, kind, int(admin), now, now))
            db.execute("INSERT OR IGNORE INTO devices VALUES (?,?)", (h, day))
    except sqlite3.Error:
        pass


def add_usage(category: str, links: int = 0, files: int = 0, size: int = 0, now: float | None = None) -> None:
    if category not in CATEGORIES or not (links or files or size):
        return
    try:
        with connection() as db:
            db.execute("INSERT INTO usage VALUES (?,?,?,?,?) ON CONFLICT(day, category) DO UPDATE SET links=links+excluded.links, files=files+excluded.files, bytes=bytes+excluded.bytes",
                       (today(now), category, int(links), int(files), int(size)))
    except sqlite3.Error:
        pass


def category_of(kind: str) -> str:
    return "cloud" if kind in ("cloud", "file") else "tool" if kind == "tool" else "video"


def summary(days: int = 14, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    days = max(1, min(int(days or 14), 90))
    keys = [today(now - i * 86400) for i in range(days)]
    rows = {d: {"day": d, "visitors": 0, "desktop": 0, "mobile": 0, "tablet": 0, "new": 0, "admin": 0,
                **{f"{c}_{m}": 0 for c in CATEGORIES for m in ("links", "files", "bytes")}} for d in keys}
    with connection() as db:
        for day, kind, admin, n in db.execute(f"SELECT day, kind, admin, COUNT(*) FROM visits WHERE day IN ({','.join('?' * len(keys))}) GROUP BY day, kind, admin", keys):
            r = rows[day]
            if admin:
                r["admin"] += n
            else:
                r["visitors"] += n
                r[kind] = r.get(kind, 0) + n
        for day, n in db.execute(f"SELECT v.day, COUNT(*) FROM visits v JOIN devices d ON d.device=v.device AND d.first_day=v.day WHERE v.admin=0 AND v.day IN ({','.join('?' * len(keys))}) GROUP BY v.day", keys):
            rows[day]["new"] = n
        for day, cat, links, files, size in db.execute(f"SELECT day, category, links, files, bytes FROM usage WHERE day IN ({','.join('?' * len(keys))})", keys):
            rows[day][f"{cat}_links"], rows[day][f"{cat}_files"], rows[day][f"{cat}_bytes"] = links, files, size
        total_visitors = db.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    with _lock:
        online = [k for t, k in _last.values() if now - t <= ONLINE_WINDOW]
    return {"today": rows[keys[0]], "yesterday": rows[keys[1]] if len(keys) > 1 else None,
            "days": [rows[d] for d in keys], "online": len(online), "all_time_devices": total_visitors,
            "rule": "同一浏览器（手机或电脑）每天只算 1 位访客；管理员登录的浏览器单独计数，不算在访客里。"}
