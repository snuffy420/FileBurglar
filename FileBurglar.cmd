@echo off
rem Launches File Burglar with no console window, using Python 3.12 via the py launcher.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3.12 was not found. Install it from python.org, then run this again.
    pause
    exit /b 1
)

start "" pyw -3.12 "%~dp0main.py"
endlocal
