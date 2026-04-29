@echo off
echo Starting Clinic AI Agent...
echo.

echo Starting FastAPI backend...
start "FastAPI" cmd /k "cd /d %~dp0 && pip install -r requirements.txt && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload"

echo Waiting 5 seconds for backend to start...
timeout /t 5 /nobreak >nul

echo Starting WhatsApp bot...
start "WhatsApp Bot" cmd /k "cd /d %~dp0\whatsapp-bot && npm install && node index.js"

echo.
echo All services starting!
echo Scan the QR code in the WhatsApp Bot window with your clinic WhatsApp.
echo Admin dashboard: http://localhost:8000/admin
echo API docs: http://localhost:8000/docs
