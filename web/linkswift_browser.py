"""Refresh normal Xunlei web sessions through the official client in the background.

The official client obtains its own session headers. No verification response is
fabricated, and no third-party parsing service receives the administrator login.
"""
import json,time,re
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
import cloud_providers as providers
import cloud_accounts as accounts
from common import Cancelled

def xunlei_share_context(client,url,share_id):
    from linkswift_cloud import fail
    if not providers.official('xunlei',url):raise ValueError('不是迅雷官方分享地址')
    captured=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        # Ignore private adapter fields that are not part of Playwright state.
        state={k:client.state.get(k,[]) for k in ('cookies','origins')}
        context=browser.new_context(storage_state=state,accept_downloads=False)
        page=context.new_page()
        def capture(response):
            u=urlsplit(response.url)
            if (u.hostname!='api-pan.xunlei.com' or u.path!='/drive/v1/share'
                or parse_qs(u.query).get('share_id',[''])[0]!=share_id):return
            try:
                if response.status!=200 or int(response.headers.get('content-length','0'))>5*1024**2:return
                data=response.json()
                if not isinstance(data,dict) or not isinstance(data.get('files'),list):return
                raw=response.request.headers
                headers={k:raw[k] for k in ('authorization','x-captcha-token','x-device-id','x-client-id','user-agent','referer') if k in raw}
                captured.append((data,headers))
            except (ValueError,TypeError):return
        page.on('response',capture)
        try:
            page.goto(url,wait_until='domcontentloaded',timeout=45000)
            until=time.monotonic()+25
            while not captured and time.monotonic()<until:
                if client.cancelled():raise Cancelled()
                page.wait_for_timeout(250)
            if not captured:fail('xunlei','官网需要登录或人工验证',True)
            data,headers=captured[-1]
            refreshed=context.storage_state()
            client.update_browser_state(refreshed)
            if client.identity:accounts.update_payload(client.identity,client.state)
            client._share_headers=headers
            return data
        finally:context.close();browser.close()

def xunlei_delete_task(client,remote,job):
    """Use the official SDK's per-action session for one validated task folder."""
    if client.key!='xunlei' or not re.fullmatch(r'[0-9a-f]{12}',job):
        raise ValueError('无效的迅雷任务清理请求')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',remote):
        raise ValueError('无效的迅雷临时目录标识')
    from cloud_cleanup import read,ensure_files
    receipt=read(job)
    if (receipt['provider'],receipt['account'],receipt['remote'])!=('xunlei',client.identity,remote):
        raise ValueError('清理账号或目录与任务记录不匹配')
    ensure_files(receipt)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(storage_state={k:client.state.get(k,[]) for k in ('cookies','origins')},accept_downloads=False)
        page=context.new_page()
        try:
            page.goto('https://pan.xunlei.com/',wait_until='domcontentloaded',timeout=45000)
            page.wait_for_function('window.$nuxt && window.$nuxt.$store && window.$nuxt.$store._actions["drive/batchDeleteFile"]',timeout=30000)
            if not providers.official('xunlei',page.url):raise ValueError('迅雷官网跳转未通过')
            result=page.evaluate('''async ({id,name}) => {
                const store=window.$nuxt.$store;
                const timeout=new Promise(resolve=>setTimeout(()=>resolve({ok:false,reason:"timeout"}),35000));
                const run=async()=>{
                    store.commit("drive/update",{curFilter:"all"});
                    let cursor="";
                    for(let page=0;page<23;page++){
                        await store.dispatch("drive/getFiles",{parent_id:"",limit:100,page_token:cursor});
                        if(store.state.drive.all[id]) break;
                        cursor=store.state.drive.pageToken||"";
                        if(!cursor) break;
                    }
                    let item=store.state.drive.all[id];
                    if(!item) return {ok:false,reason:"not-listed"};
                    await store.dispatch("drive/getFileInfo",{id});
                    item=store.state.drive.all[id];
                    if(item?.id!==id || item.name!==name || item.kind!=="drive#folder" || item.parent_id)
                        return {ok:false,reason:"identity"};
                    // SDK obtains its normal authorization for this POST action.
                    await store.dispatch("drive/batchDeleteFile",[id]);
                    return {ok:true};
                };
                try { return await Promise.race([run(),timeout]); }
                catch(e) { return {ok:false,reason:"official-action",code:e?.error_code||e?.code||e?.error||"unknown"}; }
            }''',{'id':remote,'name':'网站下载任务-'+job})
            refreshed=context.storage_state();client.update_browser_state(refreshed)
            if client.identity:accounts.update_payload(client.identity,client.state)
            if not result.get('ok'):
                raise ValueError('迅雷官方任务清理未通过（'+str(result.get('reason','unknown'))+'）')
        finally:context.close();browser.close()
