@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0IPv6-Helper.ps1" -Restore
if errorlevel 1 pause
