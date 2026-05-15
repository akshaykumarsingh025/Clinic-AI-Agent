@echo off
title Clinic AI Agent - Control Panel
echo ============================================
echo   Clinic AI Agent - Full Setup ^& Start
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
echo [3/8] Checking FFmpeg (required for voice messages)...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: FFmpeg not found in PATH.
    echo Voice message processing will not work without FFmpeg.
    echo Download from: https://www.gyan.dev/ffmpeg/builds/
    echo Extract and add the bin folder to your system PATH.
    echo.
    echo Continuing without FFmpeg...
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
echo [5/8] Installing Python dependencies (this may take a few minutes)...
pip install -r requirements.txt -q --no-warn-script-location 2>nul
if %errorlevel% neq 0 (
    echo WARNING: Some packages failed. Retrying...
    pip install -r requirements.txt --no-warn-script-location
)
echo.

:: ── Install TTS packages ────────────────────────
echo [6/8] Installing TTS packages (Chatterbox + Qwen3)...
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

:: ── Check configuration ─────────────────────────
echo [8/8] Checking configuration...

:: Check .env
if not exist ".env" (
    echo WARNING: .env file not found! Creating from .env.example...
    if exist ".env.example" (
        copy .env.example .env
        echo Created .env from .env.example. Please edit it with your settings.
    ) else (
        echo ERROR: No .env file found. Please create one.
    )
)

:: Check Google service account
if not exist "googlekey\service_account.json" (
    echo.
    echo WARNING: googlekey\service_account.json not found.
    echo Google Sheets sync requires a service account key file.
    echo Download from Google Cloud Console and place in googlekey\ folder.
)

:: Check Google OAuth token
if not exist "googlekey\oauth_token.json" (
    echo.
    echo Google Drive uploads require OAuth2 authorization.
    if exist "googlekey\oauth_credentials.json" (
        echo Running OAuth2 setup...
        python setup_drive_auth.py
    ) else (
        echo WARNING: googlekey\oauth_credentials.json not found.
        echo Create OAuth2 credentials in Google Cloud Console:
        echo   https://console.cloud.google.com/apis/credentials
        echo Save as googlekey\oauth_credentials.json
        echo Then run: python setup_drive_auth.py
    )
)

:: Check voice samples
dir /b "voices\hindi\*.wav" >nul 2>&1
if %errorlevel% neq 0 (
    dir /b "voices\hindi\*.mp3" >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo NOTE: No Hindi voice sample found in voices\hindi\
        echo Drop a .wav or .mp3 file there for Chatterbox Hindi voice cloning.
    )
)
dir /b "voices\english\*.wav" >nul 2>&1
if %errorlevel% neq 0 (
    dir /b "voices\english\*.mp3" >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo NOTE: No English voice sample found in voices\english\
        echo Drop a .wav or .mp3 file there for Qwen3 English voice cloning.
    )
)

echo.
echo ============================================
echo   Setup Complete! Launching Control Panel...
echo ============================================
echo.
echo   Click "Start All" in the UI to run everything.
echo.

:: ── Launch Control Panel (handles all services) ──
python clinic_control.py
if %errorlevel% neq 0 (
    echo.
    echo Control panel exited with an error.
    pause
)
