# Windows 公网入口

仅主机安装 Tailscale 与 Caddy，访客直接使用浏览器。主机需要保持开机联网。

1. 安装 `Tailscale.Tailscale` 和 `CaddyServer.Caddy`（可用 winget）。
2. 运行 `tailscale up --accept-dns=false --accept-routes=false --hostname=video-downloader`，登录自己的账号。
3. 启动原下载器，确认 http://127.0.0.1:8000 正常。
4. 运行本目录 Start-Gateway.ps1。端口 2020、18743 必须空闲。
5. 运行 `tailscale funnel --bg http://127.0.0.1:18743`，按提示首次启用 Funnel，使用返回的 HTTPS 地址。
6. 管理员运行 Register-Startup.ps1 设置网关登录后启动；`tailscale set --unattended=true` 设置 Tailscale 后台运行。

本目录只配置回环网关；公网禁止打开本机文件夹。无凭据、无需路由器端口配置。
免费个人套餐用途与带宽限制以服务商条款为准。公网网址可被转发，浏览器记录隔离不是访客身份认证。
停止公网入口：`tailscale funnel --https=443 off`。
