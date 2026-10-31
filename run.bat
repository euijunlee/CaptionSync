@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

where python >nul 2>nul
if errorlevel 1 goto no_python

python -c "import tkinter, captionsync" >nul 2>nul
if errorlevel 1 goto python_error

where pythonw >nul 2>nul
if not errorlevel 1 (
    pythonw -c "import tkinter, captionsync" >nul 2>nul
    if not errorlevel 1 (
        start "CaptionSync" pythonw -m captionsync %*
        exit /b 0
    )
)

echo [CaptionSync] pythonw could not start; using visible Python mode.
python -m captionsync %*
exit /b %errorlevel%

:no_python
echo.
echo [CaptionSync] Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/windows/
pause
exit /b 1

:python_error
echo.
echo [CaptionSync] Python or Tkinter could not be loaded.
echo Run this file from a command prompt to see the detailed error:
echo     python -m captionsync
echo.
python -c "import tkinter, captionsync"
pause
exit /b 1
