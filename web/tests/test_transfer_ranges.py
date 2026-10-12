import hashlib,http.client,json,re,sys,tempfile,threading,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import file_download as files,xhs_images
from common import Cancelled
DATA=bytes(range(256))*40000
class Ranges(unittest.TestCase):
 def test_server_overload_reduces_concurrency_before_resuming(self):
  workers=[]
  def chunks(url,headers,partial,total,validator,progress,cancelled,chunk_size,count,renew,checkpoint):
   workers.append(count)
   if len(workers)<3:raise files.TransferHTTPError(503)
   partial.write_bytes(DATA);progress(total)
  with patch.object(files,'_chunked_original',side_effect=chunks),patch('time.sleep'):
   target=files.fetch_original('https://cdn.example/file',self.out,'overload.apk',len(DATA),lambda n:None,lambda:False,
    checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True,workers=16)
  self.assertEqual(workers,[16,8,4]);self.assertEqual(target.read_bytes(),DATA)
 def test_xunlei_official_gcid_validates_bytes_with_object_id_md5_header(self):
  source=self.out/'fixture.bin';source.write_bytes(DATA);gcid=files.xunlei_gcid(source);source.unlink()
  self.mode='baidu-bad-md5'
  p=files.fetch_original('https://cdn.example/file',self.out,'original.apk',len(DATA),lambda n:None,lambda:False,
   checksums={'gcid':gcid},range_size=4*1024**2,workers=4)
  self.assertEqual(p.read_bytes(),DATA);p.unlink();self.mode='corrupt'
  with self.assertRaisesRegex(ValueError,'GCID'):
   files.fetch_original('https://cdn.example/file',self.out,'bad.apk',len(DATA),lambda n:None,lambda:False,
    checksums={'gcid':gcid},range_size=4*1024**2,workers=4)
  self.assertFalse(list(self.out.glob('*.part*')));self.assertFalse((self.out/'bad.apk').exists())
 def test_exhausted_chunk_retry_keeps_verified_ranges_in_same_transfer(self):
  original=files.open_public;failures=[0];zero=[0];progress=[]
  def interrupted(url,headers=None,offset=0,end=None):
   if offset==0:zero[0]+=1
   if offset==4*1024**2 and failures[0]<6:
    failures[0]+=1;raise files.TransferHTTPError(503)
   return original(url,headers,offset,end)
  with patch.object(files,'open_public',side_effect=interrupted),patch('time.sleep'):
   p=files.fetch_original('https://cdn.example/file',self.out,'resume.apk',len(DATA),progress.append,lambda:False,
      checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True,workers=1)
  self.assertEqual(p.read_bytes(),DATA);self.assertEqual(failures[0],6)
  self.assertEqual(zero[0],3) # two probes; the first completed range is not downloaded again
  self.assertEqual(progress,sorted(progress));self.assertFalse(list(self.out.glob('*.part*')))
 def test_checkpoint_cannot_resume_a_changed_object(self):
  original=files.open_public;failures=[0]
  def interrupted(url,headers=None,offset=0,end=None):
   if offset==4*1024**2 and failures[0]<6:
    failures[0]+=1;raise files.TransferHTTPError(503)
   if failures[0]==6:self.mode='changed'
   return original(url,headers,offset,end)
  with patch.object(files,'open_public',side_effect=interrupted),patch('time.sleep'):
   with self.assertRaisesRegex(ValueError,'版本'):
    files.fetch_original('https://cdn.example/file',self.out,'changed.apk',len(DATA),lambda n:None,lambda:False,
     checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True,workers=1)
  self.assertFalse(list(self.out.glob('*.part*')));self.assertFalse((self.out/'changed.apk').exists())
 def test_failed_renewal_preserves_first_login_error_and_cleans_parts(self):
  import cloud_backend
  original=files.open_public;calls=[0];guard=threading.Lock()
  def expired(url,*args,**kwargs):
   with guard:calls[0]+=1;first=calls[0]==1
   if not first:raise files.TransferHTTPError(403)
   return original(url,*args,**kwargs)
  def renew():raise cloud_backend.LoginRequired('bound-account authorization expired')
  with patch.object(files,'open_public',side_effect=expired):
   with self.assertRaisesRegex(cloud_backend.LoginRequired,'bound-account'):
    files.fetch_original('https://cdn.example/file',self.out,'renew.apk',len(DATA),lambda n:None,lambda:False,
      checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True,workers=4,renew_url=renew)
  self.assertFalse((self.out/'renew.apk').exists());self.assertFalse(list(self.out.glob('*.part*')))
 def test_cloud_retry_temporary_http_failure_keeps_original(self):
  original=files.open_public;calls=[0]
  def transient(*args,**kwargs):
   calls[0]+=1
   if calls[0]<=2:raise files.TransferHTTPError(503)
   return original(*args,**kwargs)
  with patch.object(files,'open_public',side_effect=transient),patch('time.sleep'):
   p=files.fetch_original('https://cdn.example/file',self.out,'retry.apk',len(DATA),lambda n:None,lambda:False,
                          checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True)
  self.assertEqual(p.read_bytes(),DATA)
 def test_expired_original_link_renews_without_changing_source_bytes(self):
  original=files.open_public;urls=[]
  def expiration(url,*args,**kwargs):
   urls.append(url)
   if url.endswith('/expired'):raise files.TransferHTTPError(403)
   return original(url,*args,**kwargs)
  with patch.object(files,'open_public',side_effect=expiration):
   p=files.fetch_original('https://cdn.example/expired',self.out,'renew.apk',len(DATA),lambda n:None,lambda:False,
                          checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True,
                          renew_url=lambda:('https://cdn.example/refreshed',{}))
  self.assertEqual(p.read_bytes(),DATA);self.assertIn('https://cdn.example/refreshed',urls)
 def test_chunked_expired_url_refresh_is_shared_and_verified(self):
  original=files.open_public;calls=[0];renewed=[];lock=threading.Lock()
  def expiration(url,*args,**kwargs):
   with lock:calls[0]+=1;first=calls[0]==1
   if not first and url.endswith('/file'):raise files.TransferHTTPError(403)
   return original(url,*args,**kwargs)
  def renew():renewed.append(True);return 'https://cdn.example/refreshed',{}
  with patch.object(files,'open_public',side_effect=expiration):
   p=files.fetch_original('https://cdn.example/file',self.out,'renew.apk',len(DATA),lambda n:None,lambda:False,
                          checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True,workers=4,renew_url=renew)
  self.assertEqual(p.read_bytes(),DATA);self.assertEqual(len(renewed),1)
 def test_cloud_optional_small_ranges_validate_complete_original(self):
  p=files.fetch_original('https://cdn.example/file',self.out,'cloud.apk',len(DATA),lambda n:None,lambda:False,
                         checksums={'sha1':hashlib.sha1(DATA).hexdigest()},workers=4,prefer_chunks=True)
  self.assertEqual(p.read_bytes(),DATA)
  self.assertTrue(all(r and int(r.split('-')[1])-int(r.split('=')[1].split('-')[0])+1<=4*1024**2 for r in self.calls))
  self.assertFalse(list(self.out.glob('*.part*')))
 def test_cloud_optional_ranges_ignored_keep_complete_full_fallback(self):
  self.mode='ignored'
  p=files.fetch_original('https://cdn.example/file',self.out,'cloud.apk',len(DATA),lambda n:None,lambda:False,
                         checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True)
  self.assertEqual(p.read_bytes(),DATA)
 def test_cloud_optional_ranges_without_version_or_source_hash_use_full_stream(self):
  self.mode='sequential-no-validator'
  p=files.fetch_original('https://cdn.example/file',self.out,'cloud.apk',len(DATA),lambda n:None,lambda:False,prefer_chunks=True)
  self.assertEqual(p.read_bytes(),DATA);self.assertIn(None,self.calls)
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
    self.send_header('Connection','close')
    if test.mode!='sequential-no-validator':self.send_header('ETag','"changed-version"' if test.mode in ('changed','sequential-change') and start else '"same-version"')
    if ranged:self.send_header('Content-Range',f'bytes {start+1 if test.mode=="invalid" else start}-{end}/{len(DATA)}')
    self.end_headers()
    if test.mode=='baidu-break' and start==0 and len([h for h in test.calls if h and h.startswith('bytes=0-')])==2:data=data[:512*1024]
    if test.mode.startswith('sequential-') and len(test.calls)==1:data=data[:512*1024]
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
 def fetch_sequential(self):
  from types import SimpleNamespace
  with patch.object(files.shutil,'disk_usage',return_value=SimpleNamespace(free=512*1024**2+len(DATA)*1.5)):
   return files.fetch_original('https://cdn.example/original',self.out,'sequential.mp4',len(DATA),lambda n:None,lambda:False)
 def test_single_stream_interruption_resumes_same_object(self):
  self.mode='sequential-break';self.assertEqual(self.fetch_sequential().read_bytes(),DATA)
  self.assertIn('bytes=524288-',self.calls);self.assertFalse(list(self.out.glob('*.part*')))
 def test_single_stream_changed_version_cannot_complete(self):
  self.mode='sequential-change'
  with self.assertRaisesRegex(ValueError,'版本'):self.fetch_sequential()
  self.assertFalse(list(self.out.iterdir()))
 def test_single_stream_without_validator_restarts_from_zero(self):
  self.mode='sequential-no-validator';self.assertEqual(self.fetch_sequential().read_bytes(),DATA)
  self.assertEqual(self.calls,[None,None]);self.assertFalse(list(self.out.glob('*.part*')))
class PublicImages(unittest.TestCase):
 def test_best_exposed_image_variant(self):
  state={'note':{'noteDetailMap':{'id':{'note':{'type':'normal','title':'图文','imageList':[{'urlPre':'https://cdn.xhscdn.com/tiny','urlDefault':'http://cdn.xhscdn.com/full'}]}}}}}
  self.assertEqual(xhs_images.parse('<script>window.__INITIAL_STATE__='+json.dumps(state)+'</script>'),('图文',['https://cdn.xhscdn.com/full']))
 def test_login_and_foreign_image_origin_rejected(self):
  with self.assertRaises(ValueError):xhs_images.parse('<html>login</html>')
  state={'note':{'noteDetailMap':{'id':{'note':{'imageList':[{'urlDefault':'http://127.0.0.1/private'}]}}}}}
  with self.assertRaises(ValueError):xhs_images.parse('<script>window.__INITIAL_STATE__='+json.dumps(state)+'</script>')
if __name__=='__main__':unittest.main()
