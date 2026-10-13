"""Current official Youku author component pagination; no recommendation feed."""
import json,re,time
from urllib.parse import urlsplit,parse_qs,urlencode
from common import Cancelled

def author_uid(url):
 p=urlsplit(url);host=(p.hostname or '').lower();path=p.path.rstrip('/')
 if host!='youku.com' and not host.endswith('.youku.com'):return None
 uid=parse_qs(p.query).get('uid',[''])[0] if path=='/profile/index' else path[3:] if path.startswith('/u/') else ''
 return uid if re.fullmatch(r'U[A-Za-z0-9]{5,80}={0,2}',uid) else None

def initial_data(html):
 m=re.search(r'(?:window\.)?__INITIAL_DATA__\s*=\s*',html)
 if not m:raise ValueError('优酷作者页没有返回官方作品组件，可能要求验证或入口失效')
 return json.JSONDecoder().raw_decode(html[m.end():])[0]

def parse_page(raw):
 raw=raw.strip()
 if raw.startswith('{'):return json.loads(raw)
 m=re.fullmatch(r"/\*\*/\s*typeof __vd_profile === 'function' && __vd_profile\((.*)\);",raw,re.S)
 if not m:raise ValueError('优酷官方分页没有返回有效 JSON/JSONP')
 return json.loads(m[1])

def video_component(data):
 rows=[x for x in data.get('componentList',[]) if x.get('componentId')=='pc-profile-video' and x.get('type')=='video']
 if len(rows)!=1:raise ValueError('优酷官方分页缺少唯一作者视频组件')
 return rows[0]

def next_component(payload,expected_page):
 if payload.get('message')!='success':raise ValueError('官方分页未确认 success')
 data=payload.get('data')or{}
 # The official profile frontend ends successful pagination when the
 # component array is empty. Missing/malformed arrays and HTTP/errors
 # remain failures, even though that frontend also hides those errors.
 if data.get('componentList')==[]:return None
 comp=video_component(data)
 if int(comp.get('page')or 0)!=expected_page:raise ValueError('官方页码不连续')
 return comp

def component_entries(component):
 entries=[]
 for row in component.get('moduleList',[]):
  d=row.get('data')or{};vid=str(d.get('videoId')or'');link=d.get('videoLink')or''
  if link.startswith('//'):link='https:'+link
  p=urlsplit(link)
  if p.scheme not in ('http','https') or p.hostname!='v.youku.com' or p.username or p.password or p.port not in (None,80,443):continue
  if not re.fullmatch(r'X[A-Za-z0-9]{5,80}={0,2}',vid) or p.path!='/v_show/id_'+vid+'.html':continue
  duration=str(d.get('summary')or'');duration=sum(int(n)*60**i for i,n in enumerate(reversed(duration.split(':')))) if re.fullmatch(r'\d+(?::\d+){1,2}',duration) else 0
  entries.append({'id':vid,'url':'https://v.youku.com'+p.path,'title':str(d.get('title')or vid),'thumbnail':d.get('imgUrl')or'','duration':duration})
 return entries

def resolve(url,limit=0,cookie_options=None,on_update=None,cancelled=None):
 uid=author_uid(url)
 if not uid:raise ValueError('识别失败：请提供优酷具体作者主页')
 source='https://www.youku.com/profile/index/?'+urlencode({'uid':uid})
 entries=[];seen=set();cursors=set();pages=0;complete=False;note='';title='优酷作者主页'
 from playwright.sync_api import sync_playwright
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True)
  try:
   ctx=browser.new_context();page=ctx.new_page()
   # Read the immutable official SSR payload, not a UI store which sets
   # more=false even when its pagination request fails.
   page.route('**/*',lambda r:r.continue_() if r.request.resource_type=='document' and urlsplit(r.request.url).hostname=='www.youku.com' else r.abort())
   response=page.goto(source,wait_until='commit',timeout=30000)
   if not response or response.status!=200 or urlsplit(page.url).path.rstrip('/')!='/profile/index':raise ValueError('优酷作者页要求验证或已重定向到其他入口')
   html=response.text()
   if len(html)>8*1024**2:raise ValueError('优酷作者页面超过读取限制')
   state=initial_data(html);data=state.get('data')or{}
   if (data.get('userIds')or{}).get('uid')!=uid:raise ValueError('优酷作者身份与提交的主页不一致')
   comp=video_component(data)
   title=((data.get('pageData')or{}).get('shareInfo')or{}).get('title')or title
   while True:
    if cancelled and cancelled():raise Cancelled()
    pages+=1;new=0;truncated=False
    for entry in component_entries(comp):
     if entry['url'] in seen:continue
     if limit and len(entries)>=limit:truncated=True;break
     seen.add(entry['url']);entry['index']=len(entries)+1;entries.append(entry);new+=1
    if on_update:on_update(entries,title)
    if truncated:note='已达到本次识别数量；当前官方页面仍有未读取作品';break
    if comp.get('more') is False:complete=True;break
    if limit and len(entries)>=limit:note='已达到本次识别数量；官方作者分页尚未结束';break
    cursor=comp.get('nextSession')
    if comp.get('more') is not True or not cursor or cursor in cursors or not new:note='优酷作者分页游标缺失、重复或没有新作品，保留已读取列表';break
    cursors.add(cursor);next_page=int(comp.get('page')or pages)+1
    if pages>1:time.sleep(.5)
    try:
     r=ctx.request.get('https://www.youku.com/profile/profile-data',params={'type':'video','pageNo':next_page,'nextSession':cursor,'uid':uid,'isGray':0,'extend':'{}','callback':'__vd_profile'},headers={'Referer':source},timeout=20000,max_redirects=0)
     if r.status!=200:raise ValueError('官方分页返回 HTTP '+str(r.status))
     raw=r.text()
     if len(raw)>4*1024**2:raise ValueError('官方分页响应超过限制')
     payload=parse_page(raw)
     next_comp=next_component(payload,next_page)
     if next_comp is None:complete=True;break
     comp=next_comp
    except Cancelled:raise
    except Exception as e:note='优酷作者分页未完成：'+str(e)[:180];break
  finally:browser.close()
 if not entries:raise ValueError(note or '优酷作者没有返回可选视频')
 return {'title':title,'entries':entries,'pages':pages,'complete':complete,'limited':not complete,'note':note,'method':'official-youku-author-components','completion_reason':'official-end' if complete else 'unfinished-pagination'}
