@echo off
title Clinic AI Agent - Startup
echo ============================================
echo   Clinic AI Agent - Automatic Setup ^& Start
echo ============================================
echo.

cd /d %~dp0

:: ── Check Python ──────────────────────────────
echo [1/8] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
python --version
echo.

:: ── Check Node.js ──────────────────────────────
echo [2/8] Checking Node.js...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Node.js is not installed or not in PATH.
    echo Please install Node.js 18+ from https://nodejs.org
    pause
    exit /b 1
)
node --version
echo.

:: ── Check FFmpeg ────────────────────────────────
echo [3/8] Checking FFmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: FFmpeg not found. Voice messages will not work.
    echo Download: https://www.gyan.dev/ffmpeg/builds/
    echo.
) else (
    echo FFmpeg found.
)
echo.

:: ── Create directories ─────────────────────────
echo [4/8] Creating required directories...
if not exist "database" mkdir database
if not exist "logs" mkdir logs
if not exist "audio_cache" mkdir audio_cache
if not exist "audio_cache\incoming" mkdir audio_cache\incoming
if not exist "audio_cache\id_cards" mkdir audio_cache\id_cards
if not exist "exports" mkdir exports
if not exist "static" mkdir static
if not exist "voices" mkdir voices
if not exist "voices\hindi" mkdir voices\hindi
if not exist "voices\english" mkdir voices\english
if not exist "googlekey" mkdir googlekey
echo Directories ready.
echo.

:: ── Install Python dependencies ─────────────────
echo [5/8] Installing Python dependencies...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo WARNING: Some Python packages failed to install.
    echo Retrying...
    pip install -r requirements.txt
)
echo.

:: ── Install TTS packages ────────────────────────
echo [6/8] Installing TTS packages...
pip install chatterbox-tts --no-deps -q 2>nul
pip install qwen-tts --no-deps -q 2>nul
echo TTS packages ready.
echo.

:: ── Install Node.js dependencies ─────────────────
echo [7/8] Installing WhatsApp bot dependencies...
cd whatsapp-bot
call npm install --silent 2>nul
if %errorlevel% neq 0 (
    echo WARNING: npm install failed. Retrying...
    call npm install
)
cd ..
echo.

:: ── Check .env file ────────────────────────────
echo [8/8] Checking configuration...
if not exist ".env" (
    echo WARNING: .env file not found! Creating from .env.example...
    if exist ".env.example" (
        copy .env.example .env
        echo Created .env from .env.example. Please edit it with your settings.
    ) else (
        echo ERROR: No .env file found. Please create one.
        pause
        exit /b 1
    )
)

:: Check Google OAuth token for Drive uploads
if not exist "googlekey\oauth_token.json" (
    if exist "googlekey\oauth_credentials.json" (
        echo Running Google Drive OAuth2 setup...
        python setup_drive_auth.py
    )
)

echo.

:: ── Start services ─────────────────────────────
echo ============================================
echo   Starting Services...
echo ============================================
echo.

echo Starting FastAPI backend on port 8000...
start "FastAPI Backend" cmd /k "cd /d %~dp0 && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload"

echo Waiting 5 seconds for backend to initialize...
timeout /t 5 /nobreak >nul

echo Starting WhatsApp bot on port 3001...
start "WhatsApp Bot" cmd /k "cd /d %~dp0\whatsapp-bot && node index.js"

echo.
echo ============================================
echo   All services are starting!
echo ============================================
echo.
echo   Scan the QR code in the WhatsApp Bot window
echo   with your clinic WhatsApp to connect.
echo.
echo   Backend API:  http://localhost:8000/docs
echo   Control UI:   Run start_control_ui.bat
echo.
echo   Close the individual windows to stop services.
echo.
pause
