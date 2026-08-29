@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%CD%"

echo ============================================================
echo Stop local DFIP API / website only
echo Does not kill Excel or unrelated Python processes
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_port.ps1" -Action stop -RepoRoot "%ROOT%"
exit /b %ERRORLEVEL%
