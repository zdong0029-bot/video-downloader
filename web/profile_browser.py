"""Enumerate actual official profile links; never use previews as downloads."""
from __future__ import annotations
import hashlib, http.cookiejar, ipaddress, re, socket, time, logging, base64
from urllib.parse import urlsplit, urlencode, parse_qs
from playwright.sync_api import sync_playwright
import video_platform_accounts as accounts
from common import Cancelled

FAMILIES={
 'tiktok':('tiktok.com','tiktokcdn.com','tiktokcdn-us.com','tiktokcdn-eu.com','tiktokv.com','byteoversea.com','ibytedtos.com','ibyteimg.com','ttwstatic.com','muscdn.com'),
 'xiaohongshu':('xiaohongshu.com','xhscdn.com'),
 'kuaishou':('kuaishou.com','kwimgs.com','yximgs.com','yximg.com','kwaicdn.com','ksyuncdn.com','ndcimgs.com','wskwai.com','wsbkwai.com'),
 'acfun':('acfun.cn','aixifan.com','acfun.com','ksyun.com','kuaishou.com','yximgs.com','kwimgs.com'),
 'haokan':('baidu.com','bdstatic.com','baidubce.com'),
 'mgtv':('mgtv.com','hitv.com','imgo.tv','hunantv.com'),
 'tencent':('qq.com','gtimg.cn','gtimg.com','qpic.cn'),
 'cctv':('cctv.com','cntv.cn','cctv.cn','cctvpic.com'),
 'bilibili':('bilibili.com','bilivideo.com','hdslb.com'),
 'iqiyi':('iqiyi.com','qiyi.com','qy.net','iqiyipic.com','iqiyinc.com','71edge.com'),
 'sohu':('sohu.com','sohucs.com','itc.cn'),
 'instagram':('instagram.com','cdninstagram.com','fbcdn.net'),
 'facebook':('facebook.com','fbcdn.net','fbsbx.com'),
 'xigua':('ixigua.com','bdxiguaimg.com','bdxiguastatic.com','byteimg.com','bytedance.com'),
 'rumble':('rumble.com','rumble.cloud'),
}

def family_host(host, platform):
 return any(host==h or host.endswith('.'+h) for h in FAMILIES[platform])

def work_identity(link,platform):
 """Stable work identity without stripping the actual playback signature."""
 try:
  p=urlsplit(link)
  if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not family_host(p.hostname or '',platform):return None
 except ValueError:return None
 if platform=='xiaohongshu':
  if not parse_qs(p.query).get('xsec_token'):return None
  if not re.fullmatch(r'/(?:explore|discovery/item|user/profile/[a-f0-9]{24})/[a-f0-9]{24}',p.path):return None
  return 'xiaohongshu:'+p.path.split('/')[-1]
 return p.netloc+p.path+(':'+str(parse_qs(p.query).get('vid',[''])[0]) if platform=='haokan' else '')

def kuaishou_profile_posts(request,data,target,rest=True):
 """Only accept posts returned for the requested profile, not a recommendation."""
 if not isinstance(request,dict):return []
 if rest:
  if request.get('user_id')!=target or data.get('result')!=1:return []
 else:
  if (request.get('variables') or {}).get('userId')!=target:return []
  data=(data.get('data') or {}).get('visionProfilePhotoList') or {}
 posts=[]
 for feed in data.get('feeds') or []:
  photo=feed.get('photo') or {};author=feed.get('author') or {};vid=str(photo.get('id') or '')
  if author.get('id') and str(author['id'])!=target:continue
  if re.fullmatch(r'[A-Za-z0-9]{8,32}',vid):posts.append(photo)
 return posts

