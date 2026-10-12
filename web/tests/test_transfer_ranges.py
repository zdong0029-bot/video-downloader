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
    if test.mode in ('baidu-cap','baidu-break','baidu-md5','baidu-bad-md5','denied') and (not match or end-start+1>5*1024**2 or test.mode=='denied'):
     self.send_response(403);self.send_header('Content-Length','0');self.end_headers();return
    ranged=bool(match) and test.mode!='ignored'
    if not ranged:start,end=0,len(DATA)-1
    data=DATA[start:end+1]
    if test.mode=='corrupt' and ranged:data=bytes([data[0]^1])+data[1:]
    self.send_response(206 if ranged else 200);self.send_header('Content-Type','application/octet-stream');
    if test.mode!='no-length':self.send_header('Content-Length',str(len(data)))
    if test.mode in ('baidu-md5','baidu-bad-md5'):self.send_header('Content-MD5',hashlib.md5(DATA).hexdigest() if test.mode=='baidu-md5' else '0'*32)
    self.send_header('Connection','close');self.send_header('ETag','"changed-version"' if test.mode=='changed' and start else '"same-version"')
    if ranged:self.send_header('Content-Range',f'bytes {start+1 if test.mode=="invalid" else start}-{end}/{len(DATA)}')
    self.end_headers()
    if test.mode=='baidu-break' and start==0 and len([h for h in test.calls if h and h.startswith('bytes=0-')])==2:data=data[:512*1024]
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
 def fetch_chunked(self,progress=lambda n:None):
  return files.fetch_original('https://cdn.example/original',self.out,'large.apk',len(DATA),progress,lambda:False,checksums={'md5':hashlib.md5(DATA).hexdigest()},range_size=4*1024**2,workers=3)
 def test_baidu_bounded_ranges_and_live_byte_progress(self):
  self.mode='baidu-cap';progress=[];self.assertEqual(self.fetch_chunked(progress.append).read_bytes(),DATA)
  self.assertTrue(all(re.fullmatch(r'bytes=\d+-\d+',h or '') for h in self.calls))
  self.assertTrue(all(int(h.split('-')[1])-int(h.split('=')[1].split('-')[0])+1<=4*1024**2 for h in self.calls))
  self.assertEqual(progress[-1],len(DATA));self.assertTrue(any(0<n<len(DATA) for n in progress));self.assertFalse(list(self.out.glob('*.part*')))
 def test_baidu_cdn_hex_md5_checks_whole_original(self):
  self.mode='baidu-md5'
  p=files.fetch_original('https://cdn.example/original',self.out,'hash.apk',len(DATA),lambda n:None,lambda:False,checksums={'md5':'obfuscated-share-value'},range_size=4*1024**2)
  self.assertEqual(p.read_bytes(),DATA);p.unlink();self.mode='baidu-bad-md5'
  with self.assertRaises(ValueError):files.fetch_original('https://cdn.example/original',self.out,'hash.apk',len(DATA),lambda n:None,lambda:False,range_size=4*1024**2)
  self.assertFalse((self.out/'hash.apk').exists());self.assertFalse(list(self.out.glob('*.part*')))
 def test_baidu_chunk_network_interruption_resumes_offsets(self):
  self.mode='baidu-break';self.assertEqual(self.fetch_chunked().read_bytes(),DATA)
  self.assertTrue(any(h and h.startswith('bytes=524288-') for h in self.calls));self.assertFalse(list(self.out.glob('*.part*')))
 def test_baidu_denied_ranges_never_retry_full_get(self):
  self.mode='denied'
  with self.assertRaises(ValueError):self.fetch_chunked()
  self.assertEqual(len(self.calls),1);self.assertTrue(self.calls[0].startswith('bytes=0-'));self.assertFalse(list(self.out.iterdir()))
 def test_baidu_changed_object_and_bad_hash_never_complete(self):
  for mode in ('changed','corrupt'):
   self.mode=mode
   with self.assertRaises(ValueError):self.fetch_chunked()
   self.assertFalse((self.out/'large.apk').exists());self.assertFalse(list(self.out.glob('*.part*')))
class PublicImages(unittest.TestCase):
 def test_best_exposed_image_variant(self):
  state={'note':{'noteDetailMap':{'id':{'note':{'type':'normal','title':'图文','imageList':[{'urlPre':'https://cdn.xhscdn.com/tiny','urlDefault':'http://cdn.xhscdn.com/full'}]}}}}}
  self.assertEqual(xhs_images.parse('<script>window.__INITIAL_STATE__='+json.dumps(state)+'</script>'),('图文',['https://cdn.xhscdn.com/full']))
 def test_login_and_foreign_image_origin_rejected(self):
  with self.assertRaises(ValueError):xhs_images.parse('<html>login</html>')
  state={'note':{'noteDetailMap':{'id':{'note':{'imageList':[{'urlDefault':'http://127.0.0.1/private'}]}}}}}
  with self.assertRaises(ValueError):xhs_images.parse('<script>window.__INITIAL_STATE__='+json.dumps(state)+'</script>')
if __name__=='__main__':unittest.main()
