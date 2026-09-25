<#
.SYNOPSIS
    用 yt-dlp 下载 TikTok / YouTube 等站点的视频。

.DESCRIPTION
    TikTok：yt-dlp 默认取的是无水印源（play_addr），不是网页下载按钮那个带水印的版本。
    YouTube：文件本身无水印，播放器右下角的频道图标是叠加层，下下来自然没有。
             若水印是创作者烧进画面的，本工具无法去除。

    已下载记录保存在输出目录的 .archive.txt，重复运行会自动跳过。

.PARAMETER Url
    视频链接，可传多个。

.PARAMETER Out
    保存目录。

.PARAMETER Playlist
    允许下载整个播放列表/频道。默认只下单条，避免误下几百个视频。

.PARAMETER AudioOnly
    只要音频，输出 mp3。

.PARAMETER ListOnly
    只解析不下载，看看会下到什么。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$Url,

    [string]$Out = (Join-Path (Get-Location) 'Web_Videos'),

    [switch]$Playlist,

    [switch]$AudioOnly,

    # 限制最大高度（如 1080）。0 = 不限制，取可用的最高画质。
    # 4K 视频动辄几百 MB，想省空间时用这个。
    [int]$MaxHeight = 0,

    # 已下载记录文件。默认放在脚本目录且设为隐藏，不会出现在视频文件夹里。
    [string]$ArchivePath = '',

    [switch]$ListOnly,

    # 列出该视频所有可用画质档，用于排查"这条是不是选错了版本"
    [switch]$ShowFormats
)

$ErrorActionPreference = 'Continue'

# yt-dlp 输出是 UTF-8。不设这个，中文/日文标题在控制台和日志里会变成乱码。
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------- 依赖检查

$ytdlp = Get-Command 'yt-dlp' -ErrorAction SilentlyContinue
if (-not $ytdlp) {
    # 退而找同目录下的绿色版
    $local = Join-Path $PSScriptRoot 'yt-dlp.exe'
    if (Test-Path $local) { $ytdlp = Get-Item $local }
}
if (-not $ytdlp) {
    Write-Host "未找到 yt-dlp。" -ForegroundColor Red
    Write-Host "安装方式： winget install yt-dlp.yt-dlp" -ForegroundColor Yellow
    Write-Output "RESULT ok=0 skip=0 fail=1"
    exit 1
}
$ytdlpPath = $ytdlp.Source
if (-not $ytdlpPath) { $ytdlpPath = $ytdlp.FullName }

$hasFfmpeg = [bool](Get-Command 'ffmpeg' -ErrorAction SilentlyContinue)
if (-not $hasFfmpeg) {
    Write-Host "提示：未找到 ffmpeg，将只下载已合流的单文件格式（YouTube 最高画质会受限）。" -ForegroundColor Yellow
    Write-Host "      安装： winget install Gyan.FFmpeg" -ForegroundColor DarkGray
    Write-Host ""
}

if (-not (Test-Path $Out)) { New-Item -ItemType Directory -Path $Out | Out-Null }

# 记录文件默认放脚本目录，不放视频目录——否则用户打开文件夹会看到一个莫名其妙的文档
if ([string]::IsNullOrWhiteSpace($ArchivePath)) {
    $archive = Join-Path $PSScriptRoot '.web-archive.txt'
} else {
    $archive = $ArchivePath
}

# 兼容旧版本：把遗留在视频目录里的记录迁移过来，避免已下载的视频被重复下载
$legacy = Join-Path $Out '.archive.txt'
if ((Test-Path $legacy) -and ($legacy -ne $archive)) {
    $old = @(Get-Content $legacy -ErrorAction SilentlyContinue)
    if ($old.Count -gt 0) {
        $existing = @()
        if (Test-Path $archive) { $existing = @(Get-Content $archive -ErrorAction SilentlyContinue) }
        ($existing + $old) | Select-Object -Unique | Set-Content -LiteralPath $archive -Encoding UTF8
    }
    Remove-Item -LiteralPath $legacy -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $archive)) { New-Item -ItemType File -Path $archive -Force | Out-Null }
# 设为隐藏，即使用户改了路径放进视频目录也不碍眼
try { (Get-Item -LiteralPath $archive -Force).Attributes = 'Hidden' } catch { }

# ---------------------------------------------------------------- 组装参数

$common = @(
    # 画质排序：分辨率 -> 帧率 -> 总码率。
    # yt-dlp 默认把"编码格式新"排在码率前面，于是在 TikTok 上会选中
    # 同分辨率但码率只有三分之一的 H.265 流，看起来明显发糊。
    # 这里显式让码率说话，同分辨率下永远取码率最高的那个。
    '-S', 'res,fps,tbr'
    '--no-mtime'
    # 大文件稳定性：网络抖动自动重试，卡住的连接不会无限等待。
    # --continue 是 yt-dlp 默认行为，中断后重跑同一链接会断点续传，不用从头下。
    '--retries', 'infinite'
    '--fragment-retries', 'infinite'
    '--retry-sleep', 'linear=1::8'
    '--socket-timeout', '30'
    '--file-access-retries', '5'
    '--concurrent-fragments', '4'
    '--windows-filenames'
    '--newline'
    '--ignore-errors'
)

