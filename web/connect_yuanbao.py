"""Interactive official login; the local save button grants backend use."""
import json,tkinter as tk
from playwright.sync_api import sync_playwright
import video_accounts as account
with sync_playwright() as p:
    browser=p.chromium.launch(headless=False)
    context=browser.new_context();page=context.new_page();captured={}
    def remember(request):
        if request.url.startswith('https://yuanbao.tencent.com/api/'):
            captured.update({k:v for k,v in request.all_headers().items() if k.startswith('x-') or k=='t-userid'})
    context.on('request',remember)
    page.goto('https://yuanbao.tencent.com/',wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(4500)
    if not page.get_by_text('请使用微信扫描二维码登录',exact=True).is_visible():
        page.get_by_role('button',name='登录',exact=True).click(timeout=15000)
        page.wait_for_timeout(2000)
    page.bring_to_front()
    window=tk.Tk();window.title('连接视频号 · 腾讯元宝官方授权');window.geometry('560x300')
    tk.Label(window,text='请在打开的元宝官方网页点击登录，用微信扫码。\n完成后点击下面的连接按钮，授权仅加密保存在本机。',pady=18).pack()
    message=tk.StringVar(value='朋友下载时无需登录；后台账号失效后可在原首页重新连接。')
    def connect():
        try:
            # Flush browser navigation and obtain only the session granted here.
            ua=page.evaluate('navigator.userAgent');state=context.storage_state();state['ua']=ua;state['headers']=dict(captured)
            account.parse(account.PROBE,state)
            account.save(state);message.set('授权验证通过，已加密保存。正在继续做真实视频下载测试。');button.config(state='disabled');window.after(2500,window.destroy)
        except Exception as error:message.set(str(error) if isinstance(error,ValueError) else '连接未完成（'+type(error).__name__+'），请保持官方网页登录后重试')
    button=tk.Button(window,text='已登录，连接视频号',command=connect,pady=10);button.pack(pady=12)
    tk.Label(window,textvariable=message,wraplength=500).pack(pady=10)
    window.mainloop();browser.close()