def bilibili_profile_posts(query,payload,target):
 """Read only this author's official upload response, never search suggestions."""
 if str((query.get('mid') or [''])[0])!=str(target) or payload.get('code')!=0:return []
 rows=((payload.get('data') or {}).get('list') or {}).get('vlist') or []
 return [row for row in rows if re.fullmatch(r'BV[0-9A-Za-z]{10}',str(row.get('bvid') or '')) and (not row.get('mid') or str(row['mid'])==str(target))]

def xiaohongshu_state_posts(state,target):
 """Profile store includes SSR posts that can leave the virtualized DOM."""
 if not isinstance(state,dict) or (state.get('query') or {}).get('userId')!=target:return []
 posts=[]
 for note in state.get('notes') or []:
  card=note.get('noteCard') or {};user=card.get('user') or {}
  nid=str(note.get('id') or card.get('noteId') or '');token=note.get('xsecToken') or card.get('xsecToken')
  if user.get('userId')!=target or not re.fullmatch(r'[a-f0-9]{24}',nid) or not token:continue
  posts.append((nid,token,card))
 return posts

def rumble_profile_posts(cards,source):
 path=urlsplit(source).path.rstrip('/');posts=[]
 for card in cards:
  owner=urlsplit(card.get('creator') or '')
  p=urlsplit(card.get('url') or '')
  if owner.hostname!='rumble.com' or owner.path.rstrip('/')!=path:continue
  if p.hostname=='rumble.com' and re.fullmatch(r'/v[\w-]+\.html',p.path):posts.append(card)
 return posts

def rumble_next_page(links,current):
 p=urlsplit(current);page=int(parse_qs(p.query).get('page',['1'])[0]);pages={}
 for link in links:
  q=urlsplit(link)
  if q.scheme!='https' or q.hostname!='rumble.com' or q.path.rstrip('/')!=p.path.rstrip('/'):continue
  value=parse_qs(q.query).get('page',[''])[0]
  if value.isdigit():pages[int(value)]=link
 return pages.get(page+1),bool(pages) and not any(n>page for n in pages)

def profile_snapshot(page,selector,script):
 """A normal album redirect can invalidate the DOM while metadata arrives."""
 try:return page.locator(selector).evaluate_all(script)
 except Exception as error:
  if any(word in str(error).lower() for word in ('execution context was destroyed','frame was detached','cannot find context')):return []
  raise

def advance_page(page):
 """Scroll the actual page/content pane, including virtualized author lists."""
 page.evaluate("""() => {
  const pane = Array.from(document.querySelectorAll('*')).filter(e => {
   const r=e.getBoundingClientRect();
   return r.width>innerWidth*.45 && r.height>150 && e.scrollHeight>e.clientHeight+40
    && /auto|scroll/.test(getComputedStyle(e).overflowY) && !e.closest('aside,nav,header');
  });
  for(const e of pane)e.scrollTop=Math.min(e.scrollHeight,e.scrollTop+e.clientHeight*2);
  window.scrollBy(0,Math.max(2400,innerHeight*2));
 }""")

