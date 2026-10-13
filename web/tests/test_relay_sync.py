"""Home-side relay sync against a real local relay server: resume, retry, integrity restart, proxy bypass, disabled mode."""
import hashlib, importlib.util, os, socket, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
import requests
WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB))
import task_history, relay_sync

TOKEN, SIGNING = "u" * 32, "k" * 32
DATA = os.urandom(300 * 1024 + 7)
ENV = ("RELAY_BASE_URL", "RELAY_UPLOAD_TOKEN", "RELAY_SIGNING_KEY", "RELAY_DATA", "RELAY_ALLOWED_ORIGIN", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "no_proxy")

def _relay_server(data_dir):
    os.environ.update(RELAY_DATA=data_dir, RELAY_UPLOAD_TOKEN=TOKEN, RELAY_SIGNING_KEY=SIGNING, RELAY_ALLOWED_ORIGIN="https://site.example")
    spec = importlib.util.spec_from_file_location("relay_server", WEB.parent / "relay" / "server.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

class RelaySync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn
        cls.saved_env = {k: os.environ.get(k) for k in ENV}
        cls.tmp = tempfile.TemporaryDirectory(); cls.module = _relay_server(cls.tmp.name + "/relay")
        with socket.socket() as s: s.bind(("127.0.0.1", 0)); cls.port = s.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(cls.module.app, host="127.0.0.1", port=cls.port, log_level="warning"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True); cls.thread.start()
        for _ in range(100):
            if cls.server.started: break
            time.sleep(.05)
    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True; cls.thread.join(5); cls.tmp.cleanup()
        for k, v in cls.saved_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    def setUp(self):
        self.base = f"http://127.0.0.1:{self.port}"
        os.environ.update(RELAY_BASE_URL=self.base, RELAY_UPLOAD_TOKEN=TOKEN, RELAY_SIGNING_KEY=SIGNING)
        self.work = tempfile.TemporaryDirectory(); self.old_path, self.old_cfg = task_history.PATH, relay_sync.CONFIG
        task_history.PATH = Path(self.work.name) / "data" / "task-history.sqlite"; relay_sync.CONFIG = Path(self.work.name) / "none.json"
        relay_sync.reload(); relay_sync._state.clear(); relay_sync._paths.clear()
        self.file = Path(self.work.name) / "原片.mp4"; self.file.write_bytes(DATA)
        self.chunk = patch.object(relay_sync, "CHUNK", 64 * 1024); self.chunk.start()
        self.nosleep = patch.object(relay_sync, "_sleep", lambda s: None); self.nosleep.start()
    def tearDown(self):
        self.chunk.stop(); self.nosleep.stop(); task_history.PATH, relay_sync.CONFIG = self.old_path, self.old_cfg
        relay_sync._state.clear(); relay_sync._paths.clear(); self.work.cleanup(); relay_sync.reload()
    def job(self): return f"job{time.time_ns()}"
    def fetch(self, key, headers=None):
        url, _ = relay_sync.signed_url(key); return requests.get(url, headers=headers or {}, proxies={"http": None, "https": None})

    def test_disabled_mode_is_inert(self):
        for k in ("RELAY_BASE_URL", "RELAY_UPLOAD_TOKEN", "RELAY_SIGNING_KEY"): os.environ.pop(k)
        relay_sync.reload()
        self.assertFalse(relay_sync.enabled()); self.assertIsNone(relay_sync.status("j", "n"))
        self.assertFalse(relay_sync.enqueue("j", "n", self.file, "owner")); self.assertEqual(relay_sync._state, {})
        relay_sync.restore(lambda j, n: None); self.assertEqual(relay_sync._state, {})

    def test_upload_resumes_from_server_offset_and_is_byte_identical(self):
        job = self.job(); key = relay_sync.key_for("device-cookie", job, self.file.name)
        self.assertNotIn("device-cookie", key)
        first = requests.put(self.base + "/upload/" + quote(key), data=DATA[:128 * 1024], proxies={"http": None},
                             headers={"Authorization": "Bearer " + TOKEN, "Content-Range": f"bytes 0-{128 * 1024 - 1}/{len(DATA)}"})
        self.assertEqual(first.status_code, 200)
        sent = []; real = requests.Session.put
        def spy(session, url, **kw): sent.append(kw["headers"]["Content-Range"]); return real(session, url, **kw)
        with patch.object(requests.Session, "put", spy): row = relay_sync.upload(job, self.file.name, self.file, "device-cookie")
        self.assertTrue(sent[0].startswith(f"bytes {128 * 1024}-"), sent[0])
        self.assertEqual((row["state"], row["sha256"]), ("ready", hashlib.sha256(DATA).hexdigest()))
        r = self.fetch(key); self.assertEqual((r.status_code, r.content), (200, DATA))
        self.assertEqual(self.fetch(key, {"Range": "bytes=10-19"}).content, DATA[10:20])
        sent.clear()
        with patch.object(requests.Session, "put", spy): relay_sync.upload(job, self.file.name, self.file, "device-cookie")
        self.assertEqual(sent, [])   # already complete with the same SHA-256: nothing re-sent

    def test_transient_failures_retry_with_backoff(self):
        job = self.job(); real = requests.Session.put; calls = {"n": 0}; waits = []
        def flaky(session, url, **kw):
            calls["n"] += 1
            if calls["n"] in (2, 3): raise requests.ConnectionError("link dropped")
            return real(session, url, **kw)
        with patch.object(requests.Session, "put", flaky), patch.object(relay_sync, "_sleep", waits.append):
            row = relay_sync.upload(job, self.file.name, self.file, "o")
        self.assertEqual(row["state"], "ready"); self.assertEqual(waits, [2, 4])
        self.assertEqual(self.fetch(row["key"]).content, DATA)

    def test_persistent_failure_marks_failed_without_crashing_worker(self):
        os.environ["RELAY_BASE_URL"] = "http://127.0.0.1:9"; relay_sync.reload(); job = self.job()
        relay_sync.enqueue(job, self.file.name, self.file, "o"); relay_sync._queue.join()
        self.assertEqual(relay_sync.status(job, self.file.name)["state"], "failed")
        self.assertEqual(relay_sync._load()[(job, self.file.name)]["state"], "failed")   # persisted beside task history

    def test_corrupted_remote_part_restarts_once_then_succeeds(self):
        job = self.job(); key = relay_sync.key_for("o", job, self.file.name)
        bad = bytes(len(DATA))
        for o in range(0, len(DATA), 65536):
            requests.put(self.base + "/upload/" + quote(key), data=bad[o:o + 65536], proxies={"http": None},
                         headers={"Authorization": "Bearer " + TOKEN, "Content-Range": f"bytes {o}-{min(len(DATA), o + 65536) - 1}/{len(DATA)}"})
        row = relay_sync.upload(job, self.file.name, self.file, "o")
        self.assertEqual(row["state"], "ready"); self.assertEqual(self.fetch(key).content, DATA)

    def test_upload_ignores_system_proxy(self):
        os.environ.update(HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="", no_proxy="")
        with self.assertRaises(requests.RequestException): requests.head(self.base + "/healthz", timeout=5)
        row = relay_sync.upload(self.job(), self.file.name, self.file, "o")
        self.assertEqual(row["state"], "ready")

    def test_queue_and_link_states(self):
        job = self.job()
        self.assertEqual(relay_sync.status(job, self.file.name), {"state": "none"})
        self.assertIn(relay_sync.link(job, self.file.name, self.file, "o")["state"], ("pending", "uploading", "ready"))
        relay_sync._queue.join()
        info = relay_sync.link(job, self.file.name, self.file, "o")
        self.assertEqual((info["state"], info["size"], info["sha256"]), ("ready", len(DATA), hashlib.sha256(DATA).hexdigest()))
        self.assertTrue(info["expires"] - time.time() > 5.9 * 3600)
        self.assertEqual(requests.get(info["url"], proxies={"http": None}).content, DATA)
        expired, _ = relay_sync.signed_url(relay_sync.key_for("o", job, self.file.name), ttl=-1)
        self.assertEqual(requests.get(expired, proxies={"http": None}).status_code, 403)
        # A changed local original is never served from a stale relay copy.
        self.file.write_bytes(DATA[::-1]); self.assertNotEqual(relay_sync.link(job, self.file.name, self.file, "o")["state"], "ready")
        relay_sync._queue.join()
        info = relay_sync.link(job, self.file.name, self.file, "o")
        self.assertEqual(requests.get(info["url"], proxies={"http": None}).content, DATA[::-1])

    def test_link_endpoint_keeps_ownership_and_falls_back(self):
        try:
            import subprocess, types
            if sys.platform != "win32" and "msvcrt" not in sys.modules:   # app imports Windows-only helpers lazily used
                stub = types.ModuleType("msvcrt"); stub.locking = lambda *a: None; stub.LK_LOCK = stub.LK_UNLCK = stub.LK_NBLCK = stub.LK_NBRLCK = 0
                sys.modules["msvcrt"] = stub
            import app
            from fastapi.testclient import TestClient
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"app not importable here: {e}")
        downloads = Path(self.work.name) / "downloads"; job_id = self.job(); (downloads / job_id).mkdir(parents=True)
        (downloads / job_id / self.file.name).write_bytes(DATA)
        job = app.Job(id=job_id, url="https://example.com", kind="file", status="done", owner="friend-device", completed_at=time.time())
        job.files = [app.FileItem(self.file.name, len(DATA), True)]
        with patch.object(app, "DOWNLOADS", downloads), patch.dict(app.JOBS, {job_id: job}):
            client = TestClient(app.app); path = f"/api/files/{job_id}/{quote(self.file.name)}/link"
            self.assertEqual(client.get(path, cookies={"did": "someone-else"}).status_code, 404)
            self.assertEqual(client.get(f"/api/files/{job_id}/..%2F..%2Fapp.py/link", cookies={"did": "friend-device"}).status_code, 404)
            first = client.get(path, cookies={"did": "friend-device"}).json()
            self.assertIn(first["state"], ("pending", "uploading", "ready"))
            self.assertEqual(first["fallback"], f"/api/files/{job_id}/{quote(self.file.name, safe='')}")
            self.assertEqual(client.get("/api/jobs", cookies={"did": "friend-device"}).json()[0]["files"][0]["relay"]["state"] in ("pending", "uploading", "ready"), True)
            relay_sync._queue.join()
            ready = client.get(path, cookies={"did": "friend-device"}).json()
            self.assertEqual((ready["state"], ready["size"], ready["sha256"]), ("ready", len(DATA), hashlib.sha256(DATA).hexdigest()))
            self.assertEqual(requests.get(ready["url"], proxies={"http": None}).content, DATA)
            self.assertEqual(client.get("/api/jobs", cookies={"did": "friend-device"}).json()[0]["files"][0]["relay"], {"state": "ready"})
            for k in ("RELAY_BASE_URL", "RELAY_UPLOAD_TOKEN", "RELAY_SIGNING_KEY"): os.environ.pop(k)
            relay_sync.reload()
            self.assertEqual(client.get(path, cookies={"did": "friend-device"}).json()["state"], "disabled")
            self.assertNotIn("relay", client.get("/api/jobs", cookies={"did": "friend-device"}).json()[0]["files"][0])

if __name__ == "__main__": unittest.main()
