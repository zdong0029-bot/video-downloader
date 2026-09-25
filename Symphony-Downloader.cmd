@echo off
rem 旧入口，已并入统一下载器。保留此文件是为了让原有快捷方式继续可用。
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Downloader.ps1"
endlocal
