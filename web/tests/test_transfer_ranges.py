import hashlib,http.client,json,re,sys,tempfile,threading,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import file_download as files,xhs_images
from common import Cancelled
DATA=bytes(range(256))*40000
class Ranges(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name);self.mode='normal';self.calls=[];test=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    header=self.headers.get('Range');test.calls.append(header);match=re.fullmatch(r'bytes=(\d+)-(\d*)',header or '')
    start,end=(int(match[1]),int(match[2]) if match[2] else len(DATA)-1) if match else (0,len(DATA)-1)
    ranged=bool(match) and test.mode!='ignored'
    if not ranged:start,end=0,len(DATA)-1
    data=DATA[start:end+1]
    if test.mode=='corrupt' and ranged:data=bytes([data[0]^1])+data[1:]
    self.send_response(206 if ranged else 200);self.send_header('Content-Type','application/octet-stream');
    if test.mode!='no-length':self.send_header('Content-Length',str(len(data)))
    self.send_header('Connection','close');self.send_header('ETag','"same-version"')
    if ranged:self.send_header('Content-Range',f'bytes {start+1 if test.mode=="invalid" else start}-{end}/{len(DATA)}')
    self.end_headers()
    try:self.wfile.write(data)
    except (ConnectionError,OSError):pass
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  def open_response(url,headers=None,offset=0,end=None):
   conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5);conn.request('GET','/fixture',headers=files._download_headers('cdn.example',headers,offset,end));return conn,conn.getresponse(),url
  self.mock=patch.object(files,'open_public',side_effect=open_response);self.mock.start()
 def tearDown(self):
  self.mock.stop();self.server.shutdown();self.server.server_close();self.thread.join(2);self.tmp.cleanup()
 def fetch(self,cancelled=lambda:False):return files.fetch_original('https://cdn.example/original',self.out,'file.apk',len(DATA),lambda n:None,cancelled,checksums={'sha1':hashlib.sha1(DATA).hexdigest()})
 def test_exact_four_ranges_without_accept_ranges_header(self):
  self.assertEqual(self.fetch().read_bytes(),DATA);ranges=[r for r in self.calls if r];self.assertEqual(len(ranges),4);self.assertEqual(sorted(int(r.split('=')[1].split('-')[0]) for r in ranges),[len(DATA)*i//4 for i in range(4)]);self.assertFalse(list(self.out.glob('*.part*')))
 def test_known_size_without_content_length(self):
  self.mode='no-length';self.assertEqual(self.fetch().read_bytes(),DATA);self.assertEqual(len([r for r in self.calls if r]),4)
 def test_ignored_ranges_fall_back(self):
  self.mode='ignored';self.assertEqual(self.fetch().read_bytes(),DATA);self.assertGreaterEqual(self.calls.count(None),2)
 def test_bad_range_header_falls_back(self):
  self.mode='invalid';self.assertEqual(self.fetch().read_bytes(),DATA)
 def test_wrong_bytes_never_completed(self):
  self.mode='corrupt'
  with self.assertRaises(ValueError):self.fetch()
  self.assertFalse((self.out/'file.apk').exists());self.assertFalse(list(self.out.glob('*.part*')))
 def test_cancel_cleans_segments(self):
  with self.assertRaises(Cancelled):self.fetch(lambda:len(self.calls)>=2)
  self.assertFalse(list(self.out.iterdir()))
class PublicImages(unittest.TestCase):
 def test_best_exposed_image_variant(self):
  state={'note':{'noteDetailMap':{'id':{'note':{'type':'normal','title':'图文','imageList':[{'urlPre':'https://cdn.xhscdn.com/tiny','urlDefault':'http://cdn.xhscdn.com/full'}]}}}}}
  self.assertEqual(xhs_images.parse('<script>window.__INITIAL_STATE__='+json.dumps(state)+'</script>'),('图文',['https://cdn.xhscdn.com/full']))
 def test_login_and_foreign_image_origin_rejected(self):
  with self.assertRaises(ValueError):xhs_images.parse('<html>login</html>')
  state={'note':{'noteDetailMap':{'id':{'note':{'imageList':[{'urlDefault':'http://127.0.0.1/private'}]}}}}}
  with self.assertRaises(ValueError):xhs_images.parse('<script>window.__INITIAL_STATE__='+json.dumps(state)+'</script>')
if __name__=='__main__':unittest.main()
