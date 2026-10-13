import base64,hashlib,json,os,re,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
BASE=os.environ.get('MOBILE_ALBUM_BASE_URL','http://127.0.0.1:8000')
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'
DATA=bytes(range(256))*20480 # 5 MiB, validates assembled original bytes
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lL8AAAAASUVORK5CYII=')
with sync_playwright() as p:
 b=p.chromium.launch(headless=True);c=b.new_context(viewport={'width':390,'height':844},user_agent=UA,is_mobile=True,has_touch=True);page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 calls=[];mode=['normal'];failed=[False]
 def route(r):
  h=r.request.headers.get('range','');calls.append(h);match=re.fullmatch(r'bytes=(\d+)-(\d+)',h)
  if mode[0]=='ignored':r.fulfill(body=DATA,headers={'Content-Type':'video/mp4','Content-Length':str(len(DATA))});return
  start,end=map(int,match.groups());body=DATA[start:end+1]
  if mode[0]=='retry' and start>0 and not failed[0]:failed[0]=True;r.fulfill(status=503,body='retry');return
  etag='"changed"' if mode[0]=='changed' and start>0 else '"fixture-v1"'
  if mode[0]=='short' and start>0:body=body[:10]
  r.fulfill(status=206,body=body,headers={'Content-Type':'video/mp4','Content-Length':str(len(body)),'Content-Range':f'bytes {start}-{end}/{len(DATA)}','ETag':etag})
 page.route('**/api/files/rangefixture/*',route);page.route('**/api/jobs*',lambda r:r.fulfill(json=[]));page.goto(BASE);page.wait_for_function("typeof fetchAlbumOriginal==='function'");page.evaluate("clearInterval(timer);cache=[{id:'rangefixture',kind:'douyin'}]")
 js='''async item=>{try{const events=[];const response=await fetchAlbumOriginal(item,new AbortController().signal,(n,total)=>events.push([n,total]));const bytes=await response.arrayBuffer();const sha=await crypto.subtle.digest('SHA-256',bytes);return {size:bytes.byteLength,sha:Array.from(new Uint8Array(sha)).map(n=>n.toString(16).padStart(2,'0')).join(''),events};}catch(e){return {error:e.message};}}'''
 for case in ['normal','retry','ignored','changed','short']:
  mode[0]=case;calls.clear();failed[0]=False;result=page.evaluate(js,{'id':'rangefixture','name':case+'.mp4','size':len(DATA)})
  if case in ['normal','retry','ignored']:
   assert 'size' in result,(case,result,calls)
   assert result['size']==len(DATA) and result['sha']==hashlib.sha256(DATA).hexdigest(),(case,result)
   if case=='normal':assert len(calls)==3 and all(calls),calls
   if case=='retry':assert len(calls)==4 and failed[0],calls
   if case=='ignored':assert len(calls)==1,calls
  else:assert 'error' in result,(case,result)
  print('PASS original ranges '+case,flush=True)
 # Album preparation reuses the assembled Blob; cache reopen does not fetch again.
 mode[0]='normal';calls.clear()
 page.evaluate("Object.defineProperty(navigator,'canShare',{configurable:true,value:()=>true});Object.defineProperty(navigator,'share',{configurable:true,value:async()=>{}});cache=[{id:'rangefixture',kind:'douyin',files:[{name:'album.mp4',size:5242880}]}];previewFile('rangefixture','album.mp4')")
 expect(page.locator('#shareVideo')).to_be_enabled();assert len(calls)==3,calls
 page.evaluate("closePreview();previewFile('rangefixture','album.mp4')");expect(page.locator('#shareVideo')).to_be_enabled();assert len(calls)==3,calls;page.evaluate('closePreview()')
 print('PASS large album original preparation and cached reopen')
 # No task POST or parser POST for the workstation URL pasted as media.
 posts=[];page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
 page.fill('#url','https://video-downloader.tail49ff9c.ts.net/?v=20261013-mobile-v4');page.click('#go');expect(page.locator('#hint')).to_contain_text('这是下载工作台');page.click('#scanProfile');expect(page.locator('#hint')).to_contain_text('这是下载工作台');assert not posts
 assert page.evaluate('BrowserEnvironment.mobile') and not page.evaluate('BrowserEnvironment.wechat');assert not errors,errors
 c.close()
 # Embedded WeChat renders actionable guidance without preparing unsupported share data.
 c=b.new_context(viewport={'width':390,'height':844},user_agent=UA+' MicroMessenger/8.0.60',is_mobile=True,has_touch=True)
 c.add_init_script("window.copied='';Object.defineProperty(navigator,'clipboard',{value:{writeText:async t=>{window.copied=t;}}});")
 page=c.new_page();media=[];page.route('**/api/jobs*',lambda r:r.fulfill(json=[]));page.route('**/api/files/wechatfixture/*',lambda r:(media.append(r.request.url),r.fulfill(body=PNG,content_type='image/png'))[-1]);page.goto(BASE);expect(page.locator('#embeddedBrowserHelp')).to_be_visible();page.wait_for_function("typeof previewFile==='function'");page.evaluate("clearInterval(timer);cache=[{id:'wechatfixture',files:[{name:'test.png',size:68}]}];previewFile('wechatfixture','test.png')")
 expect(page.locator('#saveHelp')).to_contain_text('微信');expect(page.locator('#shareVideo')).to_be_disabled();assert not media,media;page.evaluate('closePreview()');page.locator('#embeddedBrowserHelp').get_by_role('button',name='复制网站网址',exact=True).click();assert page.evaluate('copied')=='https://video-downloader.tail49ff9c.ts.net/'
 page.fill('#url','https://v.douyin.com/example/');page.get_by_role('button',name='复制本轮素材链接',exact=True).click();assert page.evaluate('copied')=='https://v.douyin.com/example/'
 assert page.evaluate('BrowserEnvironment.mobile && BrowserEnvironment.wechat');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.screenshot(path=str(Path(tempfile.gettempdir())/'wechat-workspace-guide.png'),full_page=False)
 print('PASS WeChat guide, no unsupported album transfer, canonical copy and source-link carryover');c.close()
 c=b.new_context(viewport={'width':1440,'height':900});page=c.new_page();page.goto(BASE);page.wait_for_function('typeof BrowserEnvironment!=="undefined"');assert page.evaluate('!BrowserEnvironment.mobile&&!BrowserEnvironment.wechat');assert page.locator('#embeddedBrowserHelp').count()==0
 print('PASS same URL auto detects desktop, iPhone Safari and WeChat');b.close()
