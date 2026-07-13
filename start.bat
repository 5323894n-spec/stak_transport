@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=%~dp0"
set "PORT=8001"
set "URL=http://127.0.0.1:%PORT%/"
cd /d "%APP_DIR%"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

title ATP Servis V2
cls
echo ===============================================
echo   ATP Servis V2 - start
echo ===============================================
echo.
echo App folder: %APP_DIR%
echo App URL:    %URL%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if not errorlevel 1 (
    echo Program is already running. Opening browser...
    start "" "%URL%"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 3" >nul 2>nul
    exit /b 0
)

echo Starting server. Keep this window open while using the program.
echo.
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%URL%'"
"%PYTHON%" run.py --port %PORT%

echo.
echo Server stopped.
pause
