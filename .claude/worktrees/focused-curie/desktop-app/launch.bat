@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

title Toast POS Manager - Launcher
color 0A

echo.
echo  ==============================================
echo    Toast POS Manager - Unified Desktop App
echo  ==============================================
echo.

cd /d "%~dp0"

echo [1/4] Checking Python...
set "PYTHON_CMD="

python --version >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
    goto :python_found
)

py -3 --version >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)

python3 --version >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=python3"
    goto :python_found
)

echo Python not found. Please install Python 3.12+ and add it to PATH.
pause
exit /b 1

:python_found
for /f "tokens=*" %%v in ('%PYTHON_CMD% --version 2^>^&1') do set "PY_VER=%%v"
echo   Found: %PY_VER%

echo.
echo [2/4] Checking dependencies...
%PYTHON_CMD% -c "import customtkinter, tkcalendar, openpyxl, psutil" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing Python packages...
    %PYTHON_CMD% -m pip install --upgrade pip
    %PYTHON_CMD% -m pip install -r "%~dp0requirements.txt"
    if %errorlevel% neq 0 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
) else (
    echo   Core packages ready.
)

echo.
echo [3/4] Checking Playwright browser...
%PYTHON_CMD% -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing Chromium browser...
    %PYTHON_CMD% -m playwright install chromium
)
echo   Chromium ready.

echo.
echo [4/4] Launching app...
set PYTHONIOENCODING=utf-8
%PYTHON_CMD% "%~dp0app.py"

if %errorlevel% neq 0 (
    echo.
    echo App exited with error. Press any key to close.
    pause >nul
)
