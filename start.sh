#!/bin/bash
echo "Starting Clinic AI Agent..."
echo ""

# Start FastAPI backend
echo "Starting FastAPI backend..."
cd "$(dirname "$0")"
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

sleep 3

# Start WhatsApp bot
echo "Starting WhatsApp bot..."
cd whatsapp-bot
npm install
node index.js &
BOT_PID=$!

cd ..

echo ""
echo "All services running!"
echo "Scan the QR code above with clinic WhatsApp"
echo "Admin dashboard: http://localhost:8000/admin"
echo "API docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services"

trap "kill $BACKEND_PID $BOT_PID 2>/dev/null; exit" INT TERM
wait
