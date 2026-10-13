param([switch]$Json,[switch]$Repair,[switch]$Restore)
$ErrorActionPreference='Stop'
$site='video-downloader.tail49ff9c.ts.net'
$base="https://$site"
$dir=Join-Path $env:LOCALAPPDATA 'VideoDownloader-ClientChecker'
$backup=Join-Path $dir 'backup.json'
$hostsPath=Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$marker="# VideoDownloader Client Checker: $site"
$reg='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
function Is-Admin {return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)}
function Elevate([string]$mode){
    Start-Process powershell.exe -Verb RunAs -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -'+$mode)
}
function Test-Site([string]$ip='',[switch]$SystemProxy){
    $start=Get-Date
    try{
        if($SystemProxy){
            $r=Invoke-WebRequest -Uri "$base/api/me" -UseBasicParsing -TimeoutSec 10
            $obj=$r.Content | ConvertFrom-Json
        }else{
            $arguments=@('--noproxy','*','--silent','--show-error','--connect-timeout','4','--max-time','8')
            if($ip){$arguments+=@('--resolve',"${site}:443:$ip")}
            $raw=& curl.exe @arguments "$base/api/me" 2>$null
            if($LASTEXITCODE -ne 0){throw 'TLS/network failure'}
            $obj=($raw -join "`n") | ConvertFrom-Json
        }
        $ok=($null -ne $obj.device -and $null -ne $obj.is_admin -and $null -ne $obj.local)
        return [pscustomobject]@{ip=$ip;ok=$ok;ms=[int]((Get-Date)-$start).TotalMilliseconds}
    }catch{return [pscustomobject]@{ip=$ip;ok=$false;ms=[int]((Get-Date)-$start).TotalMilliseconds}}
}
function Diagnose {
    if(!(Get-Command curl.exe -ErrorAction SilentlyContinue)){throw '此电脑缺少 curl.exe，请使用更新后的 Windows 10/11。'}
    $direct=Test-Site
    $proxy=Test-Site -SystemProxy
    $ips=@();$fresh=$false
    foreach($dns in @('1.1.1.1','8.8.8.8')){
        try{
            $ips=@(Resolve-DnsName $site -Type A -Server $dns -DnsOnly -NoHostsFile -QuickTimeout -ErrorAction Stop |
                Where-Object {$_.Type -eq 'A'} | Select-Object -ExpandProperty IPAddress -Unique)
            if($ips.Count){$fresh=$true;break}
        }catch{}
    }
    if(!$ips.Count){
        try{$ips=@(Resolve-DnsName $site -Type A -DnsOnly -NoHostsFile -QuickTimeout -ErrorAction Stop | Where-Object {$_.Type -eq 'A'} | Select-Object -ExpandProperty IPAddress -Unique)}catch{}
    }
    $results=@(foreach($ip in ($ips | Select-Object -First 6)){Test-Site $ip})
    $good=@($results | Where-Object ok | Sort-Object ms)
    $report=[pscustomobject]@{time=(Get-Date -Format o);website=$base;direct=$direct.ok;systemProxy=$proxy.ok;publicDns=$fresh;endpoints=$results;healthyCount=$good.Count}
    New-Item -ItemType Directory -Force $dir | Out-Null
    $report | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $dir 'last-check.json')
    return $report
}
function Refresh-Network {
    Clear-DnsClientCache
    Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class VDCheckerWinInet{[DllImport("wininet.dll")]public static extern bool InternetSetOption(IntPtr h,int o,IntPtr b,int n);}' -ErrorAction SilentlyContinue
    [void][VDCheckerWinInet]::InternetSetOption([IntPtr]::Zero,39,[IntPtr]::Zero,0)
    [void][VDCheckerWinInet]::InternetSetOption([IntPtr]::Zero,37,[IntPtr]::Zero,0)
}
function Remove-ManagedLines([string[]]$lines){
    $output=New-Object 'System.Collections.Generic.List[string]'
    for($i=0;$i -lt $lines.Count;$i++){
        if($lines[$i] -eq $marker){
            if($i+1 -ge $lines.Count -or $lines[$i+1] -notmatch ('^\d+\.\d+\.\d+\.\d+\s+'+[regex]::Escape($site)+'\s*$')){throw '修复记录被其他程序修改，停止自动改写 hosts。'}
            $i++
        }else{$output.Add($lines[$i])}
    }
    return $output.ToArray()
}
function Undo-Repair {
    if(!(Test-Path $backup)){Write-Host '没有本工具的修复记录，无需还原。';return}
    $saved=Get-Content $backup -Raw | ConvertFrom-Json
    $lines=@(Remove-ManagedLines ([IO.File]::ReadAllLines($hostsPath)))
    [IO.File]::WriteAllLines($hostsPath,$lines)
    if(!$saved.hadProxyBypass){
        $value=[string](Get-ItemProperty $reg).ProxyOverride
        Set-ItemProperty $reg ProxyOverride (($value -split ';' | Where-Object {$_ -ne $site}) -join ';')
    }
    Refresh-Network
    Remove-Item -LiteralPath $backup -Force
    Write-Host '已撤销本工具添加的网址映射和代理例外；其他配置保留。' -ForegroundColor Green
}
try{
    if(($Repair -or $Restore) -and !(Is-Admin)){
        Elevate $(if($Restore){'Restore'}else{'Repair'});exit
    }
    if($Restore){Undo-Repair;exit}
    if(!$Json){Write-Host '正在检查视频下载器，通常需要 10—60 秒…' -ForegroundColor Cyan}
    $report=Diagnose
    if($Json){$report | ConvertTo-Json -Depth 5;exit}
    Write-Host "网站：$base"
    Write-Host "本机直连正常：$($report.direct)；系统代理路径正常：$($report.systemProxy)"
    Write-Host "可用公网入口：$($report.healthyCount) / $($report.endpoints.Count)"
    $report.endpoints | Format-Table ip,ok,ms
    if($Repair){
        $good=@($report.endpoints | Where-Object ok | Sort-Object ms)
        if(!$good.Count){throw '所有已检测入口都不可达，无法通过本机映射修复。请联系主机维护者检查服务或网络。'}
        $ip=$good[0].ip
        $lines=@(Remove-ManagedLines ([IO.File]::ReadAllLines($hostsPath)))
        if(@($lines | Where-Object {$_ -match ('^\s*[^#\r\n]+\s+'+[regex]::Escape($site)+'(?:\s|$)')}).Count){throw '已有其他程序为此网站设置映射。本工具不会覆盖，请先联系维护者。'}
        if(!(Test-Path $backup)){
            @{hadProxyBypass=(([string](Get-ItemProperty $reg).ProxyOverride -split ';') -contains $site);created=(Get-Date -Format o)} | ConvertTo-Json | Set-Content -Encoding UTF8 $backup
        }
        [IO.File]::WriteAllLines($hostsPath,@($lines)+@($marker,"$ip $site"))
        $value=[string](Get-ItemProperty $reg).ProxyOverride
        if(($value -split ';') -notcontains $site){Set-ItemProperty $reg ProxyOverride (($value.TrimEnd(';')+';'+$site).TrimStart(';'))}
        Refresh-Network
        $verified=Test-Site
        $verifiedProxy=Test-Site -SystemProxy
        if($verified.ok -and $verifiedProxy.ok){Write-Host '修复已验证成功。请重新打开网站；若浏览器仍缓存旧连接，关闭该网站标签后重试。' -ForegroundColor Green}
        else{Write-Host '配置已应用，但复测尚未完全通过。不能认定修复成功；可运行“还原设置.cmd”撤销。' -ForegroundColor Yellow}
    }else{
        Write-Host '1 = 重新检测并修复（会请求管理员权限）；2 = 还原；其他键 = 退出'
        $choice=Read-Host '选择'
        if($choice -eq '1'){Elevate 'Repair'}elseif($choice -eq '2'){Elevate 'Restore'}
    }
}catch{Write-Host ('未完成：'+$_.Exception.Message) -ForegroundColor Red;if($Json){exit 1}}
finally{if(!$Json -and ($Repair -or $Restore)){[void](Read-Host '按回车关闭')}}
