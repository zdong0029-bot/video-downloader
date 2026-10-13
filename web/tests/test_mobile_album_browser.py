import base64,hashlib,json,os,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
out=Path(tempfile.gettempdir())/'video-downloader-album-tests';out.mkdir(exist_ok=True);BASE=os.environ.get('MOBILE_ALBUM_BASE_URL','http://127.0.0.1:8000')
png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lL8AAAAASUVORK5CYII=')
video=b'original-video-transport-fixture'*31
names=['photo.png','clip.mp4','broken.png','large.mp4']
bodies={'photo.png':png,'clip.mp4':video,'broken.png':png}
files=[{'name':n,'size':len(bodies[n]) if n in bodies else 4*1024**3,'done':True} for n in names]
job={'id':'albumfixture','url':'https://files.example.test/photos','title':'手机原文件','kind':'cloud','status':'done','progress':1,'message':'已准备','files':files,'owner':'fixture-device','created':1}
init='''
window.albumCalls=[];window.albumShareMode='ok';window.albumSupport=true;
Object.defineProperty(navigator,'canShare',{configurable:true,value:d=>window.albumSupport&&d.files&&d.files.length>0});
Object.defineProperty(navigator,'share',{configurable:true,value:async d=>{
 const active=navigator.userActivation.isActive;
 if(window.albumShareMode==='cancel')throw new DOMException('Cancelled','AbortError');
 if(window.albumShareMode==='denied')throw new DOMException('Denied','NotAllowedError');
 const files=[];for(const f of d.files){const bytes=await f.arrayBuffer();const digest=await crypto.subtle.digest('SHA-256',bytes);files.push({name:f.name,type:f.type,size:f.size,sha256:Array.from(new Uint8Array(digest)).map(n=>n.toString(16).padStart(2,'0')).join('')});}
 window.albumCalls.push({active,files});
}});
'''
def test_mobile_album_browser():
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True)
  c=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1')
  c.add_init_script(init);page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));requests=[]
  def route(r):
   path=r.request.url.removeprefix(BASE).split('?')[0]
   if path=='/api/jobs':r.fulfill(json=[job])
   elif path.startswith('/api/files/albumfixture/'):
    name=path.rsplit('/',1)[1];requests.append(name)
    if name=='large.mp4':r.fulfill(status=500,body='should not request large file')
    else:r.fulfill(body=bodies[name],headers={'Content-Type':'application/octet-stream','Content-Length':str(len(bodies[name]))})
   elif path.startswith('/api/thumb/'):r.fulfill(body=png,content_type='image/png')
   else:r.continue_()
  page.route('**/api/**',route);page.goto(BASE);expect(page.locator('#main')).to_be_visible();page.wait_for_function("typeof saveSelectedAlbum==='function'")
  page.click('#historyTab');expect(page.locator('#jobs .job')).to_have_count(1);page.evaluate("expandedCards.add('albumfixture');repaint()")
  row=page.locator('#jobs .file').filter(has=page.locator('.fname',has_text='photo.png'))
  expect(row.get_by_role('button',name='保存到相册',exact=True)).to_be_visible();row.get_by_role('button',name='保存到相册',exact=True).click()
  expect(page.locator('#previewImage')).to_be_visible();expect(page.locator('#previewVideo')).to_be_hidden();expect(page.locator('#shareVideo')).to_be_enabled()
  page.click('#shareVideo');page.wait_for_function('albumCalls.length===1');call=page.evaluate('albumCalls[0]');assert call['active'];assert call['files']==[{'name':'photo.png','type':'image/png','size':len(png),'sha256':hashlib.sha256(png).hexdigest()}]
  expect(page.locator('#saveHelp')).to_contain_text('网页无法确认');page.click('#previewDialog button:has-text("关闭")')
  page.evaluate("previewFile('albumfixture','clip.mp4')");expect(page.locator('#shareVideo')).to_be_enabled();expect(page.locator('#previewVideo')).to_be_visible()
  page.click('#shareVideo');page.wait_for_function('albumCalls.length===2');assert page.evaluate('albumCalls[1].files[0].sha256')==hashlib.sha256(video).hexdigest();assert page.evaluate('albumCalls[1].files[0].type')=='video/mp4'
  page.evaluate("window.albumShareMode='cancel'");page.click('#shareVideo');expect(page.locator('#saveHelp')).to_contain_text('未确认保存');expect(page.locator('#shareVideo')).to_be_enabled();assert page.evaluate('albumCalls.length')==2
  page.evaluate("window.albumShareMode='denied'");page.click('#shareVideo');expect(page.locator('#saveHelp')).to_contain_text('系统未允许');page.evaluate("window.albumShareMode='ok';closePreview();selected.clear();selected.add(key('albumfixture','photo.png'));selected.add(key('albumfixture','clip.mp4'));repaint()")
  page.get_by_role('button',name='选中存相册',exact=True).click();expect(page.locator('#shareVideo')).to_have_text('保存这 2 个到相册');page.click('#shareVideo');page.wait_for_function('albumCalls.length===3');assert len(page.evaluate('albumCalls[2].files'))==2
  page.screenshot(path=str(out/'mobile-album.png'),full_page=False)
  # Batch splitting and explicit next gesture; no automatic Photos write.
  page.evaluate("closePreview();selected.clear();selected.add(key('albumfixture','photo.png'));cache[0].files=Array.from({length:17},(_,i)=>({name:'photo.png',size:68,entry_id:String(i)}));saveSelectedAlbum('albumfixture')")
  expect(page.locator('#shareVideo')).to_have_text('保存这 16 个到相册');page.click('#shareVideo');expect(page.locator('#nextAlbumBatch')).to_be_visible();assert page.evaluate('albumCalls.length')==4
  page.click('#nextAlbumBatch');expect(page.locator('#shareVideo')).to_have_text('保存到相册');page.click('#shareVideo');page.wait_for_function('albumCalls.length===5');assert len(page.evaluate('albumCalls[4].files'))==1
  # Large file guard never allocates its original into JS memory.
  before=len(requests);page.evaluate("closePreview();cache[0].files=[{name:'large.mp4',size:4294967296}];previewFile('albumfixture','large.mp4')")
  expect(page.locator('#saveHelp')).to_contain_text('128 MB');expect(page.locator('#shareVideo')).to_be_disabled();assert 'large.mp4' not in requests[before:] or requests[before:].count('large.mp4')<=1 # metadata preview is permitted
  page.evaluate("closePreview();window.albumSupport=false;previewFile('albumfixture','photo.png')");expect(page.locator('#saveHelp')).to_contain_text('不支持');expect(page.locator('#saveOriginal')).to_have_attribute('href','/api/files/albumfixture/photo.png')
  page.evaluate("closePreview();window.albumSupport=true;cache[0].files=[{name:'broken.png',size:12345}];previewFile('albumfixture','broken.png')")
  # Explicit expected transfer mismatch: header omission means metadata length is enforced.
  page.route('**/api/files/albumfixture/broken.png*',lambda r:r.fulfill(body=png,headers={'Content-Type':'image/png','Content-Length':str(len(png))}))
  page.evaluate("closePreview();previewFile('albumfixture','broken.png')");expect(page.locator('#shareVideo')).to_be_disabled();expect(page.locator('#saveHelp')).to_contain_text('不完整')
  # Catalog result buttons point to the same original-file album flow.
  page.evaluate("closePreview();cache=[{id:'albumfixture',catalog_id:'albumcatalog',files:[{name:'photo.png',size:68,entry_id:'one'}],status:'done'}];catalogCache=[{id:'albumcatalog',url:'https://www.youtube.com/@fixture',title:'主页',entries:[{id:'one',title:'原图',url:'https://example.test'}],status:'done',message:'完成',complete:true}];catalogSet('albumcatalog').add('one');expandedCards.add('catalog-albumcatalog');renderCatalogs()")
  expect(page.locator('#catalogs').get_by_role('button',name='存相册',exact=True)).to_be_visible();page.locator('#catalogs').get_by_role('button',name='选中存相册',exact=True).click();expect(page.locator('#shareVideo')).to_be_enabled()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert not errors,errors
  print('PASS image/video original bytes and MIME, fresh click activation, batch saving/splitting, cancel/denied, large-file guard, unsupported fallback, incomplete file rejection, catalog and cloud entries, mobile width')
  browser.close()

if __name__=='__main__':
 test_mobile_album_browser()
