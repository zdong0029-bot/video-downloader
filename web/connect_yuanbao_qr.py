"""Official QR login in a private browser, with explicit user consent in this chat."""
import json,time
from pathlib import Path
from playwright.sync_api import sync_playwright
import video_accounts as account
root=account.ROOT/'data'/'video-accounts';root.mkdir(parents=True,exist_ok=True)
import msvcrt
login_lock=(root/'qr.lock').open('a+b')
if login_lock.seek(0,2)==0:login_lock.write(b'0');login_lock.flush()
login_lock.seek(0)
try:msvcrt.locking(login_lock.fileno(),msvcrt.LK_NBLCK,1)
except OSError:raise SystemExit(0)
progress=root/'qr-status.json'
def status(message):progress.write_text(json.dumps({'message':message,'time':time.time()},ensure_ascii=False),encoding='utf-8')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True);context=browser.new_context();page=context.new_page();captured={}
    def remember(request):
        if request.url.startswith('https://yuanbao.tencent.com/api/'):
            captured.update({k:v for k,v in request.all_headers().items() if k.startswith('x-') or k=='t-userid'})
    context.on('request',remember)
    try:
        page.goto('https://yuanbao.tencent.com/',wait_until='domcontentloaded',timeout=60000);page.wait_for_timeout(4000)
        if not page.get_by_text('请使用微信扫描二维码登录',exact=True).is_visible():
            page.get_by_role('button',name='登录',exact=True).click(timeout=15000);page.wait_for_timeout(2000)
        captured_qr=False
        for element in page.locator('canvas, img').all():
            box=element.bounding_box()
            if box and 130<=box['width']<=300 and 130<=box['height']<=300 and .9<box['width']/box['height']<1.1:
                element.screenshot(path=str(root/'qr-login.png'));captured_qr=True;break
        if not captured_qr:page.screenshot(path=str(root/'qr-login.png'))
        status('二维码已生成，请在聊天中的图片扫码并在微信确认登录')
        deadline=time.monotonic()+720;last_error=''
        while time.monotonic()<deadline:
            page.wait_for_timeout(2500)
            if not page.get_by_text('请使用微信扫描二维码登录',exact=True).is_visible():
                state=context.storage_state();state['ua']=page.evaluate('navigator.userAgent');state['headers']=dict(captured)
                try:
                    account.parse(account.PROBE,state);account.save(state);status('已授权并加密保存，正在进行完整视频下载测试');break
                except Exception as error:
                    last_error=str(error) if isinstance(error,ValueError) else type(error).__name__;status('网页登录已完成，播放授权检测：'+last_error);page.wait_for_timeout(10000)
        else:status('二维码流程已结束，尚未保存授权；可重新打开扫码')
    except Exception as error:status('二维码加载失败（'+type(error).__name__+'）')
    finally:browser.close()
