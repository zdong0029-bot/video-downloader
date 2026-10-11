import os
from pathlib import Path
if not os.environ.get('ISOLATED_BATCH_TEST') or Path(os.environ['ISOLATED_BATCH_TEST']).resolve() != Path(__file__).resolve().parent:
    raise SystemExit('请运行 verify_batch_catalog.py，测试必须使用临时副本')
import hashlib, json, shutil, threading, time, urllib.request
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import uvicorn
from playwright.sync_api import sync_playwright, expect
import app as a
BASE='http://127.0.0.1:18004'
ROOT=Path(__file__).parent
FIXTURE=ROOT/'fixture.mp4'
results={}
class FakeYDL:
    def __init__(self,opts):self.opts=opts
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def extract_info(self,url,download=True):
        assert self.opts['format']=='bv*+ba/b'
        assert self.opts['format_sort']==['res','fps','tbr']
        assert not self.opts.get('postprocessors')
        vid=parse_qs(urlsplit(url).query).get('v',['fixture'])[0]
        name=vid+'.mp4' if vid.startswith('catalog-') else 'same.mp4'
        dest=Path(self.opts['outtmpl']).parent/name
        shutil.copyfile(FIXTURE,dest)
        for hook in self.opts['progress_hooks']:
            hook({'status':'downloading','total_bytes':FIXTURE.stat().st_size,'downloaded_bytes':FIXTURE.stat().st_size,'info_dict':{'title':vid}})
        time.sleep(.02)
        return {'title':vid}
a.yt_dlp.YoutubeDL=FakeYDL
a.symphony.list_assets=lambda u:[a.symphony.Asset('multi '+str(i),'video-'+str(i),'https://example.com/'+str(i),filename='multi-'+str(i)+'.mp4') for i in range(3)]
def asset_download(asset,out,**kw):
    dest=out/asset.filename;shutil.copyfile(FIXTURE,dest);return dest
a.symphony.download_asset=asset_download
def catalog_resolve(url,limit=0,cookie_options=None,on_update=None):
    rows=[{'id':str(i),'url':'https://www.youtube.com/watch?v=catalog-'+str(i),'title':'主页视频 '+str(i),'thumbnail':'','duration':1} for i in range(245)]
    if limit:rows=rows[:limit]
    if on_update:on_update(rows[:20],'测试主页')
    time.sleep(.08)
    return {'title':'测试主页','entries':rows,'limited':bool(limit)}
