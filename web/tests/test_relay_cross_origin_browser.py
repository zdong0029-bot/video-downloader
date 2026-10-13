"""Real Chromium: segmented disk save and two-lane album read from a cross-origin signed relay URL, incl. link renewal on expiry."""
import hashlib, importlib.util, os, socket, sys, tempfile, threading, time, unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
import requests
WEB = Path(__file__).resolve().parent.parent
TOKEN, SIGNING = "b" * 32, "c" * 32
DATA = os.urandom(9 * 1048576 + 333)   # unaligned tail, > two 4 MiB relay segments
KEY = "jobs/tag/job1/跨域 原片.mp4"
PAGE = """<!doctype html><meta charset=utf-8><script>var cache=[],LOCAL=false;</script>
<script src=/original-file-transfer.js></script><script src=/media-transfer.js></script>"""
def port():
    with socket.socket() as s: s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

class CrossOriginRelay(unittest.TestCase):
    def test_signed_relay_url_in_real_browser(self):
        try: from playwright.sync_api import sync_playwright
        except ImportError: self.skipTest("playwright not installed")
        import uvicorn
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        page_port, relay_port = port(), port(); origin = f"http://localhost:{page_port}"
        saved = {k: os.environ.get(k) for k in ("RELAY_DATA", "RELAY_UPLOAD_TOKEN", "RELAY_SIGNING_KEY", "RELAY_ALLOWED_ORIGIN")}
        os.environ.update(RELAY_DATA=tmp.name, RELAY_UPLOAD_TOKEN=TOKEN, RELAY_SIGNING_KEY=SIGNING, RELAY_ALLOWED_ORIGIN=origin)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        spec = importlib.util.spec_from_file_location("relay_server_browser", WEB.parent / "relay" / "server.py")
        relay = importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)
        server = uvicorn.Server(uvicorn.Config(relay.app, host="127.0.0.1", port=relay_port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start(); self.addCleanup(setattr, server, "should_exit", True)
        site = Path(tmp.name) / "site"; site.mkdir()
        for name in ("original-file-transfer.js", "media-transfer.js"): (site / name).write_bytes((WEB / "static" / name).read_bytes())
        (site / "index.html").write_text(PAGE, "utf-8")
        httpd = ThreadingHTTPServer(("127.0.0.1", page_port), partial(SimpleHTTPRequestHandler, directory=str(site)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start(); self.addCleanup(httpd.shutdown)
        for _ in range(100):
            if server.started: break
            time.sleep(.05)
        base = f"http://127.0.0.1:{relay_port}"
        for o in range(0, len(DATA), 8 * 1048576):
            part = DATA[o:o + 8 * 1048576]
            requests.put(base + "/upload/" + quote(KEY), data=part, headers={"Authorization": "Bearer " + TOKEN, "Content-Range": f"bytes {o}-{o + len(part) - 1}/{len(DATA)}"}, proxies={"http": None}).raise_for_status()
        requests.post(base + "/complete/" + quote(KEY), json={"size": len(DATA), "sha256": hashlib.sha256(DATA).hexdigest()}, headers={"Authorization": "Bearer " + TOKEN}, proxies={"http": None}).raise_for_status()
        def url(ttl):
            exp = int(time.time() + ttl); return base + "/f/" + quote(KEY) + f"?exp={exp}&sig={relay.sign(KEY, exp, SIGNING.encode())}"
        good, expired, sha = url(3600), url(-10), hashlib.sha256(DATA).hexdigest()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True); page = browser.new_page()
            page.goto(f"http://localhost:{page_port}/index.html"); page.wait_for_function("typeof OriginalFileTransfer==='object'")
            result = page.evaluate("""async ({good,expired,size})=>{
              const root=await navigator.storage.getDirectory(),handle=await root.getFileHandle('relay.bin',{create:true});
              let renewals=0;const source=async renew=>{if(renew){renewals++;return good;}return expired;};
              await OriginalFileTransfer.save(source,handle,size,new AbortController().signal,()=>{});
              const file=await handle.getFile(),d=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
              window.RelayLinks={link:async()=>({url:good})};
              const r=await fetchAlbumOriginal({id:'job1',name:'x.mp4',size},new AbortController().signal,()=>{});
              const album=await crypto.subtle.digest('SHA-256',await (r.originalBlob||await r.blob()).arrayBuffer());
              const hex=b=>Array.from(new Uint8Array(b)).map(n=>n.toString(16).padStart(2,'0')).join('');
              return {size:file.size,sha:hex(d),renewals,album:hex(album),route:r.headers.get('X-Transfer-Route')};
            }""", {"good": good, "expired": expired, "size": len(DATA)})
            browser.close()
        self.assertEqual((result["size"], result["sha"]), (len(DATA), sha))
        self.assertEqual(result["renewals"], 1)
        self.assertEqual((result["album"], result["route"]), (sha, "parallel2"))

if __name__ == "__main__": unittest.main()
