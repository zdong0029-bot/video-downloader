"""Synthetic tests for provider isolation, original-only downloads and recovery."""
import base64,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad,unpad
import cloud_providers as p,linkswift_cloud as adapter,linkswift_shares as shares
import cloud_backend as cloud,cloud_accounts as accounts,file_download as files

class Protocols(unittest.TestCase):
 def test_share_password_text_is_bound_to_each_cloud_link(self):
  import ast,re
  source=Path(__file__).with_name('app.py')
  if not source.exists():source=Path(__file__).parent.parent/'app.py'
  tree=ast.parse(source.read_text(encoding='utf-8'))
  nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_extract_links' or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_URL_RE' for t in n.targets)]
  namespace={'re':re,'cloud_backend':cloud,'file_download':files}
  exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
  text='https://cloud.189.cn/t/fixture123 密码:5bgb\nhttps://drive.uc.cn/s/fixture456 提取码:ab12\nhttps://pan.quark.cn/s/fixture789?pwd=keep 提取码:nope'
  links=namespace['_extract_links'](text)
  self.assertEqual(links,['https://cloud.189.cn/t/fixture123?pwd=5bgb','https://drive.uc.cn/s/fixture456?pwd=ab12','https://pan.quark.cn/s/fixture789?pwd=keep'])
 def test_normal_browser_refresh_is_not_overwritten_by_old_request_cookies(self):
  old={'name':'session','value':'old-fixture','domain':'pan.xunlei.com','path':'/','secure':True,'httpOnly':True,'sameSite':'Strict'}
  c=adapter.Client('xunlei',{'cookies':[old]},'bound')
  c.update_browser_state({'cookies':[{**old,'value':'refreshed-fixture'}],'origins':[]})
  with patch.object(accounts,'update_payload') as saved:c.close()
  self.assertEqual(saved.call_args.args[1]['cookies'][0]['value'],'refreshed-fixture')
 def test_xunlei_own_operations_do_not_use_anonymous_share_authorization(self):
  c=adapter.Client('xunlei',{});c._share_headers={'authorization':'anonymous','x-captcha-token':'normal-share','x-client-id':'web-client'}
  with patch.object(c,'auth',return_value={'Authorization':'Bearer bound-account','X-Client-Id':'web-client','X-Device-Id':'own-device'}):
   h=c.xunlei_headers()
  self.assertEqual(h['authorization'],'Bearer bound-account');self.assertEqual(h['x-device-id'],'own-device');self.assertEqual(h['x-captcha-token'],'normal-share');c.close()
 def test_https_pinned_socket_uses_canonical_host(self):
  import socket
  conn=MagicMock();response=MagicMock();response.status=200;conn.getresponse.return_value=response
  with patch.object(files.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]),patch.object(files.socket,'create_connection'),patch.object(files.ssl,'create_default_context'),patch.object(files.http.client,'HTTPConnection',return_value=conn):
   files.open_public('https://cdn.uc.cn/original',{},0,1023)
  self.assertEqual(conn.request.call_args.kwargs['headers']['Host'],'cdn.uc.cn')
  self.assertEqual(conn.request.call_args.kwargs['headers']['Range'],'bytes=0-1023')
 def test_xunlei_cleanup_only_deletes_exact_task_folder_after_host_validation(self):
  import cloud_cleanup as cleanup
  c=MagicMock();job='1'*12;record={'provider':'xunlei','remote':'owned-folder','job':job}
  c.xunlei_list.side_effect=[[{'id':'owned-folder','name':'other personal folder','kind':'drive#folder'}]]
  with self.assertRaisesRegex(ValueError,'名称'):cleanup.perform(record,c)
  c.xunlei_delete_task.assert_not_called();c.reset_mock()
  c.xunlei_list.side_effect=[[{'id':'owned-folder','name':'网站下载任务-'+job,'kind':'drive#folder'}],[]]
  cleanup.perform(record,c)
  c.xunlei_delete_task.assert_called_once_with('owned-folder',job)
 def test_xunlei_cleanup_browser_rejects_unverified_or_unrelated_receipt(self):
  import linkswift_browser as browser,cloud_cleanup as cleanup
  c=adapter.Client('xunlei',{},'bound');job='1'*12
  with patch.object(cleanup,'read',return_value={'provider':'xunlei','account':'other','remote':'owned'}),patch.object(browser,'sync_playwright') as launch:
   with self.assertRaisesRegex(ValueError,'不匹配'):browser.xunlei_delete_task(c,'owned',job)
  launch.assert_not_called()
  with patch.object(cleanup,'read',return_value={'provider':'xunlei','account':'bound','remote':'owned'}),patch.object(cleanup,'ensure_files',side_effect=ValueError('尚未完整')),patch.object(browser,'sync_playwright') as launch:
   with self.assertRaisesRegex(ValueError,'尚未完整'):browser.xunlei_delete_task(c,'owned',job)
  launch.assert_not_called();c.identity=None;c.close()
 def test_uc_transfer_confirms_copied_name_and_size(self):
  import cloud_cleanup as cleanup
  c=adapter.Client('uc',{},'bound-identity');item={'id':'shared-file','relative':'parent/installer.apk','name':'installer.apk','size':4096,'raw':{'share_fid_token':'fixture','pdir_fid':'parent'},'share_id':'share','stoken':'fixture-token'}
  responses=[{'data':{'fid':'task-root'}},{'data':{'fid':'item-folder'}},{'data':{}}]
  with patch.object(c,'verify'),patch.object(c,'request',side_effect=responses) as request,patch.object(c,'quark_list',return_value=[{'fid':'owned-file','file_name':'installer.apk','size':4096}]),patch.object(cleanup,'register') as register:
   copied=c.uc_transfer(item,'1'*12)
  self.assertTrue(copied['_owned']);self.assertEqual(copied['id'],'owned-file');register.assert_called_once_with('1'*12,'bound-identity','uc','task-root')
  self.assertEqual(request.call_args.args[1]['fid_list'],['shared-file']);self.assertEqual(request.call_args.args[1]['to_pdir_fid'],'item-folder')
  with patch.object(c,'request',side_effect=[{'data':{'fid':'item-folder'}},{'data':{}}]),patch.object(c,'verify'),patch.object(c,'quark_list',return_value=[{'fid':'owned-file','file_name':'wrong.apk','size':4096}]):
   with self.assertRaisesRegex(ValueError,'名称'):c.uc_transfer(item,'1'*12)
  c.identity=None;c.close()
 def test_xunlei_transfer_checks_requested_original_and_parent_folder(self):
  import cloud_cleanup as cleanup
  c=adapter.Client('xunlei',{},'bound-identity');item={'id':'source','relative':'model/config.json','name':'config.json','size':4096,'share_id':'share','share_token':'fixture'}
  with patch.object(c,'xunlei_headers',return_value={}),patch.object(c,'api',side_effect=[{'id':'task-root'},{'id':'item-folder'},{'restore_status':'RESTORE_COMPLETE'}]) as api,patch.object(c,'xunlei_list',return_value=[{'id':'owned','kind':'drive#file','name':'config.json','size':4096,'phase':'PHASE_TYPE_COMPLETE'}]),patch.object(cleanup,'register') as register:
   copied=c.xunlei_transfer(item,'2'*12)
  self.assertTrue(copied['_owned']);register.assert_called_once_with('2'*12,'bound-identity','xunlei','task-root')
  self.assertEqual(api.call_args.args[1]['file_ids'],['source']);self.assertEqual(api.call_args.args[1]['parent_id'],'item-folder')
  c.identity=None;c.close()
 def test_tianyi_xml_rejects_entities_and_normalizes_boolean(self):
  self.assertEqual(shares.tianyi_xml(b'<shareVO><isFolder>true</isFolder><fileId>1234</fileId></shareVO>'),{'isFolder':True,'fileId':'1234'})
  with self.assertRaises(ValueError):shares.tianyi_xml(b'<!DOCTYPE x [<!ENTITY x "expanded">]><x>&x;</x>')
 def test_saved_cookie_flags_retained_after_session_update(self):
  state={'cookies':[{'name':'__Host-session','value':'fixture','domain':'drive.uc.cn','path':'/',
    'secure':True,'httpOnly':True,'sameSite':'Strict','expires':2000000000}]}
  c=adapter.Client('uc',state,'identity')
  with patch.object(accounts,'update_payload') as saved:c.close()
  cookie=saved.call_args.args[1]['cookies'][0]
  self.assertTrue(cookie['secure']);self.assertEqual(cookie['sameSite'],'Strict');self.assertEqual(cookie['expires'],2000000000)
 def test_provider_domains_reject_lookalikes_userinfo_and_custom_ports(self):
  examples={'baidu':'pan.baidu.com','ali':'www.alipan.com','quark':'pan.quark.cn','uc':'drive.uc.cn',
   'pan123':'www.123865.com','tianyi':'cloud.189.cn','mobile':'yun.139.com','xunlei':'pan.xunlei.com'}
  for key,host in examples.items():
   self.assertEqual(p.provider('https://'+host+'/s/fixture'),key)
   for bad in ('https://'+host+'.evil.example/s/fixture','https://'+host+'@evil.example/s/fixture',
               'https://'+host+':8080/s/fixture','file://'+host+'/s/fixture'):
    self.assertIsNone(p.provider(bad))
 def test_only_same_provider_cookies_and_local_storage_used(self):
  state={'cookies':[{'name':'own','value':'own-value','domain':'.uc.cn'},
    {'name':'other','value':'other-value','domain':'.quark.cn'},{'name':'too-broad','value':'bad','domain':'.cn'}],
    'origins':[{'origin':'https://www.123pan.com','localStorage':[{'name':'authorToken','value':'other-token'}]}]}
  c=adapter.Client('uc',state)
  try:
   self.assertEqual([x.name for x in c.s.cookies],['own']);self.assertEqual(c.values,{})
   with self.assertRaises(ValueError):c.raw('https://api-pan.xunlei.com/drive/v1/files')
  finally:c.close()
 def test_uc_share_download_bound_to_tokens_and_exact_size(self):
  c=adapter.Client('uc',{})
  try:
   item={'id':'shared-file','size':4096,'share_id':'shared-id','stoken':'fixture-stoken','raw':{'share_fid_token':'fixture-fid'}}
   with patch.object(c,'api',return_value={'data':[{'download_url':'https://cdn.uc.cn/original','size':4096}]}) as api:
    url,h=c.link(item)
   self.assertEqual(api.call_args.args[1],{'fids':['shared-file'],'fids_token':['fixture-fid'],'pwd_id':'shared-id','stoken':'fixture-stoken'})
   self.assertEqual(url,'https://cdn.uc.cn/original');self.assertEqual(h['_cookie_domains'],('uc.cn',))
   self.assertNotIn('Cookie',files._download_headers('cdn.quark.cn',h,0))
   with patch.object(c,'api',return_value={'data':[{'download_url':'https://cdn.uc.cn/original','size':1024}]}):
    with self.assertRaises(ValueError):c.link(item)
  finally:c.close()
 def test_mobile_uses_download_not_preview_and_requires_own_account(self):
  c=adapter.Client('mobile',{})
  item={'id':'file','share_id':'share','share_password':'pwd'}
  try:
   with self.assertRaises(cloud.LoginRequired):shares.mobile_link(c,item)
   auth=base64.b64encode(b'pc:own-account:own-token').decode();c.s.cookies.set('authorization',auth,domain='.yun.139.com')
   with patch.object(shares,'mobile_call',return_value={'data':{'presentURL':'https://cdn.example/low-quality','extInfo':{'cdnDownloadUrl':'https://cdn.example/original'}}}) as call:
    self.assertEqual(shares.mobile_link(c,item),'https://cdn.example/original')
    body=call.call_args.args[2]['dlFromOutLinkReqV3']
    self.assertEqual(body['account'],'own-account');self.assertEqual(body['coIDLst'],{'item':['file']})
   with patch.object(shares,'mobile_call',return_value={'data':{'presentURL':'https://cdn.example/preview'}}):
    self.assertIsNone(shares.mobile_link(c,item))
  finally:c.close()
 def test_mobile_encrypted_protocol_roundtrip_and_not_secret_logs(self):
  c=adapter.Client('mobile',{});seen=[]
  def raw(url,body=None,params=None,headers=None,form=None):
   data=base64.b64decode(form);clear=unpad(AES.new(shares.MOBILE_KEY,AES.MODE_CBC,data[:16]).decrypt(data[16:]),16)
   seen.append(json.loads(clear));iv=b'0'*16;result=json.dumps({'success':True,'data':{'coLst':[]}}).encode()
   r=MagicMock();r.status_code=200;r.content=base64.b64encode(iv+AES.new(shares.MOBILE_KEY,AES.MODE_CBC,iv).encrypt(pad(result,16)));return r
  try:
   with patch.object(c,'raw',side_effect=raw):d=shares.mobile_call(c,'getOutLinkInfoV6',{'getOutLinkInfoReq':{'linkID':'fixture'}})
   self.assertTrue(d['success']);self.assertEqual(seen,[{'getOutLinkInfoReq':{'linkID':'fixture'}}])
  finally:c.close()
 def test_xunlei_preview_is_never_accepted(self):
  c=adapter.Client('xunlei',{})
  try:
   with patch.object(c,'auth',return_value={}),patch.object(c,'api',return_value={'file_info':{'size':4096,'medias':[{'link':'https://cdn.example/preview'}]}}):
    with self.assertRaises(cloud.PlatformError):c.link({'id':'file','size':4096,'share_id':'share'})
   with patch.object(c,'auth',return_value={}),patch.object(c,'api',return_value={'file_info':{'size':4096,'web_content_link':'https://cdn.example/original'}}) as api:
    self.assertEqual(c.link({'id':'file','size':4096,'share_id':'share'})[0],'https://cdn.example/original')
    self.assertEqual(api.call_args.kwargs['params']['file_id'],'file')
  finally:c.close()
 def test_tianyi_download_signature_bound_to_share_and_original_file(self):
  c=adapter.Client('tianyi',{})
  try:
   with patch.object(c,'tianyi_token',return_value='own-test-token'),patch.object(c,'api',return_value={'fileDownloadUrl':'https://cdn.example/original'}) as api,patch('time.time',return_value=12):
    self.assertEqual(c.link({'id':'file','size':4096,'share_id':'share'})[0],'https://cdn.example/original')
   h=api.call_args.kwargs['headers'];self.assertEqual(h['Signature'],hashlib.md5(b'AccessToken=own-test-token&Timestamp=12000&dt=1&fileId=file&shareId=share').hexdigest())
  finally:c.close()
 def test_123_html_share_and_current_web_endpoint(self):
  c=adapter.Client('pan123',{})
  try:
   with patch.object(c,'pan123_api',return_value={'data':{'InfoList':[{'FileId':1,'FileName':'original.apk','Size':4096,'Type':0,'Etag':'etag','S3KeyFlag':'s3'}],'Next':'-1'}}) as api:
    items=c.prepare('https://www.123pan.com/s/fixture-123.html','')
   self.assertEqual(items[0]['share_id'],'fixture-123');self.assertEqual(api.call_args.kwargs['params']['shareKey'],'fixture-123')
   with patch.object(c,'api',return_value={}) as api:c.pan123_api('/share/get')
   self.assertEqual(api.call_args.args[0],'https://yun.123pan.com/b/api/share/get')
  finally:c.close()
 def test_expired_ali_access_token_refreshes_same_profile_once(self):
  c=cloud.Client('ali',{'_ali_token':{'refresh_token':'old-refresh'}},'bound-identity');c.token={'access_token':'expired','refresh_token':'old-refresh'}
  def response(value):r=MagicMock();r.status_code=200;r.json.return_value=value;return r
  try:
   with patch.object(accounts,'payload',return_value=('ali',b'{"_ali_token":{"refresh_token":"current-refresh","access_token":"expired"}}')) as payload,patch.object(accounts,'update_payload') as saved,patch.object(c.s,'request',side_effect=[response({'code':'AccessTokenExpired'}),response({'access_token':'fresh','refresh_token':'fresh-refresh'}),response({'url':'https://cdn.example/original'})]) as request:
    result=c.request('/v2/file/get_download_url',{'file_id':'file'},extra={'Authorization':'Bearer expired'})
   self.assertEqual(result['url'],'https://cdn.example/original');payload.assert_called_once_with('bound-identity');saved.assert_called_once()
   self.assertEqual(request.call_args.kwargs['headers']['Authorization'],'Bearer fresh')
  finally:c.close()

if __name__=='__main__':unittest.main()
