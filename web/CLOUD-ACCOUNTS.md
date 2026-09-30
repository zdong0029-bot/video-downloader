# 网盘账号管理

打开 `/cloud-accounts.html`，使用下载器管理员账号登录。

- 主机使用 `http://127.0.0.1:8000/cloud-accounts.html`，点击扫码添加 / 重新授权。
- 扫码在独立官方浏览器窗口完成，点击保存后回管理页刷新，再选择账号。
- 百度账号切换接入实际下载配置；下载进行中拒绝切换。旧账号保留。
- 阿里、夸克目前仅保存和选择加密授权，下载驱动尚未接通，不能视为下载可用。
- Windows DPAPI 绑定当前运行用户；换电脑/Windows用户需要重新授权。
- 凭据在 web/data/cloud-accounts 内，百度运行配置在 web/private-baidu 内，均不提交仓库。
- 会员有效期需到官方网页查看；登录验证不代表整文件下载验证通过。

运行依赖 web/requirements.txt 的 Playwright，以及百度下载组件 web/pcs/BaiduPCS-Go.exe（可位于子目录）。界面不自动安装组件。
