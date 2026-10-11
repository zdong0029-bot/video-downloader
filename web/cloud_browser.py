"""Ali fast-transfer links use the authorized official page, not preview video URLs."""
import asyncio, hashlib, json, shutil, time
from pathlib import Path
from urllib.parse import urlsplit
from common import Cancelled
import file_download


def download_transport(url, client, out, progress, cancelled):
    return asyncio.run(_download(url, client, out, progress, cancelled))


async def _download(url, client, out, progress, cancelled):
    from playwright.async_api import async_playwright
    from cloud_backend import LoginRequired
    work = out / '.browser-transfer'
    work.mkdir(exist_ok=False)
    errors = []; downloads = []; metadata = {'size':0, 'name':'阿里快传原文件'}
    state = {'cookies':client.state.get('cookies',[]), 'origins':client.state.get('origins',[])}
    token = client.ali_auth()
    for origin in state['origins']:
        for entry in origin.get('localStorage',[]):
            try: value=json.loads(entry['value'])
            except (ValueError,TypeError):continue
            if isinstance(value,dict) and value.get('refresh_token'):
                value.update(token);entry['value']=json.dumps(value)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, downloads_path=str(work))
        try:
            context = await browser.new_context(storage_state=state, accept_downloads=True)
            page = await context.new_page()
            async def response(r):
                host=urlsplit(r.url).hostname
                if host not in ('api.alipan.com','api.aliyundrive.com'):return
                try:d=await r.json()
                except Exception:return
                if isinstance(d,dict) and d.get('code') in ('ShareExpired','ShareLinkNotFound','ShareLinkForbidden'):
                    errors.append('阿里快传链接已经失效，请使用新的有效分享链接')
                if isinstance(d,dict) and d.get('code') in ('AccessTokenInvalid','AccessTokenExpired'):
                    errors.append('阿里登录授权已失效，请在首页重新扫码')
            page.on('response',response)
            page.on('download',lambda d:downloads.append(d))
            await page.goto(url,wait_until='domcontentloaded',timeout=45000)
            deadline=time.monotonic()+35
            button=None
            while time.monotonic()<deadline:
                if cancelled():raise Cancelled()
                if errors:raise ValueError(errors[0])
                text=await page.locator('body').inner_text()
                if '快传链接已失效' in text or '分享已失效' in text:raise ValueError('阿里快传链接已经失效，请使用新的有效分享链接')
                choices=page.get_by_text('下载',exact=True)
                for i in range(await choices.count()):
                    candidate=choices.nth(i)
                    if await candidate.is_visible():button=candidate;break
                if button:break
                await asyncio.sleep(.5)
            if button is None:raise ValueError('阿里快传页面没有提供可用的下载按钮，请检查官方分享状态')
            await button.click()
            deadline=time.monotonic()+30
            while not downloads and time.monotonic()<deadline:
                if cancelled():raise Cancelled()
                if errors:raise ValueError(errors[0])
                await asyncio.sleep(.5)
            if not downloads:raise ValueError('阿里快传要求客户端或额外授权，网页尚未发出原文件；可改用普通 /s/ 分享链接')
            d=downloads[0];name=file_download.safe_name(d.suggested_filename)
            metadata['name']=name
            target=out/(hashlib.sha256(url.encode()).hexdigest()[:10]+'_'+name)
            task=asyncio.create_task(d.save_as(str(target)))
            deadline=time.monotonic()+43200
            while not task.done():
                if cancelled():task.cancel();raise Cancelled()
                if time.monotonic()>deadline:task.cancel();raise ValueError('阿里快传下载超时')
                parts=[x for x in work.iterdir() if x.is_file()]
                size=sum(x.stat().st_size for x in parts)
                if size>file_download.LIMIT or shutil.disk_usage(out).free<512*1024**2:
                    task.cancel();raise ValueError('阿里原文件超过10GB或主机可用空间不足')
                progress([metadata],parts);await asyncio.sleep(.5)
            await task
            if await d.failure() or not target.is_file() or not target.stat().st_size:raise ValueError('阿里快传原文件未完整接收')
            if target.stat().st_size>file_download.LIMIT:target.unlink();raise ValueError('文件超过10GB')
            with target.open('rb') as stream:
                prefix=stream.read(256).lstrip().lower()
                if prefix.startswith((b'<!doctype html',b'<html')):target.unlink();raise ValueError('平台返回了登录页面，没有取得原文件')
            metadata['size']=target.stat().st_size;progress([metadata],[target])
            return [target]
        finally:
            await browser.close()
            # Directory is generated inside this task; only this private browser staging is removed.
            shutil.rmtree(work,ignore_errors=True)
