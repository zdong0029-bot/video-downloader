param([switch]$CheckOnly,[switch]$NoGui,[switch]$Restore)
$ErrorActionPreference='Stop'
$regPath='HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters'
$stateDir=Join-Path $env:ProgramData 'VideoDownloader-IPv6Helper'
$backupPath=Join-Path $stateDir 'before.json'
$messages=New-Object 'System.Collections.Generic.List[string]'
$reboot=$false
$exitCode=0
function Say([string]$text){$messages.Add($text);Write-Host $text}
function IsAdmin {([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)}
if(!$CheckOnly -and !(IsAdmin)){
    try{
        $argsList='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'"'
        if($NoGui){$argsList+=' -NoGui'}
        if($Restore){$argsList+=' -Restore'}
        $child=Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $argsList -Verb RunAs -PassThru -Wait
        exit $child.ExitCode
    }catch{Write-Host '未获得管理员授权，未更改设置。';exit 1}
}
try{
    Say 'IPv6 一键启用与检测（保留 IPv4）'
    $adapters=@(Get-NetAdapter -Physical | Where-Object {$_.Status -ne 'Disabled'})
    if(!$adapters.Count){throw '未找到可用的物理网卡。请先启用有线或无线网卡。'}
    $reg=Get-ItemProperty -LiteralPath $regPath
    $prop=$reg.PSObject.Properties['DisabledComponents']
    $oldValue=if($prop){[uint32]$prop.Value}else{[uint32]0}
    if($Restore){
        if(!(Test-Path -LiteralPath $backupPath)){throw '本机没有本工具生成的设置备份。'}
        $saved=Get-Content -LiteralPath $backupPath -Raw | ConvertFrom-Json
        foreach($item in $saved.Adapters){
            $nic=Get-NetAdapter -Physical | Where-Object {[string]$_.InterfaceGuid -eq $item.Guid}
            if($nic -and !$item.Enabled){
                $nic | Disable-NetAdapterBinding -ComponentID ms_tcpip6 -Confirm:$false | Out-Null
                Say ('已还原网卡：'+$nic.Name)
            }
        }
        if($saved.RegistryChanged){
            if($oldValue -ne [uint32]$saved.AppliedValue){throw '注册表设置已被其他程序改动，为避免覆盖，请手动核实备份。'}
            if($saved.RegistryExisted){New-ItemProperty -LiteralPath $regPath -Name DisabledComponents -PropertyType DWord -Value ([uint32]$saved.RegistryValue) -Force | Out-Null}
            else{Remove-ItemProperty -LiteralPath $regPath -Name DisabledComponents}
            $reboot=$true
        }
        Move-Item -LiteralPath $backupPath -Destination (Join-Path $stateDir ('restored-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.json'))
        Say '已还原本工具记录的原设置。'
    }elseif(!$CheckOnly){
        # Only clear the bit that disables native IPv6. Retain IPv4, DNS, routes and tunnel policies.
        $newValue=[uint32]($oldValue -band [uint32]4294967279)
        $bindings=@(foreach($nic in $adapters){
            $binding=$nic | Get-NetAdapterBinding -ComponentID ms_tcpip6
            [pscustomobject]@{Guid=[string]$nic.InterfaceGuid;Name=$nic.Name;Enabled=[bool]$binding.Enabled}
        })
        if(!(Test-Path -LiteralPath $stateDir)){New-Item -ItemType Directory -Path $stateDir | Out-Null}
        if(!(Test-Path -LiteralPath $backupPath)){
            [pscustomobject]@{RegistryExisted=[bool]$prop;RegistryValue=$oldValue;RegistryChanged=($newValue -ne $oldValue);AppliedValue=$newValue;Adapters=$bindings} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $backupPath -Encoding UTF8
        }
        if($newValue -ne $oldValue){
            New-ItemProperty -LiteralPath $regPath -Name DisabledComponents -PropertyType DWord -Value $newValue -Force | Out-Null
            $reboot=$true
            Say '已解除 Windows 对原生 IPv6 的禁用，需要手动重启电脑生效。'
        }
        foreach($nic in $adapters){
            $binding=$nic | Get-NetAdapterBinding -ComponentID ms_tcpip6
            if(!$binding.Enabled){
                $nic | Enable-NetAdapterBinding -ComponentID ms_tcpip6 -Confirm:$false | Out-Null
                Say ('已启用 IPv6：'+$nic.Name)
            }else{Say ('IPv6 已启用：'+$nic.Name)}
        }
        Start-Sleep -Seconds 3
    }else{Say '仅检查：没有修改网络设置。'}
    if(!$Restore){
        $ids=@($adapters | Select-Object -ExpandProperty ifIndex)
        $global=@(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue | Where-Object {$_.InterfaceIndex -in $ids -and $_.IPAddress -match '^[23][0-9a-fA-F]{3}:' -and $_.AddressState -eq 'Preferred'})
        $bad=@($adapters | Get-NetAdapterBinding -ComponentID ms_tcpip6 | Where-Object {!$_.Enabled})
        if($bad.Count){Say '仍有网卡未能启用 IPv6，请检查管理员权限或组织策略。';$exitCode=2}
        elseif(!$global.Count){Say '电脑暂未获得公网 IPv6 地址。需要路由器和运营商提供 IPv6；本工具不能凭空生成可联网地址。';$exitCode=2}
        else{
            Say ('检测到公网 IPv6，网卡：'+(($global.InterfaceAlias | Select-Object -Unique)-join ', '))
            $connected=$false
            try{
                $remote=@([Net.Dns]::GetHostAddresses('api6.ipify.org') | Where-Object {$_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6})
                foreach($address in ($remote | Select-Object -First 2)){
                    $client=New-Object Net.Sockets.TcpClient([Net.Sockets.AddressFamily]::InterNetworkV6)
                    try{
                        $pending=$client.BeginConnect($address,443,$null,$null)
                        if($pending.AsyncWaitHandle.WaitOne(6000)){$client.EndConnect($pending);$connected=$client.Connected}
                    }catch{}finally{$client.Dispose()}
                    if($connected){break}
                }
            }catch{}
            if($connected){Say 'IPv6 外网连接检测成功。是否能打开下载器，还取决于主机网址和入站配置。'}
            else{Say '已有公网 IPv6，但外网连接测试未通过。可能是 DNS、路由、防火墙或测试站点不可达。';$exitCode=2}
        }
    }
    if($reboot){Say '请保存工作后自行重启，再双击“仅检测”。本工具不会自动重启。'}
    Say 'IPv4、Wi-Fi 密码、DNS、默认网关及防火墙未修改。'
}catch{Say ('处理失败：'+$_.Exception.Message);$exitCode=1}
if(!$CheckOnly -and (Test-Path -LiteralPath $stateDir)){
    $messages | Set-Content -LiteralPath (Join-Path $stateDir 'last-result.txt') -Encoding UTF8
}
if(!$NoGui){
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show(($messages -join "`r`n`r`n"),'IPv6 设置结果',[Windows.Forms.MessageBoxButtons]::OK,[Windows.Forms.MessageBoxIcon]::Information) | Out-Null
}
exit $exitCode
