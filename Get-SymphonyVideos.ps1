<#
.SYNOPSIS
    批量下载 TikTok Symphony Creative Studio 分享链接里的视频。

.DESCRIPTION
    绕开浏览器那条容易被广告拦截器掐断的下载通道，直接从 TikTok CDN 拉取原始高清文件。
    默认只取每个素材的 previewLink（最高画质原片），不会把网页播放器用的多码率版本一起下下来。

.PARAMETER ShareLink
    分享链接完整 URL，或只给最后那段 share_id（UUID）。可以一次传多个。

.PARAMETER Out
    保存目录，默认 .\symphony_downloads

.PARAMETER All
    连同各分辨率转码版一起下载（一般不需要，内容与原片相同但画质更差）。

.PARAMETER ListOnly
    只列出分享链接里有什么，不下载。

.EXAMPLE
    .\Get-SymphonyVideos.ps1 -ShareLink "https://ads.tiktok.com/creative/creativestudio/shared-link/67f9d7dc-..."

.EXAMPLE
    .\Get-SymphonyVideos.ps1 -ShareLink 67f9d7dc-13d9-490a-b9bd-3ffa33582929 -Out D:\videos

.EXAMPLE
    .\Get-SymphonyVideos.ps1 -ShareLink $id -ListOnly
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$ShareLink,

    [string]$Out = (Join-Path (Get-Location) 'symphony_downloads'),

    [switch]$All,

    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Headers = @{
    'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
    'Referer'    = 'https://ads.tiktok.com/'
}