a.video_catalog.resolve=catalog_resolve
server=uvicorn.Server(uvicorn.Config(a.app,host='127.0.0.1',port=18004,log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
while not server.started:time.sleep(.05)
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        ctx=browser.new_context(accept_downloads=True,viewport={'width':1280,'height':980})
        page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(BASE);expect(page.locator('#main')).to_be_visible()
        links=['https://www.youtube.com/watch?v=batch-'+str(i) for i in range(10)]
        links.append('https://ads.tiktok.com/creative/creativestudio/shared-link/11111111-1111-4111-8111-111111111111')
        page.locator('#url').fill('\n'.join(links));page.locator('#go').click()
        page.wait_for_function("cache.length===11 && cache.every(j=>j.status==='done')")
        page.evaluate('repaint()')
        expect(page.locator('#jobs>.job')).to_have_count(2)
        group=page.locator('#jobs>.job').filter(has_text='单视频批量')
        multi=page.locator('#jobs>.job').filter(has_text='Symphony')
        expect(group.locator('.file')).to_have_count(0)
        group.locator('.toolbar input[type=checkbox]').check()
        expect(group.locator('.file')).to_have_count(10)
        assert '已选 10 / 10' in group.inner_text()
        group.get_by_role('button',name='收起',exact=True).click()
        expect(group.locator('.file')).to_have_count(0)
        assert '已选 10 / 10' in group.inner_text()
        page.evaluate('refresh(true)');page.wait_for_timeout(250)
        expect(group.locator('.file')).to_have_count(0)
        group.get_by_role('button',name='展开视频',exact=True).click()
        expect(group.locator('button').filter(has_text='定位')).to_have_count(10)
        multi.locator('.toolbar input[type=checkbox]').check()
        expect(multi.locator('.file')).to_have_count(3)
        downloads=[]
        def saved(d):
            target=ROOT/'saved'/str(len(downloads)+1)/d.suggested_filename
            target.parent.mkdir(parents=True,exist_ok=True);d.save_as(target);downloads.append(target)
        page.on('download',saved)
        group.get_by_role('button',name='下载选中',exact=True).click()
        multi.get_by_role('button',name='下载选中',exact=True).click()
        page.wait_for_function("downloadTransfers.size===2 && [...downloadTransfers.values()].every(t=>!t.active)",timeout=25000)
        assert len(downloads)==13,len(downloads)
        want=hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==want for p in downloads)
        assert page.evaluate('[...downloadTransfers.values()].reduce((n,t)=>n+t.completed,0)')==13
        results['mixed_10_single_1_multi']=True
        results['fold_selection_refresh_locate']=True
        results['concurrent_groups_13_downloads_identical_hash']=True
        # Full profile listing exceeds the previous 200-item ceiling.
        page.locator('#url').fill('https://www.youtube.com/@batch-demo/videos')
        page.locator('#scanProfile').click()
        page.wait_for_function("catalogCache.length===1 && catalogCache[0].status==='done'")
        cid=page.evaluate('catalogCache[0].id')
        assert page.evaluate('catalogCache[0].entries.length')==245
        assert page.evaluate('catalogCache[0].limit')==0
        cat=page.locator('#catalogs>.job')
        cat.locator('.toolbar input[type=checkbox]').check()
        assert '已选 245 / 245' in cat.inner_text()
        expect(cat.locator('.file')).to_have_count(60)
        cat.get_by_role('button',name='收起',exact=True).click()
        expect(cat.locator('.file')).to_have_count(0)
        assert '已选 245 / 245' in cat.inner_text()
        cat.get_by_role('button',name='展开视频',exact=True).click()
        cat.get_by_role('button',name='显示更多视频',exact=False).click()
        expect(cat.locator('.file')).to_have_count(160)
        cat.locator('.toolbar input[type=checkbox]').uncheck()
        page.evaluate("catalogToggle(catalogCache[0].id,'0',true);catalogToggle(catalogCache[0].id,'244',true)")
        cat.get_by_role('button',name='下载选中',exact=True).click()
        page.wait_for_function("cache.some(j=>j.kind==='collection' && j.status==='done' && j.files.length===2)")
        collection=page.locator('#jobs>.job').filter(has_text='测试主页')
        expect(collection.locator('.file')).to_have_count(2)
        assert page.evaluate("cache.find(j=>j.kind==='collection').files.map(f=>f.name).sort().join(',')")=='catalog-0.mp4,catalog-244.mp4'
        results['full_profile_245_links_and_only_chosen_downloaded']=True
        # Another browser cannot see or download this browser's catalog or files.
        other=browser.new_context();resp=other.request.get(BASE+'/api/catalogs');assert resp.json()==[]
        assert other.request.post(BASE+'/api/catalogs/'+cid+'/download',data={'ids':['0']}).status==404
        assert other.request.post(BASE+'/api/jobs/'+page.evaluate('cache[0].id')+'/remove-files',data={'names':['catalog-0.mp4']}).status==404
        results['browser_isolation']=True
        # Remove grouped display entries, keeping every original file on disk.
        underlying=page.evaluate("viewJobs(cache).find(j=>j.is_group).members.map(j=>({id:j.id,name:j.files[0].name}))")
        group.get_by_role('button',name='移出列表',exact=True).click()
        page.locator('#dlgYes').click()
        page.wait_for_function('!viewJobs(cache).some(j=>j.is_group)')
        assert all((a.DOWNLOADS/j['id']/j['name']).is_file() for j in underlying)
        results['group_remove_keeps_files']=True
        # Reload restores batch metadata for retained files.
        page.reload();expect(page.locator('#main')).to_be_visible()
        page.wait_for_timeout(500)
        mobile=browser.new_context(viewport={'width':390,'height':844},is_mobile=True)
        mobile.add_cookies(ctx.cookies());mp=mobile.new_page();mp.goto(BASE);expect(mp.locator('#main')).to_be_visible()
        assert mp.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
        results['mobile_no_horizontal_overflow']=True
        page.screenshot(path=str(ROOT/'integration-desktop.png'),full_page=True)
        mp.screenshot(path=str(ROOT/'integration-mobile.png'),full_page=True)
        assert not errors,errors
        results['no_browser_script_errors']=True
        other.close();mobile.close();ctx.close();browser.close()
    # The restart restore function retains batch IDs and multi-video kinds.
    metas=[json.loads(p.read_text(encoding='utf-8')) for p in a.DOWNLOADS.glob('*/meta.json')]
    assert all('batch_id' in m for m in metas)
    a.JOBS.clear();a._restore_jobs()
    assert any(j.kind=='collection' and len(j.files)==2 for j in a.JOBS.values())
    assert all(not j.batch_id or isinstance(j.batch_id,str) for j in a.JOBS.values())
    results['restart_metadata_restore']=True
finally:
    server.should_exit=True;thread.join(5)
(ROOT/'integration-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False))

