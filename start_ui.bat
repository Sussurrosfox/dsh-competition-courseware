@echo off
setlocal
title LangGraph Courseware Pipeline Launcher

:: Force Python UTF-8 encoding environment
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

:: Check if Python is installed
where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo ======================================================================
    echo   [ERROR] Python is not found in system PATH.
    echo   Please install Python 3.10+ and check 'Add Python to PATH'.
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: Launch the Web UI Workbench
echo ======================================================================
echo   Launching LangGraph Courseware Workbench...
echo ======================================================================
echo.

python launcher.py %*

if errorlevel 1 (
    echo.
    echo [ERROR] Launcher stopped with error code %errorlevel%.
    pause
)

endlocal
