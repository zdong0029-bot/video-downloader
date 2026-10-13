"""Author scope, real ending and login persistence regressions."""
import tempfile,unittest,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import profile_browser as p,video_platform_accounts as a

class Recovery(unittest.TestCase):
 def test_xhs_ssr_posts_require_actual_author_and_signature(self):
  target='a'*24;good={'id':'b'*24,'xsecToken':'fixture','noteCard':{'user':{'userId':target},'displayTitle':'first'}}
  bad={'id':'c'*24,'xsecToken':'fixture','noteCard':{'user':{'userId':'other'}}}
  state={'query':{'userId':target},'notes':[good,bad,{'id':'d'*24,'noteCard':{'user':{'userId':target}}}]}
  self.assertEqual([row[0] for row in p.xiaohongshu_state_posts(state,target)],['b'*24])
  state['query']['userId']='other';self.assertEqual(p.xiaohongshu_state_posts(state,target),[])
 def test_rumble_works_only_belong_to_current_channel(self):
  u='https://rumble.com/c/fixture'
  good={'creator':u,'url':'https://rumble.com/v1234-title.html?tracking=1'}
  self.assertEqual(p.rumble_profile_posts([good,dict(good,creator='https://rumble.com/c/other'),dict(good,url='https://evil.test/v1234-title.html')],u),[good])
 def test_rumble_last_page_does_not_skip_or_cross_channel(self):
  u='https://rumble.com/c/fixture';links=[u+'?page=1',u+'?page=2',u+'?page=3']
  self.assertEqual(p.rumble_next_page(links,u),(u+'?page=2',False))
  self.assertEqual(p.rumble_next_page(links,u+'?page=3'),(None,True))
  self.assertEqual(p.rumble_next_page([u+'?page=3'],u),(None,False))
  self.assertEqual(p.rumble_next_page(['https://rumble.com/c/other?page=2'],u),(None,False))
  self.assertEqual(p.rumble_next_page([],u),(None,False))
 def test_bili_real_login_and_host_are_required(self):
  page=SimpleNamespace(url='https://www.bilibili.com/',evaluate=lambda script:False)
  self.assertFalse(a.page_authorized('bilibili',page));page.evaluate=lambda script:True
  self.assertTrue(a.page_authorized('bilibili',page));page.url='https://evil.test/'
  self.assertFalse(a.page_authorized('bilibili',page))
 def test_ks_author_avatar_or_visitor_cookie_is_not_login(self):
  page=SimpleNamespace(url='https://www.kuaishou.com/brilliant',context=SimpleNamespace(cookies=lambda:[]),locator=lambda selector:SimpleNamespace(evaluate_all=lambda script:True))
  self.assertFalse(a.page_authorized('kuaishou',page))
  page.context.cookies=lambda:[{'name':'kuaishou.server.web_st','value':'fixture','expires':-1}]
  page.locator=lambda selector:SimpleNamespace(evaluate_all=lambda script:False)
  self.assertFalse(a.page_authorized('kuaishou',page))
  page.locator=lambda selector:SimpleNamespace(evaluate_all=lambda script:True)
  self.assertTrue(a.page_authorized('kuaishou',page))
 def test_encrypted_sessions_persist_and_new_account_wins_refresh(self):
  with tempfile.TemporaryDirectory() as td,patch.object(a,'ROOT',Path(td)):
   state={'cookies':[{'name':'SESSDATA','value':'fixture-one','domain':'.bilibili.com','path':'/','expires':-1}], 'origins':[]}
   first=a.save('bilibili',state);loaded=a.load('bilibili')
   self.assertEqual(loaded['cookies'][0]['value'],'fixture-one')
   raw=(Path(td)/'bilibili.json').read_text(encoding='utf-8');self.assertNotIn('fixture-one',raw)
   state['cookies'][0]['value']='fixture-two';a.save('bilibili',state)
   self.assertFalse(a.refresh('bilibili',loaded,first));self.assertEqual(a.load('bilibili')['cookies'][0]['value'],'fixture-two')
   revision=a.load('bilibili')['_revision'];a.result('bilibili',True,'passed')
   self.assertEqual(a.load('bilibili')['_revision'],revision)

 def test_iqiyi_waits_for_complete_public_stream_not_early_dash(self):
  from focused_extractors import iqiyi_snapshot_ready,public_iqiyi_rows
  snapshot={'videoInfo':{'payMark':0,'isLock':False},'dashData':{'data':{'program':{'video':[{'bid':600}]}}}}
  self.assertFalse(iqiyi_snapshot_ready(snapshot))
  row={'bid':500,'ml':'https://meta-cdn.video.iqiyi.com/fixture.m3u8','ispre':'1'}
  snapshot['dashData']['data']['program']['video'].append(row)
  self.assertFalse(iqiyi_snapshot_ready(snapshot))
  row['ispre']=0;self.assertTrue(iqiyi_snapshot_ready(snapshot));self.assertEqual(public_iqiyi_rows(snapshot),[row])
  snapshot['videoInfo']['payMark']=1;self.assertTrue(iqiyi_snapshot_ready(snapshot))
  with self.assertRaises(Exception):public_iqiyi_rows(snapshot)

 def test_bili_backup_changes_only_endpoint_of_same_stream(self):
  from focused_extractors import bilibili_official_backups
  good='https://upos-sz-mirror.bilivideo.com/same-original.m4s?signature=fixture'
  row={'id':80,'height':1080,'width':1920,'bandwidth':9000000,'codecs':'avc1','baseUrl':'https://node.mcdn.bilivideo.cn:8082/same.m4s','backupUrl':['https://evil.test/file',good]}
  play={'dash':{'video':[row]}};updated=bilibili_official_backups(play)['dash']['video'][0]
  self.assertEqual(updated['baseUrl'],good)
  self.assertEqual({k:v for k,v in updated.items() if k!='baseUrl'},{k:v for k,v in row.items() if k!='baseUrl'})
  self.assertEqual(play['dash']['video'][0]['baseUrl'],row['baseUrl'])
  row['backupUrl']=['https://bilivideo.com.evil.test/f','https://cdn.bilivideo.com:8082/f']
  self.assertEqual(bilibili_official_backups(play),play)

