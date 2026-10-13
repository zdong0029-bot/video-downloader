"""Relay server: resumable upload, integrity, signatures, Range/If-Range, traversal, CORS, retention."""
import hashlib, importlib, os, sys, tempfile, time, unittest
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

TOKEN, SIGNING, ORIGIN = "t" * 32, "s" * 32, "https://site.example"
DATA = os.urandom(3 * 1048576 + 17)
KEY = "jobs/abc123/job1/原片 one.mp4"

class RelayServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.update(RELAY_DATA=self.tmp.name, RELAY_UPLOAD_TOKEN=TOKEN, RELAY_SIGNING_KEY=SIGNING, RELAY_ALLOWED_ORIGIN=ORIGIN)
        import server; self.s = importlib.reload(server)
        self.c = TestClient(self.s.app); self.c.__enter__()
        self.auth = {"Authorization": "Bearer " + TOKEN}
    def tearDown(self):
        self.c.__exit__(None, None, None); self.tmp.cleanup()
    def put(self, key, start, data, total=len(DATA), auth=None):
        return self.c.put("/upload/" + quote(key), content=data, headers={**(auth or self.auth), "Content-Range": f"bytes {start}-{start + len(data) - 1}/{total}"})
    def upload(self, key=KEY, data=DATA, step=1048576):
        for o in range(0, len(data), step): self.assertEqual(self.put(key, o, data[o:o + step], len(data)).status_code, 200)
        r = self.c.post("/complete/" + quote(key), json={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}, headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
    def url(self, key=KEY, ttl=600):
        exp = int(time.time() + ttl); return "/f/" + quote(key) + f"?exp={exp}&sig={self.s.sign(key, exp, SIGNING.encode())}"

    def test_token_required(self):
        self.assertEqual(self.c.head("/upload/" + quote(KEY)).status_code, 401)
        self.assertEqual(self.put(KEY, 0, b"x", auth={"Authorization": "Bearer wrong"}).status_code, 401)

    def test_resume_from_head_offset_and_reject_gaps(self):
        self.assertEqual(self.put(KEY, 0, DATA[:1048576]).status_code, 200)
        head = self.c.head("/upload/" + quote(KEY), headers=self.auth)
        self.assertEqual((head.headers["Upload-Offset"], head.headers["Upload-Complete"]), ("1048576", "0"))
        gap = self.put(KEY, 2 * 1048576, DATA[2 * 1048576:3 * 1048576])
        self.assertEqual((gap.status_code, gap.headers["Upload-Offset"]), (409, "1048576"))
        short = self.c.put("/upload/" + quote(KEY), content=b"abc", headers={**self.auth, "Content-Range": f"bytes 1048576-1048579/{len(DATA)}"})
        self.assertEqual(short.status_code, 400)
        self.assertEqual(self.c.head("/upload/" + quote(KEY), headers=self.auth).headers["Upload-Offset"], "1048576")
        for o in range(1048576, len(DATA), 1048576): self.assertEqual(self.put(KEY, o, DATA[o:o + 1048576]).status_code, 200)
        r = self.c.post("/complete/" + quote(KEY), json={"size": len(DATA), "sha256": hashlib.sha256(DATA).hexdigest()}, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        head = self.c.head("/upload/" + quote(KEY), headers=self.auth)
        self.assertEqual((head.headers["Upload-Complete"], head.headers["X-Content-Sha256"]), ("1", hashlib.sha256(DATA).hexdigest()))
        self.assertEqual(self.c.get(self.url()).content, DATA)

    def test_sha_mismatch_discards_part_and_never_publishes(self):
        self.put(KEY, 0, DATA[:1048576], total=1048576)
        r = self.c.post("/complete/" + quote(KEY), json={"size": 1048576, "sha256": "0" * 64}, headers=self.auth)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.head("/upload/" + quote(KEY), headers=self.auth).headers["Upload-Offset"], "0")
        self.assertEqual(self.c.get(self.url()).status_code, 404)
        incomplete = self.c.post("/complete/" + quote(KEY), json={"size": 5, "sha256": "0" * 64}, headers=self.auth)
        self.assertEqual(incomplete.status_code, 409)

    def test_signature_expiry_and_tamper(self):
        self.upload()
        good = self.url(); self.assertEqual(self.c.get(good).status_code, 200)
        self.assertEqual(self.c.get(self.url(ttl=-5)).status_code, 403)
        exp = int(time.time() + 600); sig = self.s.sign(KEY, exp, SIGNING.encode())
        self.assertEqual(self.c.get("/f/" + quote(KEY) + f"?exp={exp + 1}&sig={sig}").status_code, 403)
        self.assertEqual(self.c.get("/f/" + quote(KEY) + f"?exp={exp}&sig={sig[:-1]}0").status_code in (403,), True)
        other = "jobs/abc123/job1/other.mp4"
        self.assertEqual(self.c.get("/f/" + quote(other) + f"?exp={exp}&sig={sig}").status_code, 403)
        self.assertEqual(self.c.get("/f/" + quote(KEY)).status_code, 403)

    def test_range_206_headers_and_unicode_disposition(self):
        self.upload(); size = len(DATA)
        full = self.c.get(self.url())
        etag = full.headers["etag"]
        self.assertTrue(etag.startswith('"') and not etag.startswith("W/"))
        self.assertIn("filename*=UTF-8''%E5%8E%9F%E7%89%87%20one.mp4", full.headers["content-disposition"])
        self.assertEqual((full.headers["content-length"], full.headers["accept-ranges"]), (str(size), "bytes"))
        r = self.c.get(self.url(), headers={"Range": "bytes=0-0"})
        self.assertEqual((r.status_code, r.headers["content-range"], r.content), (206, f"bytes 0-0/{size}", DATA[:1]))
        r = self.c.get(self.url(), headers={"Range": "bytes=1048576-2097151"})
        self.assertEqual((r.status_code, r.headers["content-length"], r.content), (206, "1048576", DATA[1048576:2097152]))
        r = self.c.get(self.url(), headers={"Range": "bytes=-10"}); self.assertEqual(r.content, DATA[-10:])
        r = self.c.get(self.url(), headers={"Range": f"bytes={size - 5}-"}); self.assertEqual(r.content, DATA[-5:])
        r = self.c.get(self.url(), headers={"Range": f"bytes={size}-"})
        self.assertEqual((r.status_code, r.headers["content-range"]), (416, f"bytes */{size}"))
        r = self.c.get(self.url(), headers={"Range": "bytes=0-1,5-6"}); self.assertEqual((r.status_code, len(r.content)), (200, size))
        h = self.c.head(self.url(), headers={"Range": "bytes=10-19"})
        self.assertEqual((h.status_code, h.headers["content-length"], h.content), (206, "10", b""))

    def test_if_range_matches_only_current_version(self):
        self.upload(); etag = self.c.head(self.url()).headers["etag"]
        r = self.c.get(self.url(), headers={"Range": "bytes=5-9", "If-Range": etag}); self.assertEqual((r.status_code, r.content), (206, DATA[5:10]))
        r = self.c.get(self.url(), headers={"Range": "bytes=5-9", "If-Range": '"stale"'}); self.assertEqual((r.status_code, len(r.content)), (200, len(DATA)))
        self.assertEqual(self.c.get(self.url(), headers={"If-None-Match": etag}).status_code, 304)

    def test_path_traversal_rejected(self):
        from fastapi import HTTPException
        for bad in ("../etc/passwd", "jobs/../../x", "jobs/a/./b", "jobs//x", "jobs/a\\..\\b", "jobs/\x00x", "/abs", "jobs/" + "a" * 300):
            with self.assertRaises(HTTPException, msg=bad): self.s.safe_key(bad)
        # Percent-encoded forms reach the route decoded and must still be refused (clients may not normalise them).
        for raw in ("jobs/%2e%2e/%2e%2e/x", "jobs/a%2f..%2f..%2fx", "jobs/a%5c..%5cb", "jobs/%00x"):
            r = self.c.put("/upload/" + raw, content=b"x", headers={**self.auth, "Content-Range": "bytes 0-0/1"})
            self.assertEqual(r.status_code, 400, raw)
            exp = int(time.time() + 60)
            self.assertEqual(self.c.get("/f/" + raw + f"?exp={exp}&sig=x").status_code, 400, raw)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["files", "meta", "parts"])
        self.assertFalse(any((Path(self.tmp.name) / "parts").rglob("*.part")))

    def test_cors_preflight_and_exposed_headers(self):
        self.upload()
        pre = self.c.options(self.url(), headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "range,if-range"})
        self.assertEqual(pre.status_code, 200); self.assertEqual(pre.headers["access-control-allow-origin"], ORIGIN)
        self.assertIn("if-range", pre.headers["access-control-allow-headers"].lower())
        r = self.c.get(self.url(), headers={"Origin": ORIGIN, "Range": "bytes=0-0"})
        exposed = r.headers["access-control-expose-headers"].lower()
        self.assertTrue(all(h in exposed for h in ("etag", "content-range", "content-length")))
        bad = self.c.get(self.url(ttl=-1), headers={"Origin": ORIGIN})   # readable 403 lets the page re-request a link
        self.assertEqual((bad.status_code, bad.headers.get("access-control-allow-origin")), (403, ORIGIN))
        self.assertNotIn("access-control-allow-origin", self.c.get(self.url(), headers={"Origin": "https://evil.example"}).headers)

    def test_cleanup_removes_old_files_only(self):
        self.upload(); self.upload("jobs/abc123/job2/new.mp4", b"fresh")
        old = Path(self.tmp.name) / "files" / KEY; past = time.time() - 8 * 86400; os.utime(old, (past, past))
        removed = self.s.cleanup()
        self.assertIn(old, removed); self.assertFalse(old.exists())
        self.assertEqual(self.c.get(self.url("jobs/abc123/job2/new.mp4")).content, b"fresh")
        self.assertEqual(self.c.get(self.url()).status_code, 404)

if __name__ == "__main__": unittest.main()
