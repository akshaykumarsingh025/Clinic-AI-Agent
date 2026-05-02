# Clinic AI Agent

AI-powered WhatsApp appointment booking assistant for clinics. Patients can book, reschedule, cancel and check appointments via WhatsApp — in English, Hindi, or Hinglish.

## Features

- **WhatsApp Bot** — Patients chat naturally; AI handles the rest
- **Multilingual** — English, Hindi, Hinglish (voice and text)
- **Voice Notes** — Whisper STT + Piper/VoiceClone TTS
- **Smart Booking** — 9 AM to 9 PM, duplicate detection, returning patient awareness
- **Reminders** — 24h and 2h automated reminders
- **No-Show Follow-up** — Automated follow-up with voice note escalation
- **Desktop Dashboard** — Tkinter control panel for settings, appointments, check-ins
- **Google Sheets Sync** — Auto-sync bookings after every appointment
- **Excel Export** — One-click appointment export

## Architecture

```
WhatsApp ←→ Baileys (Node.js, port 3001)
                ↕
           FastAPI (Python, port 8000)
                ↕
        ┌───────┼───────┐
      Ollama   SQLite   Whisper
      (LLM)   (DB)     (STT)
```

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **Ollama** with a model pulled (e.g. `ollama pull gemma4:e4b`)
- **ffmpeg** in PATH (for audio conversion)
- **Piper** binary + voice model (optional, for TTS)

## Installation

```bash
# Clone
git clone https://github.com/akshaykumarsingh025/Clinic-AI-Agent.git
cd Clinic-AI-Agent

# Python dependencies
pip install -r requirements.txt

# WhatsApp bot dependencies
cd whatsapp-bot
npm install
cd ..

# Configure
cp .env.example .env
# Edit .env with your clinic details
```

## Configuration

Copy `.env.example` to `.env` and fill in your values. Key settings:

| Setting | Description |
|---|---|
| `CLINIC_NAME` | Your clinic name |
| `DOCTOR_NAME` | Doctor's full name |
| `OLLAMA_MODEL` | AI model (select from dropdown in UI) |
| `APPOINTMENT_FEE` | Fee shown in confirmation |
| `GOOGLE_SHEET_URL` | Google Sheet for sync (optional) |

## Running

### Option 1: Desktop Control Panel (recommended)

```bash
python clinic_control.py
```

This opens the unified dashboard where you can:
- Start/stop backend and WhatsApp bot
- Scan QR code to connect WhatsApp
- Manage settings, appointments, check-ins
- Export data and sync to Google Sheets

### Option 2: Manual startup

```bash
# Terminal 1 — Backend
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Terminal 2 — WhatsApp Bot
cd whatsapp-bot && node index.js
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/webhook/message` | Incoming WhatsApp message |
| GET | `/appointments/today` | Today's appointments |
| GET | `/appointments/date/{date}` | Appointments by date |
| POST | `/appointments/{id}/checkin` | Check in a patient |
| GET | `/slots/available/{date}` | Available slots |
| POST | `/slots/block` | Block a slot |
| GET | `/stats/no-shows` | No-show statistics |
| GET | `/export/appointments.xlsx` | Excel export |
| POST | `/integrations/google-sheets/sync` | Sync to Google Sheets |
| GET | `/admin/settings` | Get current settings |
| POST | `/admin/settings` | Update settings |
| GET | `/admin/models` | List Ollama models |

## Troubleshooting

- **"Model not found"** — Run `ollama pull <model_name>` first
- **QR code not appearing** — Delete `whatsapp-bot/auth_info` folder and restart
- **Voice notes not working** — Ensure `ffmpeg` is in your PATH
- **Google Sheets sync failing** — Check `GOOGLE_SERVICE_ACCOUNT_JSON` path and share the sheet with the service account email

## License

MIT
