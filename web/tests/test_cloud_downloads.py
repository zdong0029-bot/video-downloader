"""Isolated cloud protocol, integrity, credentials and homepage integration tests."""
import base64, hashlib, json, shutil, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
WEB=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(WEB))
import cloud_accounts as accounts, cloud_backend as cloud, file_download as files, cloud_cleanup as cleanup
DATA=b'original-installer-file\x00\x01'*8192
class Response:
    def __init__(self,data,status=200):self.data=data;self.status_code=status
    def json(self):return self.data
class Stream:
    status=200
    def __init__(self,data=DATA,headers=None):self.data=data;self.pos=0;self.headers={'Content-Length':str(len(data)),'Content-Type':'application/octet-stream',**(headers or {})}
    def getheader(self,k):return self.headers.get(k)
    def read(self,n):b=self.data[self.pos:self.pos+n];self.pos+=len(b);return b
class Connection:
    def close(self):pass

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cloud-protocol-test-');self.root=Path(self.tmp.name)
        self.original=accounts.DIRECTORY;accounts.DIRECTORY=self.root/'credentials'
        self.old_cleanup=(cleanup.DIRECTORY,cleanup.DOWNLOADS);cleanup.DIRECTORY=self.root/'receipts';cleanup.DOWNLOADS=self.root/'downloads';cleanup.DOWNLOADS.mkdir()
        self.job=cleanup.DOWNLOADS/'abcdef123456';self.job.mkdir()
    def tearDown(self):
        accounts.DIRECTORY=self.original;cleanup.DIRECTORY,cleanup.DOWNLOADS=self.old_cleanup;self.tmp.cleanup()
    def test_encryption_switch_and_public_status(self):
        identity=accounts.save('quark','测试账号',b'{"cookies":[],"origins":[]}',True)
        self.assertNotIn('secret',accounts.listing()['accounts'][0]);self.assertNotIn('cookies',json.dumps(accounts.public_status()))
        self.assertNotIn('cookies',accounts.read()['accounts'][0]['secret'])
        self.assertEqual(accounts.payload(identity)[1],b'{"cookies":[],"origins":[]}')
        with patch('cloud_backend.validate',return_value={}):accounts.activate(identity)
        self.assertTrue(next(p for p in accounts.public_status()['providers'] if p['id']=='quark')['connected'])
        accounts.health(identity,False,'授权过期');self.assertFalse(accounts.public_status()['providers'][2]['auth_ok'])
    def test_process_safe_transactions(self):
        threads=[threading.Thread(target=lambda:accounts.save('ali','账号',b'{}')) for _ in range(12)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(accounts.listing()['accounts']),12)
    def test_original_integrity_and_refuses_bad_size_hash_html(self):
        with patch.object(files,'open_public',return_value=(Connection(),Stream(), 'https://cdn.example/file')):
            p=files.fetch_original('https://cdn.example/file',self.root,'test.apk',len(DATA),lambda n:None,lambda:False,checksums={'md5':hashlib.md5(DATA).hexdigest()})
        self.assertEqual(p.read_bytes(),DATA)
        with patch.object(files,'open_public',return_value=(Connection(),Stream(), 'https://cdn.example/file')):
            with self.assertRaises(ValueError):files.fetch_original('https://cdn.example/file',self.root,'bad.apk',len(DATA)+1,lambda n:None,lambda:False)
        with patch.object(files,'open_public',return_value=(Connection(),Stream(), 'https://cdn.example/file')):
            with self.assertRaises(ValueError):files.fetch_original('https://cdn.example/file',self.root,'hash.apk',len(DATA),lambda n:None,lambda:False,checksums={'sha1':'0'*40})
        with patch.object(files,'open_public',return_value=(Connection(),Stream(headers={'Content-Type':'text/html'}), 'https://cdn.example/file')):
            with self.assertRaises(ValueError):files.fetch_original('https://cdn.example/file',self.root,'html.apk',len(DATA),lambda n:None,lambda:False)
        self.assertFalse(list(self.root.glob('*.part')))
    def test_cookie_not_leaked_on_redirect(self):
        h={'Cookie':'SECRET','Authorization':'SECRET','Referer':'https://pan.quark.cn/'}
        self.assertNotIn('Cookie',files._download_headers('evil.example',h,0));self.assertNotIn('Authorization',files._download_headers('drive.quark.cn',h,0))
        self.assertEqual(files._download_headers('drive.quark.cn',h,123)['Cookie'],'SECRET')
    def test_quark_share_protocol_original_files(self):
        identity=accounts.save('quark','测试',b'{"cookies":[],"origins":[]}',True);accounts.activate(identity,False)
        deleted=[]
        def request(c,path,body=None,params=None,host=None,extra=None):
            if path=='/file/delete':
                self.assertTrue(list(self.job.glob('*.rename')));deleted.append(body['filelist']);return {'data':{'task_id':'delete'}}
            if path=='/share/sharepage/token':return {'data':{'stoken':'test-token'}}
            if path=='/share/sharepage/detail':return {'data':{'list':[{'fid':'source-file','file_name':'base.apk(1).rename','size':len(DATA),'file_type':1,'share_fid_token':'test-fid-token'}]}}
            if path=='/file' :return {'data':{'fid':'test-job-dir'}}
            if path=='/share/sharepage/save':
                self.assertEqual(body['to_pdir_fid'],'test-job-dir');return {'data':{'task_id':'transfer'}}
            if path=='/task':return {'data':{'status':2}}
            if path=='/file/sort':return {'data':{'list':([] if deleted else [{'fid':'test-job-dir','file_name':'网站下载任务-abcdef123456','dir':True}]) if params['pdir_fid']=='0' else [{'fid':'saved-file','file_name':'base.apk(1).rename','size':len(DATA),'file_type':1}]}}
            if path=='/file/download' and body['fids']==['source-file']:raise cloud.PlatformError('Not owned',32003)
            if path=='/file/download':return {'data':[{'download_url':'https://cdn.example/file','md5':hashlib.md5(DATA).hexdigest()}]}
            raise AssertionError(path)
        with patch.object(cloud.Client,'request',request),patch.object(files,'open_public',return_value=(Connection(),Stream(), 'https://cdn.example/file')):
            paths=cloud.run('https://pan.quark.cn/s/3f6359ee6d99','',self.job,lambda a,p:None,lambda:False)
        self.assertEqual(len(paths),1);self.assertEqual(paths[0].read_bytes(),DATA)
        self.assertEqual(deleted,[['test-job-dir']]);self.assertEqual(cleanup.read(self.job.name)['status'],'cleaned')
    def test_ali_share_protocol_and_token_rotation(self):
        state={'cookies':[],'origins':[{'origin':'https://www.alipan.com','localStorage':[{'name':'token','value':json.dumps({'refresh_token':'test-old'})}]}]}
        identity=accounts.save('ali','测试',json.dumps(state).encode(),True);accounts.activate(identity,False)
        def request(c,path,body=None,params=None,host=None,extra=None):
            if path=='/v2/account/token':return {'access_token':'test-access','refresh_token':'test-new'}
            if path=='/v2/share_link/get_share_token':return {'share_token':'test-share'}
            if path=='/adrive/v3/file/list':return {'items':[{'file_id':'file','name':'archive.rar','size':len(DATA),'type':'file','content_hash_name':'sha1','content_hash':hashlib.sha1(DATA).hexdigest()}]}
            if path=='/v2/file/get_share_link_download_url':
                self.assertIn('x-share-token',extra);return {'download_url':'https://cdn.example/file'}
            raise AssertionError(path)
        with patch.object(cloud.Client,'request',request),patch.object(files,'open_public',return_value=(Connection(),Stream(),'https://cdn.example/file')):
            paths=cloud.run('https://www.alipan.com/s/AbCd123456','',self.root,lambda a,p:None,lambda:False)
        self.assertEqual(paths[0].read_bytes(),DATA)
        self.assertEqual(json.loads(accounts.payload(identity)[1])['_ali_token']['refresh_token'],'test-new')
    def test_cloud_urls_and_missing_login(self):
        for url in ['https://pan.quark.cn.attacker.example/s/AbCd123','https://user:pwd@pan.quark.cn/s/AbCd123','https://pan.quark.cn:1234/s/AbCd123','https://pan.quark.cn/list']:
            with self.assertRaises(ValueError):cloud.parse_share(url)
        with self.assertRaises(cloud.LoginRequired):cloud.active_profile('quark')
    def test_auth_error_does_not_expose_credentials(self):
        c=cloud.Client('quark',{})
        with patch.object(c.s,'request',return_value=Response({'status':401,'code':31001,'message':'secret-token'},401)):
            with self.assertRaises(cloud.LoginRequired) as e:c.verify()
        self.assertNotIn('secret-token',str(e.exception));c.close()

    def test_cdn_content_md5_and_safe_resume(self):
        correct=base64.b64encode(hashlib.md5(DATA).digest()).decode()
        with patch.object(files,'open_public',return_value=(Connection(),Stream(headers={'Content-MD5':correct}),'https://cdn.example/file')):
            p=files.fetch_original('https://cdn.example/file',self.root,'cdn.apk',len(DATA),lambda n:None,lambda:False,checksums={'provider_md5':'incorrect-provider-hash'})
        self.assertEqual(p.read_bytes(),DATA)
        with patch.object(files,'open_public',return_value=(Connection(),Stream(headers={'Content-MD5':base64.b64encode(bytes(16)).decode()}),'https://cdn.example/file')):
            with self.assertRaises(ValueError):files.fetch_original('https://cdn.example/file',self.root,'wrong.apk',len(DATA),lambda n:None,lambda:False)
        class Broken(Stream):
            def read(self,n):
                if self.pos:raise ConnectionError('interrupted')
                return super().read(1024)
        calls=[]
        def resumed(url,headers=None,offset=0):
            calls.append(offset)
            if not offset:return Connection(),Broken(headers={'Content-MD5':correct}),'https://cdn.example/file'
            r=Stream(DATA[offset:],{'Content-Range':'bytes '+str(offset)+'-'+str(len(DATA)-1)+'/'+str(len(DATA))});r.status=206
            return Connection(),r,'https://cdn.example/file'
        with patch.object(files,'open_public',side_effect=resumed),patch('time.sleep'):
            p=files.fetch_original('https://cdn.example/file',self.root,'resume.apk',len(DATA),lambda n:None,lambda:False)
        self.assertEqual(calls,[0,1024]);self.assertEqual(p.read_bytes(),DATA)
    def test_failed_download_never_deletes_cloud(self):
        cleanup.register(self.job.name,'old-account','quark','temp-only')
        with patch.object(cleanup,'perform') as delete:
            with self.assertRaises(ValueError):cleanup.retry(self.job.name)
        delete.assert_not_called();self.assertEqual(cleanup.read(self.job.name)['status'],'download_pending')
    def test_cleanup_failure_retry_uses_original_account_and_checks_host_copy(self):
        old=accounts.save('quark','旧账号',b'{"cookies":[]}',True)
        accounts.activate(old,False);cleanup.register(self.job.name,old,'quark','task-folder')
        p=self.job/'file.apk';p.write_bytes(DATA)
        c=cloud.Client('quark',{},old)
        with patch.object(cleanup,'perform',side_effect=ValueError('network error')):
            note=cleanup.complete(self.job,old,'quark','task-folder',[p],c)
        self.assertIn('可在网盘账号区域重试',note)
        new=accounts.save('quark','新账号',b'{"cookies":[]}',True);accounts.activate(new,False)
        seen=[]
        def delete(record,client=None,delete=None):seen.append(client.identity)
        with patch.object(cleanup,'perform',side_effect=delete):result=cleanup.retry(self.job.name)
        self.assertTrue(result['ok']);self.assertEqual(seen,[old])
        # Missing/corrupt local originals must block cleanup before any provider request.
        record=cleanup.read(self.job.name);record['status']='cleanup_pending';cleanup.write(record);p.write_bytes(b'corrupted')
        with patch.object(cleanup,'perform') as delete:
            with self.assertRaises(ValueError):cleanup.retry(self.job.name)
        delete.assert_not_called();c.close()
    def test_task_folder_scope_and_no_remote_ids_public(self):
        for wrong in ['../abcdef123456','x','ABCDEF123456']:
            with self.assertRaises(ValueError):cleanup.receipt_path(wrong)
        with self.assertRaises(ValueError):cleanup.register(self.job.name,'a','baidu','/原有文件')
        cleanup.register(self.job.name,'secret-account','quark','secret-cloud-id')
        public=json.dumps(cleanup.listing());self.assertNotIn('secret-account',public);self.assertNotIn('secret-cloud-id',public)
    def test_own_quark_share_avoids_transfer_and_cleanup(self):
        identity=accounts.save('quark','测试',b'{"cookies":[]}',True);accounts.activate(identity,False)
        def request(c,path,body=None,params=None,host=None,extra=None):
            if path=='/share/sharepage/token':return {'data':{'stoken':'test'}}
            if path=='/share/sharepage/detail':return {'data':{'list':[{'fid':'owned','file_name':'original.apk','size':len(DATA),'file_type':1}]}}
            if path=='/file/sort':return {'data':{'list':[]}}
            if path=='/file/download':return {'data':[{'download_url':'https://cdn.example/file','md5':'not-a-raw-digest'}]}
            raise AssertionError('Own shares must not transfer/delete: '+path)
        with patch.object(cloud.Client,'request',request),patch.object(files,'open_public',return_value=(Connection(),Stream(),'https://cdn.example/file')):
            result=cloud.run('https://pan.quark.cn/s/abcde12345','',self.job,lambda a,p:None,lambda:False)
        self.assertIn('未转存',result.note);self.assertFalse(list(cleanup.DIRECTORY.glob('*.json')))
    def test_baidu_cleanup_requires_success_response(self):
        import baidu_backend as baidu
        import subprocess
        for output,works in [('操作成功, 以下文件/目录已删除 /网站下载任务/abcdef123456',True),('操作失败',False)]:
            with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,output.encode(),b'')):
                if works:baidu.remove_command(Path('pcs.exe'),{},'/网站下载任务/abcdef123456')
                else:
                    with self.assertRaises(ValueError):baidu.remove_command(Path('pcs.exe'),{},'/网站下载任务/abcdef123456')
        with self.assertRaises(ValueError):baidu.remove_command(Path('pcs.exe'),{},'/网站下载任务/*')

    def test_http410_reported_as_platform_error_not_login(self):
        c=cloud.Client('ali',{})
        class Gone:
            status_code=410
            def json(self):
                import requests
                raise requests.exceptions.JSONDecodeError('empty','',0)
        with patch.object(c.s,'request',return_value=Gone()):
            with self.assertRaises(cloud.PlatformError) as error:c.request('/v2/file/get_share_link_download_url',{})
        self.assertEqual(error.exception.code,410);c.close()
    def test_ali_required_transfer_then_download_then_only_temp_delete(self):
        identity=accounts.save('ali','测试',b'{"_ali_token":{"refresh_token":"old"}}',True);accounts.activate(identity,False)
        deleted=[];sequence=[]
        def request(c,path,body=None,params=None,host=None,extra=None):
            sequence.append(path)
            if path=='/v2/account/token':return {'access_token':'access','refresh_token':'new','default_drive_id':'own-drive'}
            if path=='/v2/share_link/get_share_token':return {'share_token':'share'}
            if path=='/adrive/v3/file/list':return {'items':[{'file_id':'source','name':'installer.exe','type':'file','size':len(DATA)}]}
            if path=='/v2/file/get_share_link_download_url':raise cloud.PlatformError('Gone',410)
            if path=='/adrive/v2/file/createWithFolders':
                self.assertEqual(body['name'],'网站下载任务-abcdef123456');return {'file_id':'task-folder'}
            if path=='/adrive/v4/batch':
                self.assertEqual(body['requests'][0]['body']['to_parent_file_id'],'task-folder');return {'responses':[{'status':201,'body':{'file_id':'saved-file'}}]}
            if path=='/v2/file/get':
                if body['file_id']=='saved-file':return {'type':'file','size':len(DATA)}
                if deleted:raise cloud.PlatformError('NotFound', 'NotFound.File')
                return {'type':'folder','name':'网站下载任务-abcdef123456'}
            if path=='/v2/file/get_download_url':return {'url':'https://cdn.example/installer','size':len(DATA),'content_hash_name':'sha1','content_hash':hashlib.sha1(DATA).hexdigest()}
            if path=='/v3/file/delete':
                self.assertEqual(body,{'drive_id':'own-drive','file_id':'task-folder'});self.assertTrue(list(self.job.glob('*.exe')));deleted.append(True);return {}
            raise AssertionError(path)
        with patch.object(cloud.Client,'request',request),patch.object(files,'open_public',return_value=(Connection(),Stream(),'https://cdn.example/installer')):
            result=cloud.run('https://www.alipan.com/s/abc1234567','',self.job,lambda a,p:None,lambda:False)
        self.assertEqual(result[0].read_bytes(),DATA);self.assertEqual(cleanup.read(self.job.name)['status'],'cleaned');self.assertEqual(len(deleted),1)
        self.assertLess(sequence.index('/v2/file/get_download_url'),sequence.index('/v3/file/delete'))

if __name__=='__main__':unittest.main(verbosity=2)
