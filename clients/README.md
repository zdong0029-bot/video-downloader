# 下载器的客户端辅助工具

这两个工具的源代码随仓库保存。生成网页提供的压缩包：

```powershell
web\.venv\Scripts\python.exe tools\build_client_packages.py
```

脚本只使用 Python 标准库，从源码生成 `web/static/folder-locator.zip` 和
`web/static/website-client-checker.zip`。ZIP 属于构建产物，由根目录 `.gitignore` 排除。
部署时执行一次打包，网页上的安装说明链接即可提供对应文件。

## Folder-Locator

Chrome / Edge 电脑版扩展：仅在当前下载器域名收到用户点击后，查询本浏览器已完成的
视频下载记录并打开文件夹。不能定位手机相册，也不能定位网页分段保存到任意目录的文件。
迁移网站域名时，需要同步修改 manifest.json 和 background.js 的站点限制。

## Website-Client-Checker

Windows 10/11 连接检查器：检测本网站响应、当前代理路径及 DNS 返回的入口。
用户主动选择修复时会请求管理员权限，设置仅针对本站的 hosts 映射和代理例外，提供还原入口。
源码中的站点域名在 Checker.ps1 配置。它不能消除 Funnel 带宽限制或替代服务器部署。

仅提交源码和网页说明，不包含任何人的下载记录、账号授权或运行日志。
