@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

where python >nul 2>nul
if errorlevel 1 goto no_python

python -c "import tkinter, captionsync" >nul 2>nul
if errorlevel 1 goto python_error

echo [CaptionSync] Starting in diagnostic mode...
python -m captionsync %*
if errorlevel 1 (
    echo.
    echo [CaptionSync] The application stopped with an error.
    pause
    exit /b 1
)
exit /b 0

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
