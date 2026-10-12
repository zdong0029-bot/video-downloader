"""Protocol/credential/integrity tests; no external service credentials needed."""
import sys,json,tempfile,hashlib,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import video_accounts as account,wechat_channels as channels,baidu_backend
from baidu_share import Share,AuthorizationRequired
class Adapters(unittest.TestCase):
 def test_isaac64_independent_csharp_vectors_and_prefix_boundary(self):
  vectors={0:'e1662af3b7e59867c919ad19055fc8cecea2b154d37e1459b2f96d1da14cef1f',1:'39d98f5b25cc52f0996f7ef9e1022156cb029901be6a11ebbdd7469c6ffd5839',(1<<64)-1:'5afbffd76305e81467f97c6370fa07916e9b197611bf5c357a854eb92a6354a1'}
  for key,digest in vectors.items():
   self.assertEqual(hashlib.sha256(channels.decode_head(bytes(131072),key)).hexdigest(),digest)
  original=bytes(range(256))*600;encoded=channels.decode_head(original,12345)
  self.assertEqual(encoded[131072:],original[131072:]);self.assertEqual(channels.decode_head(encoded,12345),original)
  with self.assertRaises(ValueError):channels.Isaac64(-1)
 def test_dpapi_session_filters_cookies_and_never_publishes_secrets(self):
  with tempfile.TemporaryDirectory() as d,patch.object(account,'STORE',Path(d)/'session.json'):
   account.save({'cookies':[{'domain':'.tencent.com','name':'login','value':'MY_SECRET'},{'domain':'.evil.example','name':'wrong','value':'DROP'}],'origins':[{'origin':'https://evil.example'}]})
   raw=account.STORE.read_text();self.assertNotIn('MY_SECRET',raw);self.assertNotIn('secret',account.status())
   self.assertEqual(len(account.load()['cookies']),1);self.assertEqual(account.load()['origins'],[])
 def test_unconnected_session_does_not_fetch_video(self):
  with patch.object(account,'status',return_value={'connected':False}),patch('requests.Session') as request:
   with self.assertRaisesRegex(ValueError,'尚未连接'):channels.resolve('https://weixin.qq.com/sph/test')
   request.assert_not_called()
 def test_share_encoded_json_list_and_missing_grant(self):
  share=Share('https://pan.baidu.com/s/1example');share.uk=1;share.shareid=2
  sign=Mock();sign.json.return_value={'errno':0,'data':{'sign':'sign','timestamp':1}}
  for value in ([{'fs_id':123,'dlink':'https://cdn.example/original'}],json.dumps([{'fs_id':123,'dlink':'https://cdn.example/original'}])):
   result=Mock();result.json.return_value={'errno':0,'list':value}
   with patch.object(share,'request',side_effect=[sign,result]):self.assertEqual(share.download_url({'fs_id':123}),'https://cdn.example/original')
  result=Mock();result.json.return_value={'errno':0,'list':''}
  with patch.object(share,'request',side_effect=[sign,result]),self.assertRaises(AuthorizationRequired):share.download_url({'fs_id':123})
  share.close()
 def test_share_new_encoding_and_real_login_challenge_are_distinct(self):
  share=Share('https://pan.baidu.com/s/1example');share.uk=1;share.shareid=2
  sign=Mock();sign.json.return_value={'errno':0,'data':{'sign':'sign','timestamp':1}}
  result=Mock();result.json.return_value={'errno':0,'list':'bm90LWpzb24='}
  with patch.object(share,'request',side_effect=[sign,result]),self.assertRaisesRegex(AuthorizationRequired,'编码后的下载响应'):
   share.download_url({'fs_id':123})
  with self.assertRaisesRegex(AuthorizationRequired,'网页验证'):
   share.check({'errno':-20},'test')
  share.close()
 def test_cli_local_encoding_not_misreported_as_cleanup_failure(self):
  text='操作成功，已删除 /网站下载任务/abcdef123456'
  self.assertEqual(baidu_backend.decode_log(text.encode('gb18030')),text)
  self.assertEqual(baidu_backend.decode_log(text.encode('utf-8')),text)
 def test_failed_media_never_becomes_completed_file(self):
  response=Mock();response.getheader.side_effect=lambda k:{'Content-Length':'12','Content-Type':'video/mp4'}.get(k);response.read.side_effect=[b'0000ftyp0000',b'']
  with tempfile.TemporaryDirectory() as d,patch.object(channels,'resolve',return_value={'title':'test','url':'https://cdn.qq.com/media','key':0}),patch.object(channels.file_download,'open_public',return_value=(Mock(),response,'https://cdn.qq.com/media')),patch('subprocess.run',return_value=Mock(returncode=1)):
   with self.assertRaisesRegex(ValueError,'校验失败'):channels.download('https://weixin.qq.com/sph/test',Path(d),lambda *a:None,lambda:False)
   self.assertFalse(list(Path(d).iterdir()))
if __name__=='__main__':unittest.main()
