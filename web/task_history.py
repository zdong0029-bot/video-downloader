"""Private restart-safe task records. Expiry never removes downloaded files."""
import json, sqlite3, time
from pathlib import Path
from contextlib import contextmanager
PATH = Path(__file__).parent / "data" / "task-history.sqlite"
RETENTION = 7 * 86400
ACTIVE = {"queued", "running"}
def retained(status, ended, created, now=None):
    return status in ACTIVE or (now if now is not None else time.time()) < (ended or created) + RETENTION
@contextmanager
def connection():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(PATH, timeout=15)
    db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, ended REAL, payload TEXT)")
    try:
        with db:yield db
    finally:db.close()
def remember(job):
    payload={k:getattr(job,k) for k in ("id","url","kind","status","title","message","progress","total","finished","failed","created","completed_at","owner","owner_label","batch_id","catalog_id","cloud_note","current_entry","failed_entries")}
    payload["files"]=[{"name":f.name,"size":f.size,"done":f.done,"entry_id":f.entry_id,"folder":getattr(f,"folder",""),"label":getattr(f,"label","")} for f in job.files]
    with connection() as db:
        db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?)",(job.id,job.completed_at,json.dumps(payload,ensure_ascii=False)))
        db.execute("DELETE FROM tasks WHERE ended > 0 AND ended < ?",(time.time()-RETENTION,))
def forget(job_id):
    with connection() as db: db.execute("DELETE FROM tasks WHERE id=?",(job_id,))
def load():
    with connection() as db:
        db.execute("DELETE FROM tasks WHERE ended > 0 AND ended < ?",(time.time()-RETENTION,))
        rows=db.execute("SELECT payload FROM tasks").fetchall()
    return [json.loads(row[0]) for row in rows]
