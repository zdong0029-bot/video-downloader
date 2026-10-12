"""Regressions for exact media, full streams, and private session storage."""
import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qsl
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yt_dlp
import focused_extractors as focused
import video_platform_accounts as accounts
import xhs_images
import media_quality
from yt_dlp.utils import ExtractorError

class Focused(unittest.TestCase):
    def test_haokan_exact_id_full_quality_and_no_preview(self):
        meta={'id':'123','title':'test','6s_play_url':'https://v.bdstatic.com/preview.mp4','playurl':'https://v.bdstatic.com/full.mp4',
              'clarityUrl':[{'key':'sd','url':'https://v.bdstatic.com/sd.mp4','vodVideoHW':'360$$640'}, {'key':'hd','url':'https://v.bdstatic.com/hd.mp4','vodVideoHW':'720$$1280'}]}
        result=focused.haokan_formats(meta,'123')
        self.assertEqual(max(f['height'] for f in result),720)
        self.assertFalse(any('preview' in f['url'] for f in result))
        with self.assertRaises(ExtractorError):focused.haokan_formats(meta,'456')

    def test_xigua_id_is_not_truncated(self):
        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            ie = focused.IxiguaIE(ydl)
            for url in ('https://www.ixigua.com/6764662548781335052', 'https://m.ixigua.com/video/6764662548781335052?source=share'):
                self.assertEqual(ie._match_id(url), '6764662548781335052')


    def test_xigua_modern_metadata_is_not_a_completed_video(self):
        state={'data':{'storeState':{'detail':{'videoData':{'result':{'gid':'123','definition':['720p','1080p'],'url':'not-a-playable-url'},'error':None}}}}}
        page='<script>window._SSR_DATA = '+json.dumps(state)+'</script>'
        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            ie=focused.IxiguaIE(ydl)
            with self.assertRaisesRegex(ExtractorError,'官方App'):
                ie._get_json_data(page,'123')
            with self.assertRaisesRegex(ExtractorError,'不一致'):
                ie._get_json_data(page,'456')

    def test_xigua_old_player_metadata_remains_available(self):
        state={'anyVideo':{'gidInformation':{'packerData':{'video':{'title':'old playable video'}}}}}
        page='<script id="SSR_HYDRATED_DATA">window._SSR_HYDRATED_DATA='+json.dumps(state)+'</script>'
        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            self.assertEqual(focused.IxiguaIE(ydl)._get_json_data(page,'123'),state)

    def test_official_domains_do_not_accept_deceptive_or_private_urls(self):
        for value in ('https://bdstatic.com.evil.example/file','http://127.0.0.1/file','https://user:pass@bdstatic.com/file','https://bdstatic.com:8000/file'):
            self.assertFalse(focused.official_url(value,focused.FAMILIES['haokan']))

    def test_iqiyi_paid_locked_preview_never_selected(self):
        row={'bid':500,'ml':'https://cache.video.iqiyi.com/test.m3u8','duration':2}
        snapshot={'videoInfo':{},'dashData':{'data':{'program':{'video':[row]}}}}
        self.assertEqual(focused.public_iqiyi_rows(snapshot),[row])
        for info in ({'payMark':1},{'isLock':True},{'downloadAllowed':False}):
            with self.assertRaises(ExtractorError):focused.public_iqiyi_rows({**snapshot,'videoInfo':info})
        with self.assertRaises(ExtractorError):focused.public_iqiyi_rows({**snapshot,'dashData':{'data':{'program':{'video':[{**row,'isPreview':True}]}}}})

    def test_iqiyi_preserves_blank_and_exact_provided_signature(self):
        snapshot={'tvid':'123','videoInfo':{'title':'test'},'dashData':{'data':{'program':{'video':[{'bid':500,'duration':2,'ml':'https://cache.video.iqiyi.com/stream.m3u8','ms':'qd_uid=&qd_k=&qd_sc=officialSignature&qd_v=1'}]}}}}
        manifest='#EXTM3U\n#EXTINF:2,\nhttps://data.video.iqiyi.com/segment.ts?qd_v=2\n#EXT-X-ENDLIST'
        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            ie=focused.IqiyiIE(ydl)
            with patch.object(focused,'browser_snapshot',return_value=snapshot),patch.object(ie,'_download_webpage',return_value=manifest):
                result=ie._real_extract('https://www.iqiyi.com/v_test.html')
            decoded=base64.b64decode(result['formats'][0]['url'].split(',',1)[1]).decode()
            segment=next(line for line in decoded.splitlines() if line.startswith('https://'))
            query=dict(parse_qsl(urlsplit(segment).query,keep_blank_values=True))
            self.assertEqual(query,{'qd_v':'2','qd_uid':'','qd_k':'','qd_sc':'officialSignature'})
            for unsafe in ('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://iqiyi.com/key"\nhttps://data.video.iqiyi.com/segment.ts', '#EXTM3U\nhttp://127.0.0.1/internal'):
                with patch.object(focused,'browser_snapshot',return_value=snapshot),patch.object(ie,'_download_webpage',return_value=unsafe),self.assertRaises(ExtractorError):
                    ie._real_extract('https://www.iqiyi.com/v_test.html')

    def test_kuaishou_metadata_id_and_dimensions(self):
        photo={'id':'abc','manifest':{'adaptationSet':[{'representation':[{'id':'hd','url':['https://v.kwaicdn.com/full.mp4'],'width':720,'height':1280,'avgBitrate':1000}]}]}}
        result=focused.kuaishou_formats(photo,{},'abc')
        self.assertEqual((result[0]['width'],result[0]['height']),(720,1280))
        with self.assertRaises(ExtractorError):focused.kuaishou_formats(photo,{},'other')

    def test_authorized_sessions_encrypted_isolated_and_not_published(self):
        state={'ua':'test','cookies':[{'name':'kuaishou.server.web_st','value':'SECRET_AUTH','domain':'.kuaishou.com','path':'/','expires':time.time()+2000}, {'name':'other','value':'DROP','domain':'.evil.example'}],
               'origins':[{'origin':'https://www.kuaishou.com','localStorage':[]},{'origin':'https://kuaishou.com.evil.example','localStorage':[]}]}
        with tempfile.TemporaryDirectory() as directory,patch.object(accounts,'ROOT',Path(directory)):
            accounts.save('kuaishou',state)
            raw=(Path(directory)/'kuaishou.json').read_text(encoding='utf-8')
            self.assertNotIn('SECRET_AUTH',raw)
            self.assertNotIn('secret',accounts.status('kuaishou'))
            self.assertFalse(accounts.status('kuaishou')['verified'])
            restored=accounts.load('kuaishou')
            self.assertEqual(len(restored['cookies']),1);self.assertEqual(len(restored['origins']),1)
            self.assertIsNone(accounts.load('xiaohongshu'))
            with self.assertRaises(ValueError):accounts.save('xiaohongshu',state)
            state['cookies'][0]['name']='kuaishou.server.webday7_st'
            accounts.save('kuaishou',state)
            self.assertEqual(accounts.load('kuaishou')['cookies'][0]['name'],'kuaishou.server.webday7_st')

    def test_xhs_exact_note_and_human_verification(self):
        self.assertTrue(xhs_images.supported('https://xhslink.cn/test'))
        with self.assertRaisesRegex(ValueError,'人工安全验证'):
            xhs_images.parse('<html>IP存在风险 300012</html>')
        page='<script>window.__INITIAL_STATE__='+json.dumps({'note':{'noteDetailMap':{'correct':{'note':{'type':'video'}}}}})+'</script>'
        self.assertIsNone(xhs_images.parse(page,'correct'))
        with self.assertRaises(ValueError):xhs_images.parse(page,'different')

    def test_xhs_separate_original_audio(self):
        note={'video':{'mediaV2':json.dumps({'video':{'drm_type':0},'stream':{'EF5':[{'master_url':'https://v.xhscdn.com/video.m4s','width':1080,'height':1920,'avg_bitrate':1000000,'size':100}]},'audio_stream':{'AAC':[{'master_url':'https://v.xhscdn.com/audio.m4s','audio_codec':'aac','audio_bitrate':128000,'size':20}]}})}}
        result=focused.xhs_media_formats(note)
        picture=next(f for f in result if f['vcodec']!='none');audio=next(f for f in result if f['vcodec']=='none')
        self.assertEqual(picture['height'],1920);self.assertEqual(picture['acodec'],'none')
        self.assertEqual(audio['acodec'],'aac');self.assertEqual(audio['ext'],'m4a')
        with yt_dlp.YoutubeDL({'format':'bv*+ba/b','format_sort':['res','fps','tbr'],'format_sort_force':True,'quiet':True}) as ydl:
            info=ydl.process_ie_result({'id':'test','title':'test','formats':result},download=False)
            self.assertEqual(len(info['requested_formats']),2)
        data=json.loads(note['video']['mediaV2']);data['video']['drm_type']=1;note['video']['mediaV2']=json.dumps(data)
        with self.assertRaises(ExtractorError):focused.xhs_media_formats(note)

    def test_rotation_preserves_verification_expiry_and_newer_manual_account(self):
        cookie={'name':'kuaishou.server.webday7_st','value':'OLD','domain':'.kuaishou.com','path':'/','expires':time.time()+1000}
        state={'cookies':[cookie], 'origins':[], 'ua':'browser'}
        with tempfile.TemporaryDirectory() as directory,patch.object(accounts,'ROOT',Path(directory)):
            accounts.save('kuaishou',state);accounts.result('kuaishou',True,'verified')
            old=accounts.load('kuaishou');rotated={**state,'cookies':[{**cookie,'value':'ROTATED','expires':time.time()+2000}]}
            self.assertTrue(accounts.refresh('kuaishou',rotated,old['_revision']))
            self.assertTrue(accounts.status('kuaishou')['verified'])
            self.assertEqual(accounts.load('kuaishou')['cookies'][0]['expires'],rotated['cookies'][0]['expires'])
            self.assertFalse(accounts.refresh('kuaishou',state,old['_revision']))
            active=accounts.load('kuaishou')
            self.assertFalse(accounts.refresh('kuaishou',{**state,'cookies':[]},active['_revision']))
            self.assertEqual(accounts.load('kuaishou')['_revision'],active['_revision'])
            expired={**cookie,'expires':time.time()-1}
            self.assertEqual(accounts.live_cookies({'cookies':[expired]}),[])

    def test_completed_quality_rejects_smaller_picture_and_missing_original_sound(self):
        from types import SimpleNamespace
        expected={'width':1080,'height':1920,'requested_formats':[{'vcodec':'hevc'},{'vcodec':'none','acodec':'aac'}]}
        def probe(width,audio):
            rows=[{'codec_type':'video','codec_name':'hevc','width':width,'height':width*16//9}]
            if audio: rows.append({'codec_type':'audio','codec_name':'aac'})
            return SimpleNamespace(returncode=0,stdout=json.dumps({'streams':rows}).encode())
        with patch.object(media_quality.shutil,'which',return_value='ffprobe'):
            for response in (probe(720,True),probe(1080,False)):
                with patch.object(media_quality.subprocess,'run',return_value=response),self.assertRaises(ValueError):
                    media_quality.verify('output.mp4',expected)
            with patch.object(media_quality.subprocess,'run',return_value=probe(1080,True)):
                self.assertIn('1080',media_quality.verify('output.mp4',expected))

if __name__=='__main__':unittest.main()
