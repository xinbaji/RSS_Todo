@echo off
rem ============================================================
rem  RSS_Todo launcher (ASCII only, CRLF - safe for any codepage)
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title RSS_Todo

set "PY="

rem ---- 1) project venv ----
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

rem ---- 2) Windows Python Launcher (most reliable) ----
if not defined PY (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)

rem ---- 3) python on PATH (skip Microsoft Store stub) ----
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PY (
            echo %%i | findstr /i "WindowsApps" >nul 2>nul
            if errorlevel 1 (
                "%%i" -c "import sys" >nul 2>nul
                if not errorlevel 1 set "PY=%%i"
            )
        )
    )
)

rem ---- 4) common install locations as fallback ----
if not defined PY (
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if not defined PY if exist "%%d\python.exe" set "PY=%%d\python.exe"
    )
)
if not defined PY (
    for /d %%d in ("C:\Python3*") do (
        if not defined PY if exist "%%d\python.exe" set "PY=%%d\python.exe"
    )
)

rem ---- 5) no interpreter found ----
if not defined PY (
    echo.
    echo [ERROR] No Python found.
    echo         Install Python 3.9+ from https://www.python.org/downloads/
    echo         and check "Add Python to PATH", then run this script again.
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY%
"%PY%" --version

rem ---- 6) dependency check / auto install ----
"%PY%" -c "import flask, requests, yt_dlp, imageio_ffmpeg, lxml, playwright" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [INFO] Installing dependencies, please wait...
    echo.
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency install failed. Run this manually:
        echo         "%PY%" -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
    echo [OK] Dependencies ready.
)

rem ---- 7) launch (keep window) ----
echo.
echo Starting RSS_Todo ... browser opens at http://127.0.0.1:8848
echo Extra args supported: --no-browser  --port 9000
echo Press Ctrl+C to stop.
echo.
"%PY%" app.py %*

echo.
echo RSS_Todo exited. Press any key to close.
pause
