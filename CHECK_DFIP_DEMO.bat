@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%CD%"
set "FAILED=0"

echo ============================================================
echo DFIP demo checks (no secret values)
echo Repository: %ROOT%
echo ============================================================
echo.

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY=%ROOT%\.venv\Scripts\python.exe"
    echo Python interpreter: .venv\Scripts\python.exe
) else (
    set "PY=python"
    echo Python interpreter: python on PATH
)

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Python is not available
    set "FAILED=1"
) else (
    echo [PASS] Python is available
    "%PY%" --version
)

if exist "%ROOT%\packages\api\dfip_api\__main__.py" (
    echo [PASS] API path exists: packages\api\dfip_api
) else (
    echo [FAIL] API path missing: packages\api\dfip_api
    set "FAILED=1"
)

if exist "%ROOT%\packages\web\dfip_web\__main__.py" (
    echo [PASS] Website path exists: packages\web\dfip_web
) else (
    echo [FAIL] Website path missing: packages\web\dfip_web
    set "FAILED=1"
)

if exist "%ROOT%\apps\web\static\index.html" (
    echo [PASS] Website static path exists: apps\web\static
) else (
    echo [FAIL] Website static path missing: apps\web\static
    set "FAILED=1"
)

if exist "%ROOT%\.env.example" (
    echo [PASS] Environment template exists: .env.example
) else (
    echo [FAIL] Missing .env.example
    set "FAILED=1"
)

if exist "%ROOT%\.env" (
    echo [PASS] Local .env file exists. Contents are not printed.
) else (
    echo [PASS] No .env file. App defaults or process environment still apply.
)

set "PYTHONPATH=%ROOT%\packages\core;%ROOT%\packages\db;%ROOT%\packages\shared;%ROOT%\packages\config;%ROOT%\packages\api;%ROOT%\packages\web;%ROOT%\packages\analytics"

"%PY%" -c "import dfip_api" 2>nul
if errorlevel 1 (
    echo [FAIL] API startup module not importable: python -m dfip_api
    set "FAILED=1"
) else (
    echo [PASS] API startup command available: python -m dfip_api
)

"%PY%" -c "import dfip_web" 2>nul
if errorlevel 1 (
    echo [FAIL] Website startup module not importable: python -m dfip_web
    set "FAILED=1"
) else (
    echo [PASS] Website startup command available: python -m dfip_web
)

echo.
echo Auth mode configuration (names only)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_env_presence.ps1" -RepoRoot "%ROOT%"
if errorlevel 1 (
    echo [FAIL] Auth configuration incomplete. See variable NAME above.
    set "FAILED=1"
) else (
    echo [PASS] Auth configuration required for current mode is present
)

echo.
echo Reachability (optional if not running)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { Write-Host '[PASS] API reachable: http://127.0.0.1:8000/health'; exit 0 } } catch { }; Write-Host '[PASS] API not running (checked, not required)'; exit 0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:3000/' -TimeoutSec 2; if ($r.StatusCode -eq 200) { Write-Host '[PASS] Website reachable: http://127.0.0.1:3000'; exit 0 } } catch { }; Write-Host '[PASS] Website not running (checked, not required)'; exit 0"

echo.
if "%FAILED%"=="1" (
    echo RESULT: FAIL
    exit /b 1
)
echo RESULT: PASS
exit /b 0
