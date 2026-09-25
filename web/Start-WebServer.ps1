<#
.SYNOPSIS
    启动局域网视频下载器，并打印家里人要访问的地址。
#>

$ErrorActionPreference = 'Continue'
$Host.UI.RawUI.WindowTitle = '视频下载器 - 服务端'

$Root   = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Port   = 8000

if (-not (Test-Path $Python)) {
    Write-Host '错误：找不到 Python 环境（.venv）。' -ForegroundColor Red
    Write-Host '请先在本目录运行： python -m venv .venv' -ForegroundColor Yellow
    Read-Host '按回车退出'
    exit 1
}

# 取本机局域网地址。优先有默认网关的那块网卡，避免拿到虚拟网卡（VMware/WSL）的地址。
$ip = $null
$best = Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
        Select-Object -First 1
if ($best) { $ip = $best.IPv4Address.IPAddress }
if (-not $ip) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.IPAddress -match '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' } |
           Select-Object -First 1).IPAddress
}

# 防火墙规则是局域网访问不通的头号原因，没有就提示
$rule = Get-NetFirewallRule -DisplayName '视频下载器' -ErrorAction SilentlyContinue

Clear-Host
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '              视频下载器 - 服务端已启动' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''
Write-Host '  家里人在同一个 WiFi / 路由器下，浏览器打开：' -ForegroundColor White
Write-Host ''
if ($ip) {
    Write-Host "      http://$ip`:$Port" -ForegroundColor Green
} else {
    Write-Host '      （未检测到局域网地址，请检查网络连接）' -ForegroundColor Red
}
Write-Host ''
Write-Host "  你自己也可以用：http://localhost:$Port" -ForegroundColor DarkGray
Write-Host ''
if (-not $rule) {
    Write-Host '  注意：还没有添加防火墙规则，别的设备可能连不上。' -ForegroundColor Yellow
    Write-Host '        右键以管理员身份运行 Add-FirewallRule.ps1 添加一次即可。' -ForegroundColor Yellow
    Write-Host ''
}
Write-Host '  这个窗口关掉，链接就失效了。下载期间请保持开着。' -ForegroundColor DarkGray
Write-Host '  按 Ctrl+C 停止服务。' -ForegroundColor DarkGray
Write-Host '------------------------------------------------------------' -ForegroundColor DarkGray
Write-Host ''

Set-Location $Root
& $Python -m uvicorn app:app --host 0.0.0.0 --port $Port
