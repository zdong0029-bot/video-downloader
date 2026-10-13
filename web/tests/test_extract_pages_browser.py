import json,os,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
BASE=os.environ.get('EXTRACT_PAGE_BASE_URL','http://127.0.0.1:8000')
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'
with sync_playwright() as p:
 b=p.chromium.launch(headless=True)
 for mobile in [False,True]:
  c=b.new_context(viewport={'width':390 if mobile else 1440,'height':844 if mobile else 980},is_mobile=mobile,has_touch=mobile,user_agent=UA if mobile else None)
  c.add_init_script("localStorage.setItem('video-downloader-input-links-v1',JSON.stringify({video:'OLD LINK',cloud:'OLD CLOUD LINK'}))")
  page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));jobs=[];catalogs=[];posts=[]
  def route(r):
   path=r.request.url.removeprefix(BASE).split('?')[0];method=r.request.method
   if path=='/api/jobs' and method=='GET':r.fulfill(json=jobs)
   elif path=='/api/jobs' and method=='POST':
    d=r.request.post_data_json;posts.append(d);kind='file' if d['mode']=='file' else 'web';j={'id':'job'+str(len(jobs)),'url':d['url'],'title':'fixture extracted original','kind':kind,'status':'done','total':1,'finished':1,'progress':1,'files':[],'created':len(jobs)+1};jobs.append(j);r.fulfill(json={'created':[j],'catalogs':[],'rejected':[]})
   elif path=='/api/catalogs' and method=='GET':r.fulfill(json=catalogs)
   elif path=='/api/catalogs' and method=='POST':
    d=r.request.post_data_json;posts.append(d)
    if '/discovery/item/' in d['url']:r.fulfill(json={'catalogs':[],'rejected':[{'reason':'这不是主页或合集链接'}]});return
    cat={'id':'catalog1','url':d['url'],'title':'fixture profile','status':'done','message':'complete','complete':True,'entries':[],'created':1};catalogs.append(cat);r.fulfill(json={'catalogs':[cat],'rejected':[]})
   else:r.continue_()
  page.route('**/api/**',route);page.goto(BASE);expect(page.locator('#main')).to_be_visible();page.wait_for_function("typeof openDownload==='function'")
  assert page.locator('.app-content > .video-subnav').count()==1;assert page.locator('.app-sidebar .video-subnav').count()==0
  expect(page.locator('.app-title')).to_have_text('视频 / 图片')
  expect(page.locator('#url')).to_have_value('');expect(page.locator('#scanProfile')).to_be_hidden();expect(page.locator('#go')).to_have_text('提取视频 / 图片');expect(page.locator('#mediaExtractTab')).to_have_attribute('aria-current','page')
  note='https://www.xiaohongshu.com/discovery/item/6ac119c000000000014033c23?xsec_token=fixture';page.fill('#url',note);page.click('#go');expect(page.locator('#jobs .job')).to_have_count(1);assert posts[-1]['mode']=='media';assert not catalogs
  page.click('#profileExtractTab');expect(page.locator('#url')).to_have_value('');expect(page.locator('#go')).to_be_hidden();expect(page.locator('#scanProfile')).to_be_visible();expect(page.locator('#jobs .job')).to_have_count(0);assert page.url.endswith('#profiles')
  page.fill('#url',note);page.click('#scanProfile');expect(page.locator('#hint')).to_contain_text('识别失败');assert not catalogs
  profile='https://www.youtube.com/@fixture';page.fill('#url',profile);page.click('#scanProfile');expect(page.locator('#catalogs .catalog')).to_have_count(1)
  page.click('#mediaExtractTab');expect(page.locator('#url')).to_have_value(note);expect(page.locator('#jobs .job')).to_have_count(1);expect(page.locator('#catalogs')).to_be_hidden()
  page.click('#cloudTab');expect(page.locator('#cloudTab')).to_have_text('网盘下载');expect(page.locator('#url')).to_have_value('');page.fill('#url','https://files.example.test/original.mp4');page.click('#go');expect(page.locator('#jobs .job')).to_have_count(1);assert posts[-1]['mode']=='file'
  expect(page.locator('.video-subnav')).to_be_hidden();page.click('#videoTab');expect(page.locator('#jobs .job')).to_have_count(1);page.reload();expect(page.locator('#url')).to_have_value('');expect(page.locator('#jobs .job')).to_have_count(0);expect(page.locator('#catalogs .catalog')).to_have_count(0)
  page.click('#profileExtractTab');expect(page.locator('#url')).to_have_value('');expect(page.locator('#catalogs .catalog')).to_have_count(0);page.click('#cloudTab');expect(page.locator('#url')).to_have_value('');expect(page.locator('#jobs .job')).to_have_count(0)
  page.click('#historyTab');expect(page.locator('.video-subnav')).to_be_hidden();expect(page.locator('#jobs .job')).to_have_count(2);expect(page.locator('#catalogs .catalog')).to_have_count(1)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');assert not errors,errors
  page.click('#videoTab');page.screenshot(path=str(Path(tempfile.gettempdir())/('extract-pages-mobile.png' if mobile else 'extract-pages-desktop.png')),full_page=False)
  print('PASS', 'mobile' if mobile else 'desktop','independent pages, single work routing, mismatch failure, refresh empty input/results and retained history')
  c.close()
 b.close()
