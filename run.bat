@echo off
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m captionsync %*
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m captionsync %*
    exit /b %errorlevel%
)

echo Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/windows/
pause
exit /b 1
