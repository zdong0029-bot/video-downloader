$ErrorActionPreference='Stop'
$caddy=(Get-Command caddy.exe -ErrorAction Stop).Source
& $caddy run --config (Join-Path $PSScriptRoot 'Caddyfile') --adapter caddyfile 2>> (Join-Path $PSScriptRoot 'gateway.log')
exit $LASTEXITCODE
