@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=%~dp0"
set "PORT=8001"
set "URL=http://127.0.0.1:%PORT%/"
cd /d "%APP_DIR%"

title ATP Servis V2 - update and start
cls
echo ===============================================
echo   ATP Servis V2 - update from GitHub and start
echo ===============================================
echo.
echo App folder: %APP_DIR%
echo App URL:    %URL%
echo.

rem --- find a working Python (does not require app deps yet) ---
set "PYTHON="
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
if not defined PYTHON (
    python --version >nul 2>nul && set "PYTHON=python"
)
if not defined PYTHON (
    py --version >nul 2>nul && set "PYTHON=py"
)
if not defined PYTHON (
    echo ERROR: Python was not found.
    echo Install Python 3.10+ from https://python.org and enable "Add Python to PATH".
    pause
    exit /b 1
)
echo Python: %PYTHON%
echo.

rem --- update code from GitHub ---
where git >nul 2>nul
if errorlevel 1 (
    echo WARNING: Git was not found - skipping update.
    echo Install Git from https://git-scm.com to enable automatic "git pull".
) else (
    echo Updating code from GitHub...
    git pull --ff-only
    if errorlevel 1 (
        echo WARNING: could not fast-forward ^(local changes or diverged history^).
        echo Running the current local version.
    )
)
echo.

rem --- install / update dependencies ---
echo Installing dependencies...
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: failed to install dependencies. Check your internet connection.
    pause
    exit /b 1
)
echo.

rem --- if the server is already running, just open the browser ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if not errorlevel 1 (
    echo Program is already running. Opening browser...
    start "" "%URL%"
    exit /b 0
)

echo Starting server. Keep this window open while using the program.
echo Login: admin / admin
echo.
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%URL%'"
"%PYTHON%" run.py --port %PORT%

echo.
echo Server stopped.
pause