def resolve(url, platform, limit=0, cookie_options=None, on_update=None, cancelled=None):
 from video_catalog import is_profile
 if not is_profile(url):raise ValueError('识别失败：单个作品不能进入作者主页扫描')
 target=urlsplit(url).path.rstrip('/').split('/')[-1].lstrip('@')
 if platform in ('instagram','facebook'):target=urlsplit(url).path.strip('/').split('/')[0]
 if platform=='mgtv':target=re.search(r'/h/(\d+)',url)[1]
 if platform=='tencent':target=re.search(r'/cover/(\w+)',url)[1]
 if platform=='bilibili':target=urlsplit(url).path.strip('/').split('/')[0]
 if platform=='sohu':target=urlsplit(url).path.strip('/').split('/')[1]
 entries,seen=[],set();pagination={'end':False,'blocked':False,'embed':False,'pages':0,'cursor':None,'failure':''}
 def add(link,title='',thumbnail='',duration=0,media_type='video'):
  identity=work_identity(link,platform)
  if not identity:return # SSR placeholders wait for official signed cards.
  # Playback signatures may change between pagination responses. Keep the
  # official signature in the URL, but identify the work by its stable path.
  if identity in seen:
   existing=next(e for e in entries if e['id']==hashlib.sha256(identity.encode()).hexdigest()[:16])
   if platform=='xiaohongshu':existing['url']=link
   if title and existing['title'].startswith('作品 '):existing['title']=str(title)[:500]
   if duration and not existing['duration']:existing['duration']=duration
   return
  if limit and len(entries)>=limit:return
  seen.add(identity)
  entries.append({'id':hashlib.sha256(identity.encode()).hexdigest()[:16],'url':link,'title':str(title or '作品 '+str(len(entries)+1))[:500],'thumbnail':thumbnail if str(thumbnail).startswith('https://') else '', 'duration':duration or 0,'media_type':media_type})
  if on_update and (len(entries)<=3 or len(entries)%20==0):on_update(entries,platform+' 主页')
 def respond(resp):
  path=urlsplit(resp.url).path
  if platform=='tiktok' and path=='/api/post/item_list/':
   if resp.status!=200:pagination['blocked']=True;return
   try:
    d=resp.json()
    if d.get('statusCode') not in (None,0):pagination['blocked']=True;return
    pagination['pages']+=1;pagination['cursor']=d.get('cursor')
    for item in d.get('itemList') or []:
     author=(item.get('author') or {}).get('uniqueId','')
     if author.casefold()!=target.casefold():continue
     vid=str(item.get('id',''));video=item.get('video') or {}
     if re.fullmatch(r'\d{15,25}',vid):add('https://www.tiktok.com/@'+target+'/video/'+vid,item.get('desc'),video.get('cover'),video.get('duration'))
    pagination['end']=d.get('hasMore') is False
   except Exception:pagination.update(blocked=True,failure='TikTok 官方作者列表返回空响应或非 JSON；当前会话/线路未通过列表请求，不能把推荐流作为作者作品')
  elif platform=='xiaohongshu' and ('/user_posted' in path or '/user/posted' in path):
   try:
    if parse_qs(urlsplit(resp.url).query).get('user_id',[''])[0]!=target:return
    payload=resp.json()
    if payload.get('code')!=0 or payload.get('success') is False:pagination['blocked']=True;return
    d=payload.get('data') or {};pagination['pages']+=1;pagination['cursor']=d.get('cursor')
    # The official profile store retains SSR and paginated posts together.
    # Read that complete author-scoped store below, rather than mixing
    # potentially different SSR/response snapshots or virtualized DOM cards.
    pagination['end']=d.get('has_more') is False
   except Exception:pass
  elif platform=='kuaishou' and path in ('/graphql','/rest/v/profile/feed'):
   try:
    req=resp.request.post_data_json
    if not isinstance(req,dict):return
    if path=='/rest/v/profile/feed':
     if req.get('user_id')!=target:return
     data=resp.json()
     if data.get('result')!=1:
      pagination.update(blocked=True,failure='快手官方作者列表拒绝当前会话，返回代码 '+str(data.get('result'))+'；已有 Cookie 不代表官网当前接受登录，请在官方账号入口检测授权')
      return
    else:
     if (req.get('variables') or {}).get('userId')!=target:return
     data=(resp.json().get('data') or {}).get('visionProfilePhotoList') or {}
    payload=resp.json()
    pagination['pages']+=1;pagination['cursor']=data.get('pcursor')
    for photo in kuaishou_profile_posts(req,payload,target,path=='/rest/v/profile/feed'):
     vid=str(photo.get('id') or '')
     if re.fullmatch(r'[A-Za-z0-9]{8,32}',vid):add('https://www.kuaishou.com/short-video/'+vid,photo.get('caption'),photo.get('coverUrl'),float(photo.get('duration') or 0)/1000)
    pagination['end']=data.get('pcursor') in ('no_more','noMore')
   except Exception:pass
  elif platform=='bilibili' and path=='/x/space/wbi/arc/search':
   try:
    query=parse_qs(urlsplit(resp.url).query)
    if query.get('mid',[''])[0]!=target:return
    payload=resp.json()
    if payload.get('code')!=0:
     pagination.update(blocked=True,failure='B站官方作者列表要求登录或安全验证，返回代码 '+str(payload.get('code'))+'；不会改抓其他作者或推荐列表')
     return
    data=payload.get('data') or {};meta=data.get('page') or {};pn=int(meta.get('pn') or query.get('pn',['1'])[0]);size=int(meta.get('ps') or 0);total=meta.get('count')
    pagination['pages']=max(pagination['pages'],pn);pagination['cursor']=pn
    for item in bilibili_profile_posts(query,payload,target):
     thumbnail=str(item.get('pic') or '')
     if thumbnail.startswith('//'):thumbnail='https:'+thumbnail
     length=str(item.get('length') or '');duration=sum(int(v)*60**i for i,v in enumerate(reversed(length.split(':')))) if re.fullmatch(r'\d+(?::\d+){1,2}',length) else 0
     add('https://www.bilibili.com/video/'+item['bvid'],item.get('title'),thumbnail,duration)
    pagination['end']=total is not None and size>0 and pn*size>=int(total)
   except Exception:pass
  elif platform=='haokan' and path=='/web/author/listall':
   try:
    if parse_qs(urlsplit(resp.url).query).get('app_id',[''])[0]!=target:return
    data=resp.json().get('data') or {}
    pagination['pages']+=1;pagination['cursor']=data.get('page')
    for feed in data.get('results') or []:
     item=feed.get('content') or {};vid=str(item.get('vid',''))
     if not re.fullmatch(r'\d{10,25}',vid):continue
     duration=str(item.get('duration') or '')
     seconds=sum(int(v)*60**i for i,v in enumerate(reversed(duration.split(':')))) if re.fullmatch(r'\d+(?::\d+){1,2}',duration) else 0
     add('https://haokan.baidu.com/v?vid='+vid,item.get('title'),item.get('cover_src_pc'),seconds)
    pagination['end']=data.get('has_more') in (False,0)
   except Exception:pass
  elif platform=='tencent' and path=='/vinfo_proxy':
   try:
    req=resp.request.post_data_json
    if not isinstance(req,dict) or req.get('cid')!=target:return
    cover=(resp.json().get('data') or {}).get('coverInfo') or {}
    vids=cover.get('video_ids') or []
    for index,vid in enumerate(vids):
     if re.fullmatch(r'\w{11}',str(vid)):add('https://v.qq.com/x/cover/'+target+'/'+vid+'.html',str(cover.get('title') or '')+' 第'+str(index+1)+'集')
    pagination['end']=len(vids)==int(cover.get('episode_all') or 0)
   except Exception:pass
  elif platform=='iqiyi' and path=='/tvg/v2/lw/base_info':
   try:
    data=resp.json().get('data') or {};base=data.get('base_data') or {}
    if urlsplit(base.get('album_page_url') or '').path!=urlsplit(url).path:return
    for tab in (data.get('template') or {}).get('tabs') or []:
     for block in tab.get('blocks') or []:
      if block.get('bk_id')!='source_selector_bk':continue
      declared=((block.get('data') or {}).get('video_list_params') or {}).get('more',{}).get('total')
      block_rows=[]
      for group in (block.get('data') or {}).get('data') or []:
       for season in group.get('videos') or []:
        for item in season.get('data') or []:
         link=item.get('page_url') or ''
         if re.fullmatch(r'/v_\w+\.html',urlsplit(link).path):add(link,item.get('title') or item.get('short_title'),item.get('image_url'));block_rows.append(link)
      if isinstance(declared,int) and declared>0 and len(set(block_rows))==declared and (not limit or len(entries)<limit):
       pagination.update(end=True,pages=1,cursor=declared)
   except Exception:pass
 with sync_playwright() as p:
  launch={'headless':True}
  # This host already uses a local SOCKS route for foreign platforms. Keep it
  # local to profile discovery; do not change Windows networking or media quality.
  proxied=False
  if platform in ('tiktok','instagram','facebook','rumble'):
   try:
    with socket.create_connection(('127.0.0.1',10808),timeout=.3):pass
    launch['proxy']={'server':'socks5://127.0.0.1:10808'};proxied=True
   except OSError:pass
  browser=p.chromium.launch(**launch)
  try:
   saved=accounts.load(platform) if platform in accounts.PROVIDERS else None
   opts={'storage_state':{'cookies':accounts.live_cookies(saved),'origins':saved['origins']}} if saved else {}
   if saved and saved.get('ua'):opts['user_agent']=saved['ua']
   ctx=browser.new_context(**opts)
   cookie_path=(cookie_options or {}).get('cookiefile')
   if cookie_path:
    jar=http.cookiejar.MozillaCookieJar(cookie_path)
    try:
     jar.load(ignore_discard=True,ignore_expires=False)
     cookies=[{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path or '/','secure':bool(c.secure),'expires':c.expires or -1} for c in jar if family_host(c.domain.lstrip('.'),platform)]
     if cookies:ctx.add_cookies(cookies)
    except (OSError,http.cookiejar.LoadError):pass
   checked={}
   def route(r):
    q=urlsplit(r.request.url);host=q.hostname or ''
    if r.request.resource_type in ('script','xhr','fetch') and (q.scheme!='https' or not family_host(host,platform)):
     logging.getLogger(__name__).debug('Blocked resource %s %s %s',platform,host,q.path)
    if q.scheme!='https' or not family_host(host,platform) or r.request.resource_type in ('media','image','font'):r.abort();return
    if host not in checked:
     try:
      # When SOCKS resolves the official host remotely, an unusable local
      # Teredo AAAA record must not invalidate its otherwise public IPv4 DNS.
      records=socket.getaddrinfo(host,443,family=socket.AF_INET if proxied else socket.AF_UNSPEC,type=socket.SOCK_STREAM)
      checked[host]=bool(records) and all(ipaddress.ip_address(a[4][0]).is_global for a in records)
     except OSError:checked[host]=False
    if checked[host]:r.continue_()
    else:r.abort()
   ctx.route('**/*',route);page=ctx.new_page();page.on('response',respond)
   # Profile discovery needs links and metadata, not thumbnail/font transfers.
   # Commit first; the bounded loop below waits for the actual work list.
   page.goto(url,wait_until='domcontentloaded' if platform=='acfun' else 'commit',timeout=30000)
   started=time.monotonic();expected_total=None;mgtv_loaded=False;wall=started+3600;stale=0;old=0;next_from=None;next_at=0;visited_pages=set()
   while time.monotonic()<wall and (not limit or len(entries)<limit):
    if cancelled and cancelled():raise Cancelled()
    page.wait_for_timeout(800)
    if platform=='tiktok':selector='a[href*="/@'+target+'/video/"]'
    elif platform=='xiaohongshu':selector=None
    elif platform=='acfun':selector='.ac-space-contribute-list a.ac-space-video[href*="/v/ac"]'
    elif platform=='haokan':selector='.videolist a.videoitem[href*="vid="]'
    elif platform=='mgtv':selector='a[href*="/b/'+target+'/"]'
    elif platform=='cctv':selector='a[href*="VIDE"][href$=".shtml"]'
    elif platform=='tencent':selector=None
    elif platform=='bilibili':selector='.bili-video-card a[href*="/video/"], .bili-video-card__wrap a[href*="/video/"], .small-item a[href*="/video/"]'
    elif platform=='instagram':selector='a[href*="/'+target+'/reel/"]'
    elif platform=='facebook':selector='a[href*="/'+target+'/videos/"]'
    elif platform=='xigua':selector='.videoitem a[href*="/video/"]'
    elif platform=='rumble':selector=None
    elif platform=='sohu':selector='a[href*="tv.sohu.com/v/"]'
    else:selector=None # GraphQL posts are scoped to the target, never the recommended feed.
    if selector:
     for item in profile_snapshot(page,selector,"els=>els.map(a=>{const row=a.closest('section,li,.scroll-item,.videolist-item,.ac-space-video');return {url:a.href,title:row?.querySelector('.title')?.innerText||a.innerText||row?.innerText,thumb:a.querySelector('img')?.src||row?.querySelector('img')?.src}})"):
      link=item['url'];path=urlsplit(link).path
      if platform=='xiaohongshu':
       if not re.fullmatch(r'/(?:explore|discovery/item)/[a-f0-9]{24}',path) and not re.fullmatch(r'/user/profile/'+re.escape(target)+r'/[a-f0-9]{24}',path):continue
      elif platform=='instagram':
       m=re.fullmatch('/'+re.escape(target)+r'/reel/([\w-]+)/?',path,re.I)
       if not m:continue
       link='https://www.instagram.com/reel/'+m[1]+'/'
      elif platform=='facebook':
       m=re.fullmatch('/'+re.escape(target)+r'/videos/(?:[^/]+/)?(\d+)/?',path,re.I)
       if not m:continue
       link='https://www.facebook.com/'+target+'/videos/'+m[1]+'/'
      elif platform=='xigua':
       m=re.fullmatch(r'/video/(\d{15,25})',path)
       if not m:continue
       # Mobile SSR exposes this author's feed, rather than site recommendations.
       own=page.evaluate("""id=>Object.values(window._SSR_DATA?.data?.loadersData||{}).some(x=>
          Array.isArray(x.data)&&x.data.some(v=>v.action?.type==='USER-PAGE/GETUSERINFO/FULFILLED'&&v.value?.id===id))""",target)
       if not own:continue
       link='https://www.ixigua.com/'+m[1]
      elif platform=='rumble':
       if not re.fullmatch(r'/v[\w-]+\.html',path):continue
       link='https://rumble.com'+path
      elif platform=='acfun' and not re.fullmatch(r'/v/ac\d+(?:_\d+)?',path):continue
      elif platform=='mgtv':
       if not re.fullmatch('/b/'+target+r'/\d+\.html',path):continue
       link='https://www.mgtv.com'+path
      elif platform=='cctv':
       if urlsplit(link).hostname!='tv.cctv.com' or not re.fullmatch(r'/\d{4}/\d{2}/\d{2}/VIDE\w+\.shtml',path):continue
      elif platform=='bilibili':
       match=re.fullmatch(r'/video/(BV\w+|av\d+)/?',path)
       if not match:continue
       link='https://www.bilibili.com/video/'+match[1]
      elif platform=='sohu':
       match=re.fullmatch(r'/v/([\w=+-]+)\.html',path)
       if not match:continue
       try:decoded=base64.urlsafe_b64decode(match[1]+'===').decode()
       except (ValueError,UnicodeError):continue
       scope=urlsplit(url).path.strip('/').split('/')[0]+'/'+target+'/'
       if not decoded.lstrip('/').startswith(scope):continue
       link='https://tv.sohu.com'+path
      add(link,item['title'],item.get('thumb'))
    if platform=='xiaohongshu':
     state=page.evaluate("""() => {const u=window.__INITIAL_STATE__?.user, val=x=>x?.value??x?._value??x;
       return {notes:val(u?.notes)?.[0],query:val(u?.noteQueries)?.[0],status:val(u?.userNoteFetchingStatus)?.[0]};}""")
     for nid,token,card in xiaohongshu_state_posts(state,target):
      cover=card.get('cover') or {}
      add('https://www.xiaohongshu.com/user/profile/'+target+'/'+nid+'?'+urlencode({'xsec_token':token,'xsec_source':'pc_user'}),card.get('displayTitle'),cover.get('urlDefault') or cover.get('url'),media_type='image' if card.get('type')=='normal' else 'video')
     if (state.get('query') or {}).get('userId')==target and state.get('status')=='resolved' and (state.get('query') or {}).get('hasMore') is False:
      pagination['end']=True
    if platform=='rumble':
     if urlsplit(page.url).path.rstrip('/')!=urlsplit(url).path.rstrip('/'):raise ValueError('频道页面发生非目标作者跳转，未继续扫描')
     cards=profile_snapshot(page,'.channel-listing__container rum-video-thumbnail[creator-url]',"els=>els.map(e=>({url:e.getAttribute('url'),creator:e.getAttribute('creator-url'),title:e.getAttribute('video-title'),thumb:e.getAttribute('src'),duration:Number(e.getAttribute('duration'))||0}))")
     for item in rumble_profile_posts(cards,url):add('https://rumble.com'+urlsplit(item['url']).path,item['title'],item.get('thumb'),item['duration'])
    if platform=='sohu' and expected_total is None:
     html=page.content()
     matches=[re.search(r"\bvar\s+"+key+r"\s*=\s*['\"](\d+)['\"]",html) for key in ('pgcount','pgindex','pgsize','pid')]
     if all(matches) and matches[3][1]==target:
      expected_total=int(matches[0][1]);pagination['pages']=int(matches[1][1])
    if platform=='sohu' and expected_total and len(entries)==expected_total:
     pagination['end']=True
    if platform=='sohu' and entries and not pagination['end']:
     controls=page.locator('#pagination_2 a').filter(has_text=re.compile('下一页|下页|Next',re.I))
     if controls.count() and not controls.first.get_attribute('aria-disabled')=='true' and time.monotonic()-next_at>3:
      next_at=time.monotonic();controls.first.click(timeout=4000)
    if platform=='rumble' and entries and stale>=2:
     links=page.locator('.channel-listing__container a[href*="page="]').evaluate_all('els=>els.map(e=>e.href)')
     next_link,ended=rumble_next_page(links,page.url)
     if next_link and next_link!=next_from:
      next_from=next_link;pagination['pages']+=1;page.goto(next_link,wait_until='commit',timeout=30000);stale=0
     elif ended:pagination['end']=True
    if platform=='tencent':
     # The native extractor expects an old class attribute order. Read only
     # the current episode controls; never related/recommended covers.
     for item in page.locator('[data-vid]').evaluate_all("els=>els.filter(a=>/episode|选集/.test(a.className+' '+a.parentElement?.className)).map(a=>({vid:a.dataset.vid,title:a.innerText}))"):
      if re.fullmatch(r'\w{11}',str(item['vid'])):add('https://v.qq.com/x/cover/'+target+'/'+item['vid']+'.html',item['title'])
    if platform=='bilibili' and entries and not pagination['end']:
     controls=page.get_by_role('button',name='下一页',exact=True)
     if controls.count() and not controls.first.is_disabled():
      current=pagination['cursor']
      if current is not None and (current!=next_from or time.monotonic()-next_at>=8):
       next_from=current;next_at=time.monotonic();controls.first.click(timeout=4000);page.wait_for_timeout(1000)
    if platform=='acfun' and entries:
     # This official author page uses numbered pages, not infinite scroll.
     controls=page.locator('.pager__btn__next:visible')
     if controls.count():
      button=controls.first
      disabled=button.get_attribute('disabled') is not None or button.get_attribute('aria-disabled')=='true' or re.search(r'disabl',button.get_attribute('class') or '')
      links=page.locator('.ac-space-contribute-list a.ac-space-video[href*="/v/ac"]').evaluate_all('els=>els.map(e=>e.href)')
      fingerprint=hashlib.sha256('\n'.join(links).encode()).hexdigest() if links else None
      if fingerprint:visited_pages.add(fingerprint);pagination['pages']=len(visited_pages)
      if disabled and fingerprint and fingerprint!=next_from:pagination['end']=True
      elif not disabled and fingerprint and (fingerprint!=next_from or time.monotonic()-next_at>=8):
       next_from=fingerprint;next_at=time.monotonic()
       button.click(timeout=4000);page.wait_for_timeout(1000)
    if len(entries)==old:stale+=1
    else:stale=0;old=len(entries)
    # Slow initial hydration must not be mistaken for an empty creator.
    # Once a real list exists, retain the short pagination idle deadline.
    idle_limit=45 if limit==0 else 10
    if pagination['end'] or stale>=idle_limit and (entries or time.monotonic()-started>=40):break
    advance_page(page)
   body=page.locator('body').inner_text(timeout=5000)
   if platform=='tiktok' and not entries:
    if "Couldn't find this account" in body or '找不到此账号' in body:raise ValueError('TikTok 官网找不到此账号，可能已改名、删除或主页用户名拼写有误；请使用官网仍能打开的作者主页')
    page.goto('https://www.tiktok.com/embed/@'+target,wait_until='domcontentloaded',timeout=30000);page.wait_for_timeout(1800)
    for item in page.locator('a[href*="/video/"]').evaluate_all('els=>els.map(a=>({url:a.href,title:a.innerText}))'):
     if re.fullmatch('/@'+re.escape(target)+r'/video/\d{15,25}',urlsplit(item['url']).path):add(item['url'],item['title'])
    pagination['embed']=True
   if not entries:
    if pagination['failure']:raise ValueError(pagination['failure'])
    if '/login' in page.url:raise ValueError('平台官方主页已跳转登录页，已保存的会话没有被官网接受；主页列表当前不可用')
    if '验证' in body or 'captcha' in body.lower():raise ValueError('平台官方主页要求验证，当前会话未返回作者作品列表；不会把推荐视频当作该主页作品')
    if '/login' in page.url or ('登录' in body and platform=='xiaohongshu' and not accounts.page_authorized(platform,page)):raise ValueError('平台官方主页当前未登录，已保存的授权没有被官网接受')
    raise ValueError('官网未返回可选作品列表；账号可能无公开视频，或当前公开入口不提供列表')
   title=re.sub(r'\s*[-|]\s*(TikTok|小红书|快手|AcFun).*$', '',page.title()) or platform+' 主页'
   if saved and not pagination['blocked'] and accounts.page_authorized(platform,page):
    fresh=ctx.storage_state();fresh['ua']=page.evaluate('navigator.userAgent');accounts.refresh(platform,fresh,saved['_revision'])
  finally:browser.close()
 if platform in ('instagram','facebook','xigua') and not pagination['end']:
  pagination['failure']='已读取官网当前可见的目标作者作品；官网未提供已结束的全部分页，可能需要有效平台登录或客户端作者列表。当前不标为全部'
 limited=bool(pagination['blocked'] or pagination['embed'] or not pagination['end'] or limit and len(entries)>=limit)
 note='TikTok 官方嵌入入口仅返回最近最多 10 条作品，这不是完整主页列表'+('；'+pagination['failure'] if pagination['failure'] else '') if pagination['embed'] else ((pagination['failure'] or '平台分页未确认结束，保留已识别作品，可继续读取') if limited else '')
 return {'title':title,'entries':entries,'limited':limited,'complete':not limited,'pages':pagination['pages'],'completion_reason':'official-end' if not limited else 'recent-embed' if pagination['embed'] else 'unfinished-pagination','note':note,'method':'official-creator-embed' if pagination['embed'] else 'official-profile-browser'}
