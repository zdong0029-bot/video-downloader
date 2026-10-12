import base64, json, sys, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from baidu_share import Share, AuthorizationRequired
import media_quality

class BaiduWebTransferTests(unittest.TestCase):
    def client(self):
        return object.__new__(Share)

    def test_normal_web_signature_known_vector(self):
        self.assertEqual(base64.b64decode(Share.web_sign('Key','Plaintext')).hex(),'bbf316e8d940af0ad3')

    def test_inventory_stays_in_exact_temporary_task(self):
        c=self.client();remote='/网站下载任务/0123456789ab'
        directory={'path':remote+'/folder','isdir':1}
        file={'path':remote+'/folder/test.bin','isdir':0,'fs_id':1,'size':2,'server_filename':'test.bin'}
        c.request=Mock(side_effect=[Mock(json=lambda:{'errno':0,'list':[directory]}),Mock(json=lambda:{'errno':0,'list':[file]})])
        self.assertEqual(c.owned_files(remote),[file])
        self.assertEqual(c.request.call_args_list[1].kwargs['params']['dir'],directory['path'])
        for invalid in ('/personal','/网站下载任务/0123456789ab/child'):
            with self.assertRaises(ValueError):c.owned_files(invalid)
        for escaped in ('/personal/file',remote+'/../personal/file'):
            c.request=Mock(return_value=Mock(json=lambda:{'errno':0,'list':[{'path':escaped}]}))
            with self.assertRaises(ValueError):c.owned_files(remote)

    def test_owned_download_matches_requested_id_and_requires_authorization(self):
        c=self.client();token={'bdstoken':'normal','sign1':'Plaintext','sign3':'Key','timestamp':1}
        def respond(rows):
            c.request=Mock(side_effect=[Mock(json=lambda:{'errno':0,'result':token}),Mock(json=lambda:{'errno':0,'dlink':rows})])
        respond([{'fs_id':99,'dlink':'https://example.invalid/unrelated'},{'fs_id':1,'dlink':'https://example.invalid/original'}])
        self.assertEqual(c.owned_download_url({'fs_id':1}),'https://example.invalid/original')
        self.assertEqual(json.loads(c.request.call_args.kwargs['data']['fidlist']),[1])
        respond([{'fs_id':99,'dlink':'https://example.invalid/unrelated'}])
        with self.assertRaises(AuthorizationRequired):c.owned_download_url({'fs_id':1})
        c.request=Mock(return_value=Mock(json=lambda:{'errno':0,'result':{}}))
        with self.assertRaises(AuthorizationRequired):c.owned_download_url({'fs_id':1})

    def test_missing_long_video_segment_rejected_without_lowering_quality(self):
        expected={'width':1920,'height':1080,'duration':7200,'acodec':'aac'}
        def probe(duration):
            return SimpleNamespace(returncode=0,stdout=json.dumps({'format':{'duration':duration},'streams':[{'codec_type':'video','width':1920,'height':1080},{'codec_type':'audio','codec_name':'aac'}]}).encode())
        with patch.object(media_quality.shutil,'which',return_value='ffprobe'):
            with patch.object(media_quality.subprocess,'run',return_value=probe(6800)),self.assertRaises(ValueError):
                media_quality.verify('sample.mp4',expected)
            with patch.object(media_quality.subprocess,'run',return_value=probe(7199)):
                self.assertIn('1920',media_quality.verify('sample.mp4',expected))

if __name__=='__main__':unittest.main()
