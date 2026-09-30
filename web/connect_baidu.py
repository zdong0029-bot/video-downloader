"""Explicit interactive connector: user signs in on Baidu, then confirms locally."""
import os
import re
import subprocess
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'private-baidu'
EXE=next((ROOT/'pcs').rglob('BaiduPCS-Go.exe'))

def main():
    CONFIG.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=False)
        context=browser.new_context()
        page=context.new_page()
        page.goto('https://pan.baidu.com/disk/main',wait_until='domcontentloaded',timeout=60000)
        window=tk.Tk(); window.title('连接百度网盘到下载器'); window.geometry('550x260')
        tk.Label(window,text='请在新打开的百度页面扫码登录。\n登录完成后点击下方按钮，将该账号连接到下载器。\n\n连接后，朋友提交的百度分享任务可使用此账号转存及下载。\n授权仅保存在这台主机；转存文件会占用网盘空间。\n不会把账号密码发送给网站访客。',justify='left',padx=20,pady=20).pack()
        status=tk.StringVar(value='等待你在百度官方页面登录')
        def connect():
            cookies=context.cookies('https://pan.baidu.com/')
            allowed={'BDUSS','BDUSS_BFESS','STOKEN','BAIDUID','BAIDUID_BFESS','PTOKEN'}
            values={c['name']:c['value'] for c in cookies if c['name'] in allowed}
            if not values.get('BDUSS') or not values.get('STOKEN'):
                status.set('尚未检测到完整网盘登录，请确认已进入“我的文件”页面。'); return
            env=os.environ.copy(); env['BAIDUPCS_GO_CONFIG_DIR']=str(CONFIG)
            command='login -cookies="'+'; '.join(k+'='+v for k,v in values.items())+'"\nquit\n'
            status.set('正在验证后台授权…'); window.update_idletasks()
            try:
                subprocess.run([str(EXE)],input=command,encoding='utf-8',env=env,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                (CONFIG/'pcs_command_history.txt').unlink(missing_ok=True)
                check=subprocess.run([str(EXE),'who'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
                text=check.stdout.decode('utf-8',errors='replace')
                if not re.search(r'uid:\s*[1-9][0-9]*', text):
                    status.set('后台登录尚未确认，请保留窗口，等待进一步检查。'); return
                (ROOT/'baidu-connected.flag').write_text('connected',encoding='ascii')
                status.set('账号已连接，可以关闭此窗口。')
                messagebox.showinfo('完成','后台授权已连接；接下来进行真实下载测试。')
            except Exception as error:
                status.set('连接未完成：'+type(error).__name__)
        tk.Button(window,text='已扫码，连接此账号到下载器',command=connect,padx=15,pady=8).pack()
        tk.Label(window,textvariable=status,wraplength=510,pady=10).pack()
        window.mainloop(); browser.close()

if __name__=='__main__': main()
