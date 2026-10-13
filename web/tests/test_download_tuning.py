import hashlib,json,os,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import download_tuning as tuning
import file_download as files

DATA=os.urandom(40*1024**2+12345)

class FakeResp:
    def __init__(self,body,start,end,total,status=206):
        self.status=status;self._b=memoryview(body);self._h={'Content-Range':f'bytes {start}-{end}/{total}','Content-Length':str(len(body)),'ETag':'"v1"','Content-Type':'application/octet-stream'}
    def getheader(self,k,d=None):return self._h.get(k,d)
    def read(self,n=-1):
        b=self._b[:n] if n>=0 else self._b;self._b=self._b[len(b):];return bytes(b)
    def close(self):pass

class Conn:
    def close(self):pass

class Tuning(unittest.TestCase):
    def test_defaults_and_overrides(self):
        self.assertEqual(tuning.profile('quark'),{'workers':16,'range_size':8*1024**2})
        self.assertEqual(tuning.profile('baidu')['range_size'],4*1024**2)
        with tempfile.TemporaryDirectory() as d:
            cfg=Path(d)/'t.json';cfg.write_text(json.dumps({'quark':{'workers':24,'chunk_mib':12}}))
            with patch.object(tuning,'CONFIG_PATH',cfg):
                self.assertEqual(tuning.profile('quark'),{'workers':24,'range_size':12*1024**2})
                with patch.dict(os.environ,{'DL_WORKERS_QUARK':'999','DL_CHUNK_MIB_QUARK':'0'}):
                    self.assertEqual(tuning.profile('quark'),{'workers':32,'range_size':1024**2})
                with patch.dict(os.environ,{'DL_WORKERS_QUARK':'abc'}):
                    self.assertEqual(tuning.profile('quark')['workers'],24)
        self.assertEqual(tuning.profile('unknown'),{'workers':8,'range_size':8*1024**2})

    def test_throttle_halves_live_connections_and_completes(self):
        lock=threading.Lock();active=[0];peak_after=[0];throttled=[0];state={'after':False}
        def open_public(url,headers=None,offset=0,end=None):
            with lock:
                active[0]+=1
                if state['after']:peak_after[0]=max(peak_after[0],active[0])
                if throttled[0]<3 and offset>0:
                    throttled[0]+=1;active[0]-=1
                    if throttled[0]==3:state['after']=True
                    raise files.TransferHTTPError(429)
            time.sleep(.01)
            body=DATA[offset:end+1]
            r=FakeResp(body,offset,end,len(DATA))
            orig=r.close
            def close():
                with lock:active[0]-=1
            r.close=close
            return None,r,url
        with tempfile.TemporaryDirectory() as d,patch.object(files,'open_public',side_effect=open_public),patch('time.sleep',lambda s:None):
            partial=Path(d)/'x.part'
            files._chunked_original('https://cdn/x',{},partial,len(DATA),'"v1"',lambda n:None,lambda:False,
                                    chunk_size=1024**2,workers=16)
            self.assertEqual(hashlib.sha256(partial.read_bytes()).digest(),hashlib.sha256(DATA).digest())
        self.assertEqual(throttled[0],3)

    def test_large_range_size_is_accepted(self):
        seen=[]
        def chunks(url,headers,partial,total,validator,progress,cancelled,chunk_size,count,renew,checkpoint):
            seen.append((chunk_size,count));partial.write_bytes(DATA);progress(total)
        def open_public(url,headers=None,offset=0,end=None):
            return Conn(),FakeResp(DATA[offset:end+1],offset,end,len(DATA)),url
        with tempfile.TemporaryDirectory() as d,patch.object(files,'open_public',side_effect=open_public),patch.object(files,'_chunked_original',side_effect=chunks):
            files.fetch_original('https://cdn/x',Path(d),'big.bin',len(DATA),lambda n:None,lambda:False,
                                 checksums={'sha1':hashlib.sha1(DATA).hexdigest()},prefer_chunks=True,
                                 range_size=8*1024**2,workers=16)
        self.assertEqual(seen,[(8*1024**2,16)])

if __name__=='__main__':unittest.main()