class YoukuRecovery(unittest.TestCase):
 def test_concrete_author_url(self):
  import youku_profile as y,video_catalog as c
  url='https://www.youku.com/profile/index/?uid=UMTUzODYyNDg0'
  self.assertEqual(y.author_uid(url),'UMTUzODYyNDg0');self.assertTrue(c.is_profile(url))
  for bad in ['https://evil.test/profile/index/?uid=UMTUzODYyNDg0','https://www.youku.com/profile/index/','https://v.youku.com/v_show/id_XNjQyMjM5Mjg5Ng==.html']:
   self.assertIsNone(y.author_uid(bad));self.assertFalse(c.is_profile(bad))
 def test_author_component_scope(self):
  import youku_profile as y
  video={'componentId':'pc-profile-video','type':'video','moduleList':[{'data':{'videoId':'X123456','videoLink':'//v.youku.com/v_show/id_X123456.html','summary':'01:23'}},{'data':{'videoId':'X987654','videoLink':'https://evil.test/v_show/id_X987654.html'}}]}
  d={'componentList':[{'componentId':'pc-profile-recommend','type':'recommend','moduleList':[]},video]}
  rows=y.component_entries(y.video_component(d));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['duration'],83)
  with self.assertRaises(ValueError):y.video_component({'componentList':d['componentList'][:1]})
 def test_successful_empty_page_ends_but_missing_or_error_does_not(self):
  import youku_profile as y
  self.assertIsNone(y.next_component({'message':'success','data':{'componentList':[]}},3))
  for payload in [{'message':'fail','data':{'componentList':[]}},{'message':'success','data':{}},{'message':'success','data':{'componentList':[{'componentId':'pc-profile-video','type':'video','page':2}]}}]:
   with self.assertRaises(ValueError):y.next_component(payload,3)
 def test_limit_on_official_final_page_is_not_complete(self):
  import youku_profile as y
  from contextlib import nullcontext
  component={'componentId':'pc-profile-video','type':'video','more':False,'moduleList':[
   {'data':{'videoId':vid,'videoLink':'https://v.youku.com/v_show/id_'+vid+'.html'}} for vid in ('X123456','X987654')]}
  state={'data':{'userIds':{'uid':'UMTUzODYyNDg0'},'componentList':[component]}}
  response=SimpleNamespace(status=200,text=lambda:'window.__INITIAL_DATA__ = '+json.dumps(state))
  page=SimpleNamespace(url='https://www.youku.com/profile/index/',route=lambda *a:None,goto=lambda *a,**k:response)
  browser=SimpleNamespace(new_context=lambda:SimpleNamespace(new_page=lambda:page),close=lambda:None)
  playwright=SimpleNamespace(chromium=SimpleNamespace(launch=lambda **k:browser))
  with patch('playwright.sync_api.sync_playwright',return_value=nullcontext(playwright)):
   bounded=y.resolve('https://www.youku.com/profile/index/?uid=UMTUzODYyNDg0',limit=1)
   all_rows=y.resolve('https://www.youku.com/profile/index/?uid=UMTUzODYyNDg0',limit=2)
  self.assertEqual(len(bounded['entries']),1);self.assertFalse(bounded['complete']);self.assertTrue(bounded['limited'])
  self.assertEqual(len(all_rows['entries']),2);self.assertTrue(all_rows['complete'])

 def test_jsonp_never_executes(self):
  import youku_profile as y
  raw="/**/ typeof __vd_profile === 'function' && __vd_profile("+json.dumps({'message':'success'})+");"
  self.assertEqual(y.parse_page(raw)['message'],'success')
  for bad in [raw+'alert(1)','other({})','<html>captcha</html>']:
   with self.assertRaises(ValueError):y.parse_page(bad)
 def test_immutable_ssr(self):
  import youku_profile as y
  self.assertEqual(y.initial_data('<script>window.__INITIAL_DATA__ = {"data":{"componentList":[]}};</script>'),{'data':{'componentList':[]}})
  with self.assertRaises(ValueError):y.initial_data('<html>请验证</html>')

if __name__=='__main__':unittest.main()
