"""User-operated login window; stores a profile only after explicit local click."""
import os,re,subprocess,sys,tempfile,tkinter as tk
from pathlib import Path
from playwright.sync_api import sync_playwright
import cloud_accounts as accounts
from urllib.parse import urlsplit

provider=sys.argv[1]
if provider not in accounts.PROVIDERS: raise SystemExit(2)
urls={'baidu':'https://pan.baidu.com/disk/main','ali':'https://www.alipan.com/','quark':'https://pan.quark.cn/'}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=False)
    context=browser.new_context();page=context.new_page()
    page.goto(urls[provider],wait_until='domcontentloaded',timeout=60000)
    window=tk.Tk();window.title('添加 / 更换'+accounts.PROVIDERS[provider]);window.geometry('540x310')
    tk.Label(window,text='请在官方网页扫码。原后台账号继续保留。\n给新账号填写备注，保存后回到后台选择启用。',pady=15).pack()
    label=tk.Entry(window,width=48);label.insert(0,accounts.PROVIDERS[provider]+'新账号');label.pack()
    message=tk.StringVar(value='网页登录成功不等于整文件下载验证通过。')
    def save():
        import json
        try:
            if provider=='baidu':
                values={c['name']:c['value'] for c in context.cookies(urls[provider]) if c['name'] in {'BDUSS','STOKEN','BAIDUID','BDUSS_BFESS','BAIDUID_BFESS','PTOKEN'}}
                if not values.get('BDUSS') or not values.get('STOKEN'): raise ValueError('请先在官方网页完成登录')
                exe=next((accounts.ROOT/'pcs').rglob('BaiduPCS-Go.exe'))
                with tempfile.TemporaryDirectory(dir=accounts.ROOT/'private-baidu') as directory:
                    env=os.environ.copy();env['BAIDUPCS_GO_CONFIG_DIR']=directory
                    command='login -cookies="'+'; '.join(k+'='+v for k,v in values.items())+'"\nquit\n'
                    subprocess.run([str(exe)],input=command,encoding='utf-8',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
                    result=subprocess.run([str(exe),'who'],env=env,capture_output=True,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
                    if not re.search(r'uid:\s*[1-9][0-9]*',result.stdout.decode('utf-8',errors='replace')): raise ValueError('账号验证未通过，原账号未修改')
                    payload=(Path(directory)/'pcs_config.json').read_bytes()
                verified=True
            else:
                # Tk owns the event loop; page.url may still be the pre-login URL.
                # A protocol roundtrip refreshes navigation events, including new tabs.
                logged_in=False
                for candidate in context.pages:
                    if candidate.is_closed(): continue
                    current=urlsplit(candidate.evaluate('location.href'))
                    valid=(current.hostname in ('www.alipan.com','www.aliyundrive.com') and current.path.startswith('/drive/')) if provider=='ali' else (current.hostname=='pan.quark.cn' and current.path.startswith('/list'))
                    if valid: logged_in=True;break
                if not logged_in: raise ValueError('未检测到文件页，请在此授权窗口打开的浏览器中登录后再保存')
                payload=json.dumps(context.storage_state()).encode('utf-8');verified=False
            accounts.save(provider,label.get().strip() or accounts.PROVIDERS[provider],payload,verified)
            message.set('已加密保存。请回后台刷新并选择启用。');button.config(state='disabled')
        except Exception as error: message.set(str(error) if isinstance(error,ValueError) else '连接失败，原账号未修改：'+type(error).__name__)
    button=tk.Button(window,text='已登录，保存这个账号',command=save,pady=8);button.pack(pady=16)
    tk.Label(window,textvariable=message,wraplength=490).pack()
    if provider!='baidu':tk.Label(window,text='此平台下载驱动尚未接通：目前仅保存和选择授权，\n不会显示为下载可用。',fg='#a85b00').pack(pady=12)
    window.mainloop();browser.close()
