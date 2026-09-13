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
if not exist "%ROOT%\scripts\dfip_demo_prepare.py" (
    echo FAIL: Missing scripts\dfip_demo_prepare.py
    exit /b 1
)

REM Match pyproject.toml [tool.pytest.ini_options] pythonpath. Not a secret store.
set "PYTHONPATH=%ROOT%\packages\core;%ROOT%\packages\db;%ROOT%\packages\shared;%ROOT%\packages\config;%ROOT%\packages\api;%ROOT%\packages\web;%ROOT%\packages\analytics"

REM Pin local demo bind/advertise URLs. Do not inherit a stale parent-shell
REM DFIP_API_BASE_URL such as http://127.0.0.1:8010.
set "DFIP_API_BASE_URL=http://127.0.0.1:8000"
set "DFIP_API_PORT=8000"
set "DFIP_WEB_PORT=3000"

REM Do not "import dfip_api, dfip_web" here. import dfip_web loads
REM dfip_web.app and constructs the FastAPI app at import time. Entrypoints
REM were already checked above; runtime validation is python -m dfip_api / dfip_web.
echo Entrypoints present: packages\api\dfip_api\__main__.py
echo Entrypoints present: packages\web\dfip_web\__main__.py

echo Checking auth configuration names only (values are never printed)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_env_presence.ps1" -RepoRoot "%ROOT%" -RequireDemoLogin
if errorlevel 1 exit /b 1

echo Preparing local demo database and identities...
"%PY%" "%ROOT%\scripts\dfip_demo_prepare.py"
set "PREPARE_EXIT=%ERRORLEVEL%"
if not "%PREPARE_EXIT%"=="0" (
    echo FAIL: Local demo prepare did not succeed.
    echo Step: scripts\dfip_demo_prepare.py
    echo Command: python scripts\dfip_demo_prepare.py
    echo Exit code: %PREPARE_EXIT%
    echo Next: fix the stderr message above, then run START_DFIP_DEMO.bat again.
    echo Passwords and secrets are never printed.
    exit /b 1
)

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

echo Checking website API configuration...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_port.ps1" -Action web-config-check -RepoRoot "%ROOT%"
set "WEB_CFG_EXIT=%ERRORLEVEL%"
if "%WEB_CFG_EXIT%"=="3" (
    echo Website API configuration is stale/incorrect.
    echo Stopping stale website process tree on port 3000 only...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_port.ps1" -Action stop-web -RepoRoot "%ROOT%"
    if errorlevel 1 (
        echo FAIL: Could not stop the stale website on port 3000.
        echo API on port 8000 was not stopped.
        exit /b 1
    )
    goto start_web
)
if "%WEB_CFG_EXIT%"=="2" goto start_web
if not "%WEB_CFG_EXIT%"=="0" (
    echo FAIL: Website API configuration check failed.
    exit /b 1
)
echo Website already responding with API %API_URL%; skipping new website window.
goto web_ok

:start_web
echo Starting website in a new window: python -m dfip_web
echo DFIP_API_BASE_URL=%DFIP_API_BASE_URL%
start "DFIP-WEB" /D "%ROOT%" cmd /k ""%PY%" -m dfip_web"

echo Waiting for %WEB_URL% ...
set /a ELAPSED=0
:wait_web
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%WEB_URL%' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1"
if errorlevel 1 goto wait_web_retry
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\dfip_demo_port.ps1" -Action web-config-check -RepoRoot "%ROOT%"
if not errorlevel 1 goto web_ok
:wait_web_retry
set /a ELAPSED+=2
if %ELAPSED% GEQ %WAIT_SECONDS% (
    echo.
    echo WEBSITE FAILED TO START
    echo Command: python -m dfip_web
    echo Working directory: %ROOT%
    echo Expected URL: %WEB_URL%
    echo Expected /config.json apiBaseUrl: %DFIP_API_BASE_URL%
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
echo SPA API:  %DFIP_API_BASE_URL%
echo.
echo SPA login username: demo-publisher
echo Client portal username: demo-client
echo Passwords come from DFIP_LOCAL_DEMO_*_PASSWORD in the environment / .env
echo and are never printed here.
echo Optional Company 2 is not created by this launcher.
echo Stop:     double-click STOP_DFIP_DEMO.bat
echo Secrets:  not printed
echo ============================================================
exit /b 0
pause
