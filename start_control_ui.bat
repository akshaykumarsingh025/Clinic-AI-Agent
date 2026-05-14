@echo off
title Clinic AI Agent - Control Panel
echo ============================================
echo   Clinic AI Agent - Control Panel Startup
echo ============================================
echo.

cd /d %~dp0

:: ── Check Python ──────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: ── Install dependencies if needed ──────────────
echo Checking Python dependencies...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo WARNING: Some packages failed. Retrying...
    pip install -r requirements.txt
)
echo.

:: ── Check tkinter ──────────────────────────────
python -c "import tkinter" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: tkinter is not available. Please reinstall Python
    echo with the "tcl/tk and IDLE" option enabled.
    pause
    exit /b 1
)

echo Starting Control Panel...
echo.
python clinic_control.py
if %errorlevel% neq 0 (
    echo.
    echo Control panel exited with an error.
    pause
)
