@echo off
rem Trae2api-cn tray launcher (debug: keeps a console window with live logs).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run these first:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt -r requirements-tray.txt
  pause
  exit /b 1
)
echo Starting Trae2api-cn tray in debug mode. Close this window to stop.
".venv\Scripts\python.exe" "tray_app.py"
if errorlevel 1 pause
