@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0" || exit /b 1

if not exist "frontend\dist\index.html" (
  echo Frontend build is missing. Run setup_rdp.ps1 first.
  exit /b 1
)

if exist ".venv\Scripts\python.exe" (
  if exist "native\bin\rms-gateway.exe" (
    call start-native.cmd
    exit /b !errorlevel!
  )
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

echo Python was not found. Install Python and the packages in requirements.txt.
exit /b 1
