"""Official interactive login; saves only after the user's local confirmation."""
import sys,json,time,importlib
from pathlib import Path
import tkinter as tk
from playwright.sync_api import sync_playwright
import video_platform_accounts as accounts

# Keep the official browser alive when the status detector is updated.
accounts_mtime = Path(accounts.__file__).stat().st_mtime_ns
def reload_detector():
    global accounts_mtime
    current = Path(accounts.__file__).stat().st_mtime_ns
    if current != accounts_mtime:
        importlib.invalidate_caches()
        importlib.reload(accounts)
        accounts_mtime = current

providers = sys.argv[1:] or ['kuaishou', 'xiaohongshu']
if any(p not in accounts.PROVIDERS for p in providers):
    raise SystemExit(2)

with sync_playwright() as p:
    launch={'headless':False}
    if 'tiktok' in providers:
        import socket
        try:
            with socket.create_connection(('127.0.0.1',10808),timeout=.3):pass
            launch['proxy']={'server':'socks5://127.0.0.1:10808'}
        except OSError:pass
    browser = p.chromium.launch(**launch)
    window = tk.Tk(); window.title('连接视频平台官方账号 · ' + ' / '.join(accounts.PROVIDERS[item][0] for item in providers)); window.geometry('600x360')
    tk.Label(window, text='请在官方网页登录，并亲自完成页面要求的验证。\n登录后点击下面的保存按钮；检测通过时也会自动加密保存。', pady=14).pack()
    contexts = []
    for provider in providers:
        name, url, _ = accounts.PROVIDERS[provider]
        previous=accounts.load(provider)
        opts={'storage_state':{'cookies':accounts.live_cookies(previous),'origins':previous['origins']}} if previous else {}
        if previous and previous.get('ua'):opts['user_agent']=previous['ua']
        context = browser.new_context(**opts); page = context.new_page(); contexts.append(context)
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
        except Exception:
            pass  # Keep the official tab available for the human to reload.
        frame = tk.Frame(window); frame.pack(fill='x', padx=20, pady=12)
        message = tk.StringVar(value='请在官方网页完成登录和验证')
        saved = {'done':False, 'revision':None}
        def save(provider=provider, context=context, message=message, saved=saved, quiet=False):
            try:
                reload_detector()
                for tab in context.pages:
                    if not tab.is_closed():
                        tab.evaluate('location.href')  # Flush navigation events while Tk owns the loop.
                # Manual Douyin confirmation saves issued session credentials.
                # save() requires a live sessionid; saved never means verified.
                require_page_check = quiet or provider != 'douyin'
                if require_page_check and provider in ('xiaohongshu','tiktok','douyin') and not any(
                    accounts.page_authorized(provider, tab)
                    for tab in context.pages if not tab.is_closed()
                ):
                    raise ValueError('官网尚未确认登录成功；请在本窗口完成官方登录及验证后再保存。')
                state = context.storage_state()
                state['ua'] = next(tab.evaluate('navigator.userAgent') for tab in context.pages if not tab.is_closed())
                if saved['done'] and quiet:
                    revision = accounts.refresh(provider, state, saved['revision'])
                    if not revision: return
                else:
                    revision = accounts.save(provider, state)
                saved.update(done=True, revision=revision)
                message.set('授权已加密保存；真实下载验证待完成。现在可回网站复测。')
            except ValueError as error:
                if not quiet:message.set(str(error))
            except Exception:
                if not quiet:message.set('暂未保存，请在本窗口打开的官方网页完成登录后重试')
        def schedule(provider=provider, context=context, saved=saved, save=save):
            def poll():
                try:
                    for tab in context.pages:
                        if not tab.is_closed(): tab.evaluate('location.href')
                    save(quiet=True)
                except Exception: pass
                window.after(30000 if saved['done'] else 2500, poll)
            window.after(2500, poll)
        schedule()
        tk.Button(frame, text=name + '：已登录，保存授权', command=save, pady=7).pack()
        tk.Label(frame, textvariable=message, wraplength=550).pack(pady=5)
    tk.Label(window, text='授权仅保存在这台电脑；访客不会看到登录信息。\n保存表示连接了账号，下载可用性仍以实际文件验证为准。', fg='#47636b').pack(pady=12)
    window.mainloop(); browser.close()
