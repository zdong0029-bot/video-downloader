"""Large originals use authenticated native channels, with integrity gates."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import cloud_backend as cloud

class NativeOriginals(unittest.TestCase):
 def test_quark_web_size_error_uses_original_desktop_channel(self):
  c=cloud.Client('quark',{});calls=[]
  def request(path,body=None,params=None,host=None,extra=None):
   calls.append((path,body,params,extra))
   if params is None:raise cloud.PlatformError('web size limit',23018)
   return {'data':[{'download_url':'https://cdn.quark.cn/original','size':4*1024**3}]}
  try:
   with patch.object(c,'request',side_effect=request):
    url,headers=c.link({'id':'authorized-file','size':4*1024**3},'')
   self.assertEqual(url,'https://cdn.quark.cn/original');self.assertEqual(headers['User-Agent'],cloud.QUARK_DESKTOP_UA)
   self.assertEqual(len(calls),2);self.assertEqual(calls[-1][2],{'entry':'ft'})
   self.assertEqual(calls[-1][1],{'fids':['authorized-file']})
  finally:c.close()
 def test_quark_login_and_permission_errors_do_not_retry_as_desktop(self):
  for error in (cloud.LoginRequired('login expired'),cloud.PlatformError('not owned',32003)):
   c=cloud.Client('quark',{})
   try:
    with patch.object(c,'request',side_effect=error) as request:
     with self.assertRaises(type(error)):c.quark_download('file')
     self.assertEqual(request.call_count,1)
   finally:c.close()
 def test_quark_wrong_original_size_rejected(self):
  c=cloud.Client('quark',{})
  try:
   with patch.object(c,'request',return_value={'data':[{'download_url':'https://cdn.quark.cn/file','size':10}]}):
    with self.assertRaises(ValueError):c.link({'id':'file','size':20},'')
  finally:c.close()
 def test_ali_empty_web_link_uses_own_original_and_keeps_sha1(self):
  c=cloud.Client('ali',{});item={'id':'owned-file','drive_id':'own-drive','size':3*1024**3,'_owned':True,'sha1':'a'*40}
  d={'url':'https://cdn.aliyundrive.cloud/original','size':item['size'],'content_hash_name':'sha1','content_hash':'a'*40}
  try:
   with patch.object(c,'ali_private',return_value={'url':'','cdn_url':'','size':item['size']}),patch.object(c,'ali_auth',return_value={'access_token':'own-test-token'}),patch.object(c,'request',return_value=d) as req:
    url,headers=c.link(item,'')
   self.assertEqual(url,d['url']);self.assertEqual(item['sha1'],'a'*40)
   self.assertEqual(req.call_args.args[0],'/v2/file/get_download_url')
   self.assertEqual(req.call_args.args[1]['file_id'],'owned-file');self.assertNotIn('share_id',req.call_args.args[1])
   self.assertEqual(req.call_args.kwargs['extra']['X-Canary'],'client=windows,app=adrive,version=v6.0.0')
  finally:c.close()
 def test_ali_native_bad_size_or_hash_never_accepted(self):
  for size,sha in ((1,'a'*40),(3*1024**3,'b'*40)):
   c=cloud.Client('ali',{})
   try:
    with patch.object(c,'ali_private',return_value={'url':''}),patch.object(c,'ali_auth',return_value={'access_token':'own-test-token'}),patch.object(c,'request',return_value={'url':'https://cdn.example/file','size':size,'content_hash_name':'sha1','content_hash':sha}):
     with self.assertRaises(ValueError):c.link({'id':'file','drive_id':'drive','size':3*1024**3,'sha1':'a'*40,'_owned':True},'')
   finally:c.close()
 def test_ali_existing_original_url_not_replaced(self):
  c=cloud.Client('ali',{})
  try:
   with patch.object(c,'ali_private',return_value={'url':'https://cdn.example/file','size':1024}),patch.object(c,'request') as request:
    self.assertEqual(c.link({'id':'file','drive_id':'drive','size':1024,'_owned':True},'')[0],'https://cdn.example/file')
    request.assert_not_called()
  finally:c.close()

if __name__=='__main__':unittest.main(verbosity=2)
