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
 def test_kuaishou_rest_feed_bound_to_profile(self):
  feed={'result':1,'pcursor':'no_more','feeds':[{'photo':{'id':'3abc123456','caption':'own'},'author':{'id':'authorA'}},{'photo':{'id':'3abc654321'},'author':{'id':'authorB'}}]}
  posts=kuaishou_profile_posts({'user_id':'authorA'},feed,'authorA')
  self.assertEqual([p['id'] for p in posts],['3abc123456'])
  self.assertEqual(kuaishou_profile_posts({'user_id':'authorB'},feed,'authorA'),[])
  self.assertEqual(kuaishou_profile_posts({'user_id':'authorA'},{**feed,'result':401},'authorA'),[])

if __name__=='__main__':unittest.main()
