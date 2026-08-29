@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%CD%"
set "API_URL=http://127.0.0.1:8000"
set "WEB_URL=http://127.0.0.1:3000"
set "API_HEALTH=%API_URL%/health"
set "WAIT_SECONDS=60"

echo ============================================================
echo DFIP one-click local demo
echo Repository: %ROOT%
echo ============================================================
echo.

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY=%ROOT%\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo FAIL: Python is not available.
    echo Install Python 3.11+ and/or create .venv as in README.md
    exit /b 1
)

if not exist "%ROOT%\packages\api\dfip_api\__main__.py" (
    echo FAIL: API package missing: packages\api\dfip_api
    exit /b 1
)
if not exist "%ROOT%\packages\web\dfip_web\__main__.py" (
    echo FAIL: Website package missing: packages\web\dfip_web
    exit /b 1
)
if not exist "%ROOT%\apps\web\static\index.html" (
    echo FAIL: Website static files missing: apps\web\static
    exit /b 1
)

REM Match pyproject.toml [tool.pytest.ini_options] pythonpath. Not a secret store.
set "PYTHONPATH=%ROOT%\packages\core;%ROOT%\packages\db;%ROOT%\packages\shared;%ROOT%\packages\config;%ROOT%\packages\api;%ROOT%\packages\web;%ROOT%\packages\analytics"

echo Checking Python modules (dfip_api, dfip_web)...
"%PY%" -c "import dfip_api, dfip_web" 2>nul
if errorlevel 1 (
    echo FAIL: Cannot import dfip_api / dfip_web.
    echo From the repository root run: python -m pip install -e ".[dev]"
    echo PYTHONPATH is also set from pyproject.toml package dirs.
    exit /b 1
)

echo Checking auth configuration names only (values are never printed)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_env_presence.ps1" -RepoRoot "%ROOT%"
if errorlevel 1 exit /b 1

echo Checking ports 8000 and 3000...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_port.ps1" -Action start-check -RepoRoot "%ROOT%"
if errorlevel 1 exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%API_HEALTH%' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1"
if not errorlevel 1 (
    echo API already healthy; skipping new API window.
    goto api_ok
)

echo Starting API in a new window: python -m dfip_api
start "DFIP-API" /D "%ROOT%" cmd /k ""%PY%" -m dfip_api"

echo Waiting for %API_HEALTH% ...
set /a ELAPSED=0
:wait_api
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%API_HEALTH%' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1"
if not errorlevel 1 goto api_ok
set /a ELAPSED+=2
if %ELAPSED% GEQ %WAIT_SECONDS% (
    echo.
    echo API FAILED TO START
    echo Command: python -m dfip_api
    echo Working directory: %ROOT%
    echo Expected health: %API_HEALTH%
    echo See the DFIP-API window for the Python traceback.
    exit /b 1
)
ping 127.0.0.1 -n 3 >nul
goto wait_api

:api_ok
echo API health: PASS

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%WEB_URL%' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1"
if not errorlevel 1 (
    echo Website already responding; skipping new website window.
    goto web_ok
)

echo Starting website in a new window: python -m dfip_web
start "DFIP-WEB" /D "%ROOT%" cmd /k ""%PY%" -m dfip_web"

echo Waiting for %WEB_URL% ...
set /a ELAPSED=0
:wait_web
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%WEB_URL%' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1"
if not errorlevel 1 goto web_ok
set /a ELAPSED+=2
if %ELAPSED% GEQ %WAIT_SECONDS% (
    echo.
    echo WEBSITE FAILED TO START
    echo Command: python -m dfip_web
    echo Working directory: %ROOT%
    echo Expected URL: %WEB_URL%
    echo See the DFIP-WEB window for the Python traceback.
    exit /b 1
)
ping 127.0.0.1 -n 3 >nul
goto wait_web

:web_ok
echo Website: PASS

echo Opening browser: %WEB_URL%
start "" "%WEB_URL%"

echo.
echo ============================================================
echo DFIP local demo is running
echo ============================================================
echo API:      %API_URL%
echo Health:   %API_HEALTH%
echo Website:  %WEB_URL%
echo Login:    use an existing app_user (DEMO USER NOT SEEDED)
echo Stop:     double-click STOP_DFIP_DEMO.bat
echo Secrets:  not printed (loaded from environment / .env by the app)
echo ============================================================
exit /b 0
