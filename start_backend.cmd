@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0" || exit /b 1

netstat -ano -p tcp | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 goto :port_in_use

goto :start_backend

:port_in_use
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/manager/status' -TimeoutSec 3 -ErrorAction Stop | Out-Null; exit 0 } catch { if ($_.Exception.Response) { exit 0 }; exit 1 }"
if not errorlevel 1 (
    echo MT5 RMS backend is already running on http://127.0.0.1:8000.
    echo Reuse this backend with the Vite frontend. Stop it before starting another instance.
    exit /b 0
)
echo Port 8000 is occupied, but it is not responding as the MT5 RMS backend.
echo Stop that process or change PORT in .env before starting the backend.
exit /b 1

:start_backend

if exist "native\bin\rms-gateway.exe" (
    call start-native.cmd
    exit /b !errorlevel!
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" api.py
    exit /b !errorlevel!
)

where python >nul 2>nul
if not errorlevel 1 (
    python api.py
    exit /b !errorlevel!
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 api.py
    exit /b !errorlevel!
)

echo Python was not found.
echo Create a .venv in this folder and install dependencies before running this tool.
exit /b 1
