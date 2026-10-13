"""URL dispatch and canonical work identity regressions; no real accounts."""
import unittest
import video_catalog as catalog
from profile_browser import family_host,work_identity,kuaishou_profile_posts

class ProfileCatalogTests(unittest.TestCase):
 def test_profiles(self):
  cases=['https://www.tiktok.com/@fixture','https://www.youtube.com/@fixture/shorts',
   'https://space.bilibili.com/123/video','https://www.xiaohongshu.com/user/profile/'+'a'*24,
   'https://www.kuaishou.com/profile/abc123','https://www.acfun.cn/u/123',
   'https://haokan.baidu.com/author/123','https://www.mgtv.com/h/123.html',
   'https://v.qq.com/x/cover/abc123.html','https://www.dailymotion.com/NASA',
   'https://www.twitch.tv/fixture/clips','https://weibo.com/u/123?tabtype=video',
   'https://vimeo.com/fixture/videos','https://www.nicovideo.jp/user/123',
   'https://www.ted.com/playlists/123/fixture','https://archive.org/details/fixture',
   'https://list.youku.com/show/id_fixture.html','https://www.iqiyi.com/a_fixture.html',
   'https://www.toutiao.com/c/user/token/fixture/','https://tv.cctv.com/lm/fixture/index.shtml']
  for url in cases:
   with self.subTest(url=url):self.assertTrue(catalog.is_profile(url))
 def test_individual_work_not_profile(self):
  for url in ['https://www.tiktok.com/@fixture/video/1234567890123456789',
   'https://www.youtube.com/watch?v=fixture','https://www.dailymotion.com/video/x123',
   'https://www.kuaishou.com/short-video/3abc123','https://v.qq.com/x/cover/a/b.html',
   'https://www.mgtv.com/b/123/456.html','https://www.acfun.cn/v/ac123']:
   with self.subTest(url=url):self.assertFalse(catalog.is_profile(url))
 def test_single_xhs_work_never_enters_profile_browser(self):
  from unittest.mock import patch
  from profile_browser import resolve as browser_resolve
  for url in ['https://www.xiaohongshu.com/discovery/item/6ac119c000000000014033c23?xsec_token=fixture', 'https://www.xiaohongshu.com/explore/'+'b'*24, 'https://www.youtube.com/watch?v=fixture']:
   self.assertFalse(catalog.is_profile(url))
   with patch('profile_browser.resolve') as resolve:
    with self.assertRaisesRegex(ValueError,'识别失败'):catalog.resolve(url)
    resolve.assert_not_called()
  with self.assertRaisesRegex(ValueError,'识别失败'):browser_resolve('https://www.xiaohongshu.com/discovery/item/'+'b'*24,'xiaohongshu')
 def test_all_connected_platform_work_and_profile_classification(self):
  import json
  from pathlib import Path
  from unittest.mock import patch
  rows=json.loads(Path(__file__).with_name('profile_dispatch_cases.json').read_text(encoding='utf-8'))
  self.assertEqual(len(rows),31)
  for row in rows:
   with self.subTest(platform=row['platform']):
    self.assertEqual(catalog.is_profile(row['profile_url']),row['has_profile'])
    self.assertFalse(catalog.is_profile(row['work_url']))
    # A single work fails before either browser discovery or native extraction.
    with patch('profile_browser.resolve') as browser,patch.object(catalog.yt_dlp,'YoutubeDL') as native:
     with self.assertRaisesRegex(ValueError,'识别失败'):catalog.resolve(row['work_url'])
     browser.assert_not_called();native.assert_not_called()
 def test_platform_entrypoints_and_video_on_profile_are_not_profiles(self):
  for url in ['https://clips.twitch.tv/FaintLightGullWholeWheat','https://www.douyin.com/user/fixture?modal_id=7686067191647997182',
   'https://space.bilibili.com','https://space.bilibili.com/login','https://www.youtube.com/playlist',
   'https://www.youtube.com/@fixture/watch?v=fixture','https://www.instagram.com/accounts',
   'https://www.instagram.com/explore','https://x.com/home','https://twitter.com/i',
   'https://www.twitch.tv/login']:
   with self.subTest(url=url):self.assertFalse(catalog.is_profile(url))
 def test_host_boundary_and_credentials(self):
  for url in ['https://evil-tiktok.com/@x','https://tiktok.com.evil.test/@x',
    'https://user:password@tiktok.com/@x','https://tiktok.com:8000/@x',
    'http://127.0.0.1/profile','file:///c:/secret','https://tiktok.com:bad/@x']:
   with self.subTest(url=url):self.assertFalse(catalog.platform_url(url));self.assertFalse(catalog.is_profile(url))
 def test_weibo_work_not_parent_url(self):
  parent='https://weibo.com/u/123?tabtype=video'
  a=catalog.normalize_entry({'id':'one','display_id':'AbCd1','webpage_url':parent,'extractor_key':'Weibo'},1,parent)
  b=catalog.normalize_entry({'id':'two','display_id':'AbCd2','webpage_url':parent,'extractor_key':'Weibo'},2,parent)
  self.assertEqual(a['url'],'https://weibo.com/123/AbCd1');self.assertNotEqual(a['id'],b['id'])
 def test_full_official_signature_preserved(self):
  url='https://www.xiaohongshu.com/explore/'+'b'*24+'?xsec_token=fixture&xsec_source=pc_user'
  self.assertEqual(catalog.normalize_entry({'url':url},1)['url'],url)
  from focused_extractors import XiaoHongShuIE
  profile_work='https://www.xiaohongshu.com/user/profile/'+'a'*24+'/'+'b'*24+'?xsec_token=fixture&xsec_source=pc_user'
  self.assertEqual(XiaoHongShuIE()._match_id(profile_work),'b'*24)
  self.assertFalse(catalog.is_profile(profile_work))
  self.assertEqual(catalog.normalize_entry({'url':profile_work},1)['url'],profile_work)
 def test_skip_playlist_and_restricted_entries(self):
  self.assertIsNone(catalog.normalize_entry({'_type':'playlist'},1))
  self.assertIsNone(catalog.normalize_entry({'url':'https://www.youtube.com/watch?v=a','availability':'private'},1))
 def test_browser_resource_domains(self):
  self.assertTrue(family_host('p66-plat.wskwai.com','kuaishou'))
  self.assertTrue(family_host('r.img.cctvpic.com','cctv'))
  self.assertFalse(family_host('wskwai.com.evil.test','kuaishou'))
  self.assertFalse(family_host('localhost','kuaishou'))
 def test_xhs_unsigned_hydration_placeholder_is_not_a_work(self):
  bare='https://www.xiaohongshu.com/explore/'+'b'*24
  self.assertIsNone(work_identity(bare,'xiaohongshu'))
  full='https://www.xiaohongshu.com/user/profile/'+'a'*24+'/'+'b'*24+'?xsec_token=fixture'
  self.assertEqual(work_identity(full,'xiaohongshu'),'xiaohongshu:'+'b'*24)
  self.assertEqual(work_identity(full,'xiaohongshu'),work_identity(full.replace('fixture','rotated'),'xiaohongshu'))
 def test_bilibili_official_uploads_bound_to_author(self):
  from profile_browser import bilibili_profile_posts
  rows=[{'bvid':'BV16G411C7Zh','mid':123},{'bvid':'BV16G411C7Zh','mid':999},{'bvid':'garbage','mid':123}]
  payload={'code':0,'data':{'list':{'vlist':rows}}}
  self.assertEqual(bilibili_profile_posts({'mid':['123']},payload,'123'),[rows[0]])
  self.assertEqual(bilibili_profile_posts({'mid':['999']},payload,'123'),[])
  self.assertEqual(bilibili_profile_posts({'mid':['123']},{**payload,'code':-352},'123'),[])
 def test_album_redirect_retries_dom_snapshot(self):
  from types import SimpleNamespace
  from profile_browser import profile_snapshot
  def failure(*args,**kwargs):raise RuntimeError('Execution context was destroyed, most likely because of a navigation')
  page=SimpleNamespace(locator=lambda selector:SimpleNamespace(evaluate_all=failure))
  self.assertEqual(profile_snapshot(page,'a','script'),[])
 def test_kuaishou_rest_feed_bound_to_profile(self):
  feed={'result':1,'pcursor':'no_more','feeds':[{'photo':{'id':'3abc123456','caption':'own'},'author':{'id':'authorA'}},{'photo':{'id':'3abc654321'},'author':{'id':'authorB'}}]}
  posts=kuaishou_profile_posts({'user_id':'authorA'},feed,'authorA')
  self.assertEqual([p['id'] for p in posts],['3abc123456'])
  self.assertEqual(kuaishou_profile_posts({'user_id':'authorB'},feed,'authorA'),[])
  self.assertEqual(kuaishou_profile_posts({'user_id':'authorA'},{**feed,'result':401},'authorA'),[])

if __name__=='__main__':unittest.main()
