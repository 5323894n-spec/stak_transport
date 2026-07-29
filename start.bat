@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=%~dp0"
set "PORT=8001"
set "URL=http://127.0.0.1:%PORT%/"
cd /d "%APP_DIR%"

set "PYTHON="
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import fastapi, uvicorn, openpyxl, multipart" >nul 2>nul
    if not errorlevel 1 set "PYTHON=.venv\Scripts\python.exe"
)
if not defined PYTHON (
    python -c "import fastapi, uvicorn, openpyxl, multipart" >nul 2>nul
    if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
    py -c "import fastapi, uvicorn, openpyxl, multipart" >nul 2>nul
    if not errorlevel 1 set "PYTHON=py"
)
if not defined PYTHON (
    echo ERROR: Python with required modules was not found.
    echo Install dependencies with: py -m pip install -r requirements.txt
    echo Or create .venv and install requirements.txt into it.
    pause
    exit /b 1
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
