<#
.SYNOPSIS
    注册开机自启，登录后自动在后台运行下载器服务。

.DESCRIPTION
    用计划任务在「用户登录时」启动，而不是「开机时」。原因：
    你的代理客户端（127.0.0.1:10808）是跑在用户会话里的普通程序。
    如果服务以 SYSTEM 身份在登录前启动，就拿不到这个代理，TikTok 会全部失败。

    另外延迟 40 秒再启动，留时间给代理客户端先跑起来——
    服务比代理先启动的话，前几个任务同样会失败。

    卸载：  Install-AutoStart.ps1 -Remove
#>

param([switch]$Remove)

$TaskName = '视频下载器-自启'
$Root     = $PSScriptRoot
$Python   = Join-Path $Root '.venv\Scripts\pythonw.exe'
$Port     = 8000

if ($Remove) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已移除开机自启任务「$TaskName」。" -ForegroundColor Green
    } else {
        Write-Host "没有找到任务「$TaskName」，无需移除。" -ForegroundColor DarkGray
    }
    Read-Host '按回车退出'
    exit 0
}

if (-not (Test-Path $Python)) {
    # pythonw.exe 无控制台窗口；没有就退回 python.exe
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
}
if (-not (Test-Path $Python)) {
    Write-Host '错误：找不到 Python 环境（.venv）。请先按使用说明重建环境。' -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host '检测到已有任务，先移除再重新注册。' -ForegroundColor DarkGray
}

# 必须走 server.py 这个壳，不能直接 -m uvicorn：
# pythonw 没有控制台，sys.stdout 是 None，uvicorn 的日志处理器会直接把进程弄崩，
# 表现是任务显示运行过但端口根本没监听。server.py 会先把输出接到 server.log。
$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "`"$(Join-Path $Root 'server.py')`"" `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT40S'   # 等代理客户端先起来

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

# 以当前用户身份、登录后运行，才能用到用户会话里的代理
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description '开机登录后自动启动局域网视频下载器' | Out-Null

Write-Host ''
Write-Host "已注册开机自启：$TaskName" -ForegroundColor Green
Write-Host ''
Write-Host '  触发时机：你登录 Windows 后 40 秒' -ForegroundColor DarkGray
Write-Host '  运行方式：后台无窗口' -ForegroundColor DarkGray
Write-Host '  异常退出：自动重试 3 次，每次间隔 1 分钟' -ForegroundColor DarkGray
Write-Host ''
Write-Host '以后开机登录就自动有服务，不用再双击启动器。' -ForegroundColor Cyan
Write-Host ''
Write-Host '想关掉自启，运行：  Install-AutoStart.ps1 -Remove' -ForegroundColor DarkGray
Write-Host '想立刻启动一次，运行下面这条（或直接重启）：' -ForegroundColor DarkGray
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor DarkGray
Write-Host ''
Read-Host '按回车退出'
