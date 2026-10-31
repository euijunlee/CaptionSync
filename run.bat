@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw -m captionsync %*
    exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
    start "" python -m captionsync %*
    exit /b 0
)

echo Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/windows/
pause
exit /b 1
