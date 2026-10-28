@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo RMS Python environment is missing.
  exit /b 1
)
".venv\Scripts\python.exe" "native\start_native.py"
