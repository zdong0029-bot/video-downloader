<#
.SYNOPSIS
    统一下载器启动界面：自动识别 Symphony 分享码 / TikTok / YouTube 等链接并分派给对应引擎。
#>

$ErrorActionPreference = 'Continue'
$Host.UI.RawUI.WindowTitle = '视频下载器'

$Root          = $PSScriptRoot
$SymphonyTool  = Join-Path $Root 'Get-SymphonyVideos.ps1'
$WebTool       = Join-Path $Root 'Get-WebVideos.ps1'
$SymphonyOut   = Join-Path $Root 'Symphony_Videos'
$WebOut        = Join-Path $Root 'Web_Videos'
$LogDir        = Join-Path $Root 'Logs'

foreach ($t in @($SymphonyTool, $WebTool)) {
    if (-not (Test-Path $t)) {
        Write-Host ''
        Write-Host "错误：缺少 $(Split-Path $t -Leaf)" -ForegroundColor Red
        Write-Host "应位于：$t"
        Read-Host '按回车退出'
        exit 1
    }
}

foreach ($d in @($SymphonyOut, $WebOut, $LogDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

$script:PreviewMode = $false

# ---------------------------------------------------------------- 工具函数

function Test-YtDlp {
    return [bool](Get-Command 'yt-dlp' -ErrorAction SilentlyContinue)
}

function Install-YtDlp {
    Write-Host ''
    Write-Host '下载 TikTok / YouTube 需要 yt-dlp，当前未安装。' -ForegroundColor Yellow
    Write-Host '将通过 winget 安装 yt-dlp（开源）。' -ForegroundColor Yellow
    Write-Host 'ffmpeg 会作为依赖自动一并安装——没有它 YouTube 只能下到 720p。' -ForegroundColor Yellow
    Write-Host ''
    $yn = Read-Host '现在安装？(Y/N)'
    if ($yn -notmatch '^[Yy]') {
        Write-Host '已取消。你也可以稍后手动执行： winget install yt-dlp.yt-dlp' -ForegroundColor DarkGray
        return $false
    }

    Write-Host ''
    Write-Host '正在安装 yt-dlp 及其依赖（ffmpeg）……' -ForegroundColor Cyan
    winget install --id yt-dlp.yt-dlp --accept-package-agreements --accept-source-agreements --disable-interactivity

    # winget 装完后当前会话的 PATH 不会自动刷新，手动重载
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')

    if (Test-YtDlp) {
        Write-Host '安装完成。' -ForegroundColor Green
        return $true
    }
    Write-Host '安装后仍未检测到 yt-dlp，可能需要重开这个窗口。' -ForegroundColor Yellow
    Read-Host '按回车继续'
    return $false
}

function Show-Header {
    Clear-Host
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '                    视频下载器' -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '直接粘贴链接，自动识别来源：'
    Write-Host '  - Symphony 分享链接 / UUID   -> Symphony_Videos'
    Write-Host '  - TikTok / YouTube 等链接    -> Web_Videos（TikTok 取无水印源）'
    Write-Host ''
    Write-Host '命令：  O 打开目录    L 切换预览模式    Q 退出'
    if ($script:PreviewMode) {
        Write-Host '  当前：预览模式（只解析，不下载）' -ForegroundColor Yellow
    }
    Write-Host ''
}

# 调用下载引擎：Tee-Object 边跑边显示、同时写日志，不再攒到最后一次性输出。
# 统计来自引擎输出的 RESULT 行（纯 ASCII，措辞改动不影响解析）。
function Invoke-Engine {
    param(
        [string]$Tool,
        [hashtable]$ToolArgs,   # 必须是哈希表：数组展开是按位置传参，命名参数会绑错
        [string]$LogPath
    )

    & $Tool @ToolArgs *>&1 | Tee-Object -FilePath $LogPath

    $text = ''
    if (Test-Path $LogPath) { $text = Get-Content $LogPath -Raw }

    $m = [regex]::Match($text, 'RESULT ok=(\d+) skip=(\d+) fail=(\d+)')
    if ($m.Success) {
        return [pscustomobject]@{
            Parsed = $true
            Ok     = [int]$m.Groups[1].Value
            Skip   = [int]$m.Groups[2].Value
            Fail   = [int]$m.Groups[3].Value
        }
    }
    return [pscustomobject]@{ Parsed = $false; Ok = 0; Skip = 0; Fail = 0 }
}

function Show-Log {
    param([string]$Path)
    # 引擎如果在参数绑定阶段就崩了，Tee-Object 来不及建文件，直接开记事本会再报一个错
    if (Test-Path $Path) {
        Start-Process notepad.exe -ArgumentList @("`"$Path`"")
    } else {
        Write-Host '（本次未生成日志文件，错误信息见上方输出）' -ForegroundColor DarkGray
    }
}

# 把已有窗口拉到前台需要调 Win32 API。重复运行时类型已存在，忽略报错即可。
if (-not ('Win32Fg' -as [type])) {
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Win32Fg {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
}
'@ -ErrorAction Stop
    } catch { }
}

# 打开目录：已经开着就把那个窗口拉到前面，不再开重复窗口
function Open-Folder {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    $target = (Resolve-Path -LiteralPath $Path).ProviderPath.TrimEnd('\')

    try {
        $shell = New-Object -ComObject Shell.Application
        foreach ($w in @($shell.Windows())) {
            $cur = $null
            try {
                # 只认资源管理器窗口；IE / Edge 之类没有 Folder，直接跳过
                if ($w.Document -and $w.Document.Folder -and $w.Document.Folder.Self) {
                    $cur = $w.Document.Folder.Self.Path
                }
            } catch { continue }

            if ($cur -and $cur.TrimEnd('\') -ieq $target) {
                if ('Win32Fg' -as [type]) {
                    $hwnd = [IntPtr]$w.HWND
                    # 最小化了就先还原（SW_RESTORE = 9）
                    if ([Win32Fg]::IsIconic($hwnd)) { [void][Win32Fg]::ShowWindow($hwnd, 9) }
                    [void][Win32Fg]::SetForegroundWindow($hwnd)
                }
                return
            }
        }
    } catch {
        # COM 出问题就退回到直接打开，不要因为这个功能让整个流程挂掉
    }

    # Invoke-Item 自己处理带空格的路径，不像 Start-Process -ArgumentList 会被拆开
    Invoke-Item -LiteralPath $Path
}

# ---------------------------------------------------------------- 主循环

while ($true) {
    Show-Header
    $inputValue = Read-Host '请粘贴链接'
    if ([string]::IsNullOrWhiteSpace($inputValue)) { continue }
    $inputValue = $inputValue.Trim().Trim('"')

    if ($inputValue -match '^[Qq]$') { break }

    if ($inputValue -match '^[Ll]$') {
        $script:PreviewMode = -not $script:PreviewMode
        continue
    }

    if ($inputValue -match '^[Oo]$') {
        Write-Host ''
        Write-Host '  1  Symphony_Videos'
        Write-Host '  2  Web_Videos'
        Write-Host '  3  Logs'
        $pick = Read-Host '打开哪个目录'
        switch ($pick) {
            '1' { Open-Folder $SymphonyOut }
            '2' { Open-Folder $WebOut }
            '3' { Open-Folder $LogDir }
            default { Open-Folder $Root }
        }
        continue
    }

    # ---- 来源识别 ----
    $uuid = [regex]::Match($inputValue, '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')

    if ($uuid.Success) {
        $kind    = 'Symphony'
        $tool    = $SymphonyTool
        $outDir  = $SymphonyOut
        $tag     = $uuid.Value.Substring(0, 8)
        $engArgs = @{ ShareLink = $inputValue; Out = $outDir }
        if ($script:PreviewMode) { $engArgs['ListOnly'] = $true }
    }
    elseif ($inputValue -match '^https?://') {
        if (-not (Test-YtDlp)) {
            if (-not (Install-YtDlp)) { continue }
        }
        $kind    = 'Web'
        $tool    = $WebTool
        $outDir  = $WebOut
        $tag     = 'web'
        $engArgs = @{ Url = $inputValue; Out = $outDir }
        if ($script:PreviewMode) { $engArgs['ListOnly'] = $true }
    }
    else {
        Write-Host ''
        Write-Host '无法识别。请粘贴 Symphony 分享链接/UUID，或以 http(s):// 开头的视频链接。' -ForegroundColor Red
        Read-Host '按回车重新输入'
        continue
    }

    $stamp   = Get-Date -Format 'yyyyMMdd_HHmmss'
    $logPath = Join-Path $LogDir ("{0}_{1}_{2}.log" -f $kind, $tag, $stamp)

    Write-Host ''
    Write-Host "来源：$kind    输出：$outDir" -ForegroundColor DarkGray
    Write-Host '----------------------------------------------------------' -ForegroundColor DarkGray

    $r = $null
    try {
        $r = Invoke-Engine -Tool $tool -ToolArgs $engArgs -LogPath $logPath
    }
    catch {
        "启动器错误：$($_.Exception.Message)" | Out-File -FilePath $logPath -Encoding utf8 -Append
        Write-Host ''
        Write-Host "启动器错误：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host "日志：$logPath" -ForegroundColor Yellow
        Show-Log $logPath
        Read-Host '按回车继续'
        continue
    }

    Write-Host '----------------------------------------------------------' -ForegroundColor DarkGray
    Write-Host ''

    if ($script:PreviewMode) {
        Write-Host '预览完成，未下载。取消预览模式请输入 L。' -ForegroundColor Yellow
    }
    elseif (-not $r.Parsed) {
        # 引擎没输出 RESULT 行 —— 多半是崩在半路，不要谎报成功
        Write-Host '未能获取执行结果，可能中途出错。' -ForegroundColor Red
        Write-Host "日志：$logPath" -ForegroundColor Yellow
        Show-Log $logPath
    }
    elseif ($r.Ok -gt 0) {
        Write-Host "完成：本次成功下载 $($r.Ok) 个。" -ForegroundColor Green
        if ($r.Fail -gt 0) {
            Write-Host "另有 $($r.Fail) 个失败，详见日志：$logPath" -ForegroundColor Yellow
        }
        Write-Host '正在打开文件夹……' -ForegroundColor Cyan
        Open-Folder $outDir
    }
    elseif ($r.Skip -gt 0 -and $r.Fail -eq 0) {
        Write-Host "这些视频之前已下载过（跳过 $($r.Skip) 个），本次无新增。" -ForegroundColor Green
        Open-Folder $outDir
    }
    elseif ($r.Fail -gt 0) {
        Write-Host "没有下载到视频，$($r.Fail) 个失败。" -ForegroundColor Red
        Write-Host "日志：$logPath" -ForegroundColor Yellow
        Show-Log $logPath
    }
    else {
        # ok=skip=fail=0：引擎跑通了但什么都没解析到
        Write-Host '没有解析到可下载内容。链接可能已过期（Symphony 分享链接 30 天有效）或无效。' -ForegroundColor Red
        Write-Host "日志：$logPath" -ForegroundColor Yellow
        Show-Log $logPath
    }

    Write-Host ''
    $again = Read-Host '按回车继续；输入 Q 退出'
    if ($again -match '^[Qq]$') { break }
}