if (-not $Playlist) { $common += '--no-playlist' }

if ($AudioOnly) {
    if (-not $hasFfmpeg) {
        Write-Host "只要音频需要 ffmpeg，请先安装： winget install Gyan.FFmpeg" -ForegroundColor Red
        Write-Output "RESULT ok=0 skip=0 fail=1"
        exit 1
    }
    $fmt = @('-f', 'ba/b', '-x', '--audio-format', 'mp3')
    $tmpl = Join-Path $Out '%(uploader)s - %(title).80s [%(id)s].%(ext)s'
}
elseif ($hasFfmpeg) {
    # bv*+ba = 最佳视频流 + 最佳音频流，由 ffmpeg 合并。这是能拿到的最高画质。
    if ($MaxHeight -gt 0) {
        $sel = "bv*[height<=$MaxHeight]+ba/b[height<=$MaxHeight]/bv*+ba/b"
    } else {
        $sel = 'bv*+ba/b'
    }
    $fmt = @('-f', $sel, '--merge-output-format', 'mp4')
    $tmpl = Join-Path $Out '%(uploader)s - %(title).80s [%(id)s].%(ext)s'
}
else {
    # 无 ffmpeg：只取已经音视频合一的格式，避免下完无法合并
    $fmt = @('-f', 'b[ext=mp4]/b')
    $tmpl = Join-Path $Out '%(uploader)s - %(title).80s [%(id)s].%(ext)s'
}

# ---------------------------------------------------------------- 执行

$ok = 0; $skip = 0; $fail = 0

foreach ($u in $Url) {

    $u = $u.Trim()
    if ([string]::IsNullOrWhiteSpace($u)) { continue }

    Write-Host ""
    Write-Host "链接 $u" -ForegroundColor Cyan

    if ($ShowFormats) {
        & $ytdlpPath -F --no-warnings $u
        Write-Host ''
        Write-Host '  本工具会选中的是：' -ForegroundColor Cyan
        & $ytdlpPath --simulate --no-warnings @common @fmt `
            --print '    %(format_id)s | %(resolution)s | %(vcodec)s | %(tbr)s kbps' $u
        if ($LASTEXITCODE -ne 0) { $fail++ }
        continue
    }

    if ($ListOnly) {
        $ytArgs = @('--simulate', '--no-warnings') + $common +
                @('--print', '%(uploader)s / %(title)s / %(resolution)s / %(duration_string)s', $u)
        & $ytdlpPath @ytArgs
        if ($LASTEXITCODE -ne 0) { $fail++ }
        continue
    }

    # 用下载前后的文件差集统计真实新增，比解析 yt-dlp 输出可靠
    $before = @{}
    Get-ChildItem $Out -File -ErrorAction SilentlyContinue |
        ForEach-Object { $before[$_.Name] = $true }

    $ytArgs = $common + $fmt + @('--download-archive', $archive, '-o', $tmpl, $u)
    & $ytdlpPath @ytArgs
    $code = $LASTEXITCODE

    # 中途失败会留下 .part / .ytdl 临时文件。这些不是成品，绝不能算成功，
    # 否则一个下到一半的 2GB 文件会被报成"下载完成"。
    $junk = @('.part', '.ytdl', '.tmp', '.temp', '.txt')
    $new = @(Get-ChildItem $Out -File -ErrorAction SilentlyContinue |
             Where-Object { -not $before.ContainsKey($_.Name) -and ($junk -notcontains $_.Extension) })

    $leftovers = @(Get-ChildItem $Out -File -ErrorAction SilentlyContinue |
                   Where-Object { $_.Extension -eq '.part' -or $_.Extension -eq '.ytdl' })

    if ($code -ne 0) {
        # 退出码说了算：哪怕落了文件，非 0 就是失败
        Write-Host "      失败（yt-dlp 退出码 $code）" -ForegroundColor Red
        if ($leftovers.Count -gt 0) {
            Write-Host "      已保留 $($leftovers.Count) 个未完成的临时文件，重跑同一链接会断点续传。" -ForegroundColor Yellow
        }
        $fail++
    }
    elseif ($new.Count -gt 0) {
        foreach ($f in $new) {
            Write-Host ("      OK  {0:N1} MB  ->  {1}" -f ($f.Length / 1MB), $f.Name) -ForegroundColor Green
        }
        $ok += $new.Count
    }
    else {
        Write-Host "      已下载过，跳过" -ForegroundColor DarkGray
        $skip++
    }
}

Write-Host ""
if ($ListOnly -or $ShowFormats) {
    Write-Host "（仅解析，未下载）" -ForegroundColor DarkGray
}
else {
    Write-Host "完成：成功 $ok，跳过 $skip，失败 $fail" -ForegroundColor Cyan
    Write-Host "保存目录：$Out"
}

# 机器可读结果行，格式与 Get-SymphonyVideos.ps1 保持一致，不要改。
Write-Output ("RESULT ok={0} skip={1} fail={2}" -f $ok, $skip, $fail)
