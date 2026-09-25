<#
.SYNOPSIS
    放行 8000 端口，让局域网内其它设备能访问下载器。只需运行一次。

.DESCRIPTION
    必须以管理员身份运行（右键 -> 以管理员身份运行 PowerShell）。
    规则只对私有网络生效（家庭/办公 WiFi），公共网络下不会开放，
    所以拿去咖啡馆连公共 WiFi 时不会把服务暴露给陌生人。
#>

$Port = 8000
$Name = '视频下载器'

$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $admin) {
    Write-Host ''
    Write-Host '需要管理员权限。' -ForegroundColor Red
    Write-Host '请右键这个文件 -> 使用 PowerShell 运行（以管理员身份），或在管理员 PowerShell 里执行。' -ForegroundColor Yellow
    Read-Host '按回车退出'
    exit 1
}

$existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "规则「$Name」已存在，无需重复添加。" -ForegroundColor Green
    $existing | Format-Table DisplayName, Enabled, Direction, Action, Profile -AutoSize | Out-String | Write-Host
    Read-Host '按回车退出'
    exit 0
}

New-NetFirewallRule -DisplayName $Name `
    -Description '允许局域网设备访问本机的视频下载器网页' `
    -Direction Inbound -Protocol TCP -LocalPort $Port `
    -Action Allow -Profile Private | Out-Null

Write-Host ''
Write-Host "已添加防火墙规则：$Name（TCP $Port，仅私有网络）" -ForegroundColor Green
Write-Host '现在局域网里的手机、平板、其它电脑就能访问了。' -ForegroundColor Green
Write-Host ''
Write-Host '想删除的话，在管理员 PowerShell 里运行：' -ForegroundColor DarkGray
Write-Host "  Remove-NetFirewallRule -DisplayName '$Name'" -ForegroundColor DarkGray
Write-Host ''
Read-Host '按回车退出'