function Get-ShareId {
    param([string]$Text)
    $m = [regex]::Match($Text, '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
    if (-not $m.Success) { return $null }
    return $m.Value
}

function Get-SafeName {
    param([string]$Name, [int]$Index)
    if ([string]::IsNullOrWhiteSpace($Name)) { $Name = "asset_$Index" }
    foreach ($ch in [IO.Path]::GetInvalidFileNameChars()) {
        $Name = $Name.Replace($ch, '_')
    }
    $Name = $Name.Trim()
    if ($Name.Length -gt 80) { $Name = $Name.Substring(0, 80) }
    return $Name
}

function Save-Url {
    param([string]$Url, [string]$Path, [int]$Retries = 5)

    for ($try = 1; $try -le $Retries; $try++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing `
                -TimeoutSec 300 -Headers $Headers -ErrorAction Stop
            # 校验是不是真的 MP4：第 5-8 字节应为 'ftyp'
            $fs = [IO.File]::OpenRead($Path)
            try {
                $buf = New-Object byte[] 12
                $null = $fs.Read($buf, 0, 12)
            } finally { $fs.Close() }
            if ([Text.Encoding]::ASCII.GetString($buf[4..7]) -ne 'ftyp') {
                throw "下载的文件不是有效 MP4（可能是错误页面）"
            }
            return $true
        }
        catch {
            if (Test-Path $Path) { Remove-Item $Path -Force -ErrorAction SilentlyContinue }
            if ($try -eq $Retries) {
                Write-Host "      失败: $($_.Exception.Message)" -ForegroundColor Red
                return $false
            }
            Start-Sleep -Seconds ([Math]::Min(2 * $try, 10))
        }
    }
    return $false
}

# ---------------------------------------------------------------- main

if (-not $ListOnly -and -not (Test-Path $Out)) {
    New-Item -ItemType Directory -Path $Out | Out-Null
}

$totalOk = 0; $totalFail = 0; $totalSkip = 0

foreach ($link in $ShareLink) {

    $shareId = Get-ShareId $link
    if (-not $shareId) {
        Write-Host "跳过：无法从 '$link' 中识别出 share_id" -ForegroundColor Yellow
        continue
    }

    Write-Host ""
    Write-Host "分享码 $shareId" -ForegroundColor Cyan

    $api = "https://ads.tiktok.com/CreativeOne/FactoryCue/SymphonyShareLink/GetShareLink?share_id=$shareId&aid=585599&app_name=creative_aio_client&device_platform=web"

    try {
        $resp = Invoke-RestMethod -Uri $api -Headers $Headers -TimeoutSec 60 -ErrorAction Stop
    }
    catch {
        Write-Host "  接口请求失败: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  提示：检查网络，或该链接已超过 30 天有效期。" -ForegroundColor DarkGray
        continue
    }

    if ($resp.BaseResp.StatusCode -ne 0) {
        Write-Host "  接口返回错误: $($resp.BaseResp.StatusMessage)" -ForegroundColor Red
        continue
    }

    $drafts = @($resp.share_link.draft_infos)
    Write-Host "  包含 $($drafts.Count) 个素材"

    $i = 0
    foreach ($d in $drafts) {
        $i++
        $name = Get-SafeName $d.name $i
        $vi   = $d.videoInfo

        $res = '?'
        if ($vi -and $vi.width) { $res = "$($vi.width)x$($vi.height)" }
        $dur = '?'
        if ($vi -and $vi.duration) { $dur = "{0:N1}s" -f $vi.duration }

        Write-Host ("  [{0}/{1}] {2}  ({3}, {4})" -f $i, $drafts.Count, $name, $res, $dur)

        # 收集待下载项：默认只要 previewLink（原始最高画质）
        $targets = @()

        if ($d.previewLink) {
            $targets += [pscustomobject]@{ Url = $d.previewLink; Suffix = '' }
        }
        elseif ($vi -and $vi.playInfos) {
            # 没有 previewLink 时，退而取分辨率最高的码流
            $best = $vi.playInfos | Sort-Object { [int]$_.width } -Descending | Select-Object -First 1
            $u = $best.playUrl
            if (-not $u) { $u = $best.backupUrl }
            if ($u) { $targets += [pscustomobject]@{ Url = $u; Suffix = "_$($best.definition)" } }
        }

        if ($All -and $vi -and $vi.playInfos) {
            foreach ($p in $vi.playInfos) {
                $u = $p.playUrl
                if (-not $u) { $u = $p.backupUrl }
                if ($u) { $targets += [pscustomobject]@{ Url = $u; Suffix = "_$($p.width)p" } }
            }
        }

        if ($targets.Count -eq 0) {
            Write-Host "      无可下载地址（可能是图片素材或尚未渲染完成）" -ForegroundColor Yellow
            continue
        }

        # 同一批素材的 name 经常重复（同一组 prompt 生成的多条），仅靠 name 会互相覆盖/误跳过。
        # 加上 vid 末 8 位保证唯一，同时让重复运行能正确跳过。
        $tag = ''
        if ($d.vid -and $d.vid.Length -ge 8) {
            $tag = '_' + $d.vid.Substring($d.vid.Length - 8)
        }

        foreach ($t in $targets) {
            $file = Join-Path $Out ("{0}{1}{2}.mp4" -f $name, $tag, $t.Suffix)

            if ($ListOnly) {
                Write-Host "      -> $(Split-Path $file -Leaf)" -ForegroundColor DarkGray
                continue
            }
            if (Test-Path $file) {
                Write-Host "      已存在，跳过" -ForegroundColor DarkGray
                $totalSkip++
                continue
            }

            if (Save-Url -Url $t.Url -Path $file) {
                $kb = [math]::Round((Get-Item $file).Length / 1KB, 1)
                Write-Host ("      OK  {0} KB  ->  {1}" -f $kb, (Split-Path $file -Leaf)) -ForegroundColor Green
                $totalOk++
            }
            else { $totalFail++ }
        }
    }
}

Write-Host ""
if ($ListOnly) {
    Write-Host "（仅列出，未下载）" -ForegroundColor DarkGray
}
else {
    Write-Host "完成：成功 $totalOk，跳过 $totalSkip，失败 $totalFail" -ForegroundColor Cyan
    Write-Host "保存目录：$Out"
}

# 机器可读结果行。启动器解析这一行而不是上面的中文，措辞改动不会影响统计。
# 格式固定为纯 ASCII，不要改。
Write-Output ("RESULT ok={0} skip={1} fail={2}" -f $totalOk, $totalSkip, $totalFail)
