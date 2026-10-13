import sys,unittest,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
WEB=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(WEB))
import douyin,media_quality,video_platform_accounts as accounts,file_download,focused_extractors as focused,yt_dlp
class Recovery(unittest.TestCase):
 def test_douyin_only_selects_requested_work_from_mixed_payload(self):
  payload={'aweme_list':[{'aweme_id':'recommended','video':{}},{'aweme_id':'requested','video':{}}]}
  self.assertEqual(douyin._scan(payload,'requested')['aweme_id'],'requested')
  self.assertIsNone(douyin._scan(payload,'missing'))
 def test_douyin_detail_must_match_requested_id(self):
  self.assertIsNone(douyin._scan({'aweme_detail':{'aweme_id':'different'}},'requested'))
 def test_short_body_is_not_successful_douyin_original(self):
  # A valid MP4 signature cannot make a truncated body complete.
  class Response:
   status=200
   def __init__(self):self.read_once=False
   def getheader(self,name):return {'Content-Length':'1024','Content-Type':'video/mp4'}.get(name)
   def close(self):pass
   def read(self,size=-1):
    if self.read_once:return b''
    self.read_once=True;return b'\0\0\0\x20ftyp'+b'\0'*20
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'test.mp4';v=douyin.DouyinVideo('123','test','https://v.douyinvod.com/test.mp4',duration=3600,width=1080,height=1920)
   def open_response(*args):return SimpleNamespace(close=lambda:None),Response(),v.url
   with patch.object(file_download,'open_public',side_effect=open_response),patch('time.sleep'),self.assertRaisesRegex(ValueError,'未标记完成'):
    douyin.download(v,path)
   self.assertFalse(list(Path(td).iterdir()))
 def test_douyin_rejects_lower_quality_completed_file(self):
  response=SimpleNamespace(getheader=lambda name:'20' if name=='Content-Length' else 'video/mp4',close=lambda:None)
  with tempfile.TemporaryDirectory() as td:
   dest=Path(td)/'video.mp4';v=douyin.DouyinVideo('123','test','https://v.douyinvod.com/full.mp4',duration=3600,width=1080,height=1920)
   def fetch(url,folder,name,*args):(folder/name).write_bytes(b'\0\0\0\x20ftyp'+b'\0'*12)
   with patch.object(file_download,'open_public',return_value=(SimpleNamespace(close=lambda:None),response,v.url)),patch.object(file_download,'fetch_original',side_effect=fetch),patch.object(media_quality,'verify',side_effect=ValueError('resolution mismatch')),self.assertRaisesRegex(ValueError,'resolution'):
    douyin.download(v,dest)
   self.assertFalse(dest.exists())
 def test_failed_douyin_replacement_preserves_existing_file(self):
  response=SimpleNamespace(getheader=lambda name:'20' if name=='Content-Length' else 'video/mp4',close=lambda:None)
  with tempfile.TemporaryDirectory() as td:
   dest=Path(td)/'video.mp4';old=b'existing completed original';dest.write_bytes(old)
   v=douyin.DouyinVideo('123','test','https://v.douyinvod.com/full.mp4',duration=3600,width=1080,height=1920)
   def fetch(url,folder,name,*args):(folder/name).write_bytes(b'\0\0\0\x20ftyp'+b'\0'*12)
   with patch.object(file_download,'open_public',return_value=(SimpleNamespace(close=lambda:None),response,v.url)),patch.object(file_download,'fetch_original',side_effect=fetch),patch.object(media_quality,'verify',side_effect=ValueError('incomplete media')),self.assertRaises(ValueError):
    douyin.download(v,dest)
   self.assertEqual(dest.read_bytes(),old);self.assertEqual(list(Path(td).iterdir()),[dest])
 def test_xhs_visitor_cookie_cannot_be_authorized_login(self):
  p=SimpleNamespace(url='https://www.xiaohongshu.com/explore',evaluate=lambda script:False)
  self.assertFalse(accounts.page_authorized('xiaohongshu',p))
  p.evaluate=lambda script:True;self.assertTrue(accounts.page_authorized('xiaohongshu',p))
  p.url='https://evil.example/';self.assertFalse(accounts.page_authorized('xiaohongshu',p))
 def test_long_middle_decode_failure_is_not_completed(self):
  data={'format':{'duration':'3600'},'streams':[{'codec_type':'video','width':1080,'height':1920},{'codec_type':'audio'}]}
  good=SimpleNamespace(returncode=0,stdout=json.dumps(data).encode());broken=SimpleNamespace(returncode=1,stdout=b'')
  with patch.object(media_quality.shutil,'which',side_effect=lambda name:name),patch.object(media_quality.subprocess,'run',side_effect=[good,good,broken]),self.assertRaisesRegex(ValueError,'中段'):
   media_quality.verify('video.mp4',{'width':1080,'height':1920,'duration':3600})
 def test_long_original_checks_three_positions_without_encoding(self):
  data={'format':{'duration':'3600'},'streams':[{'codec_type':'video','width':1080,'height':1920},{'codec_type':'audio'}]};good=SimpleNamespace(returncode=0,stdout=json.dumps(data).encode())
  with patch.object(media_quality.shutil,'which',side_effect=lambda name:name),patch.object(media_quality.subprocess,'run',return_value=good) as run:
   media_quality.verify('video.mp4',{'width':1080,'height':1920,'duration':3600})
  cmds=[call.args[0] for call in run.call_args_list];self.assertEqual(len(cmds),4)
  self.assertEqual([float(cmd[cmd.index('-ss')+1]) for cmd in cmds[1:]],[0,1800,3595])
  self.assertTrue(all(cmd[-2:]==['null','-'] for cmd in cmds[1:]))
 def test_xhs_long_media_duration_is_retained_for_completeness(self):
  note={'video':{'mediaV2':json.dumps({'stream':{'EF5':[{'master_url':'https://v.xhscdn.com/full.m4s','width':1920,'height':1080,'duration':3952500}]}})}}
  html='<script>window.__INITIAL_STATE__='+json.dumps({'note':{'noteDetailMap':{'test':{'note':note}}}})+'</script>'
  def original(ie,url):ie._note_page=html;return {'id':'test','formats':[]}
  with yt_dlp.YoutubeDL({'quiet':True}) as ydl,patch.object(focused.OriginalXiaoHongShuIE,'_real_extract',new=original):
   info=focused.XiaoHongShuIE(ydl)._real_extract('https://www.xiaohongshu.com/explore/test')
  self.assertEqual(info['duration'],3952.5);self.assertEqual(info['formats'][0]['width'],1920)
if __name__=='__main__':unittest.main()
