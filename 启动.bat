@echo off
title MCdownloader
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"

if not defined PY (
    echo [ERROR] Python not found.
    echo Please install Python 3.9+ with tkinter first.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Using interpreter: %PY%
echo Starting MCdownloader GUI...
echo.
%PY% mc_downloader.py

echo.
echo Program exited. If you see an error, take a screenshot of the log above.
pause
