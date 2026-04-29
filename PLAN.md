# 🏥 AI Appointment Booking Agent — Full Project Plan
### For Dr. Deepika Singh's Clinic | South Delhi | Gynecologist

> **Stack:** Python + FastAPI + Gemma (Ollama) + Whisper + Piper TTS + Baileys (WhatsApp) + SQLite + APScheduler  
> **Cost:** ~₹0/month (WhatsApp-first) | ~₹500/month (with calls)  
> **Target:** Fully working booking agent with no-show follow-up system

---

## 📁 Final Project Structure

```
clinic-agent/
│
├── backend/                        # FastAPI Python server
│   ├── main.py                     # App entry point, routes
│   ├── agent.py                    # Gemma AI brain (intent + reply)
│   ├── booking.py                  # Appointment CRUD logic
│   ├── scheduler.py                # No-show + reminder jobs
│   ├── tts.py                      # Piper TTS voice generation
│   ├── stt.py                      # Whisper transcription
│   ├── whatsapp_sender.py          # Send messages via Baileys API
│   ├── database.py                 # SQLite setup + queries
│   ├── models.py                   # Pydantic models
│   └── config.py                   # Settings, clinic config
│
├── whatsapp-bot/                   # Node.js Baileys WhatsApp client
│   ├── index.js                    # Main bot, QR scan + listener
│   ├── sender.js                   # Send text / audio / buttons
│   ├── downloader.js               # Download incoming voice notes
│   └── package.json
│
├── voices/                         # Piper TTS voice models
│   ├── en_IN-female.onnx           # Indian English female voice
│   └── en_IN-female.onnx.json
│
├── database/
│   └── clinic.db                   # SQLite database (auto created)
│
├── audio_cache/                    # Cached TTS audio files
│
├── logs/                           # App logs
│
├── .env                            # Config (clinic name, phone, etc.)
├── requirements.txt
├── package.json
├── start.sh                        # One command to start everything
└── README.md
```

---

## 🗄️ Phase 0 — Environment Setup

### 0.1 Prerequisites
```bash
# Python 3.11+
python --version

# Node.js 18+
node --version

# Ollama (you have this)
ollama --version

# Pull Gemma model
ollama pull gemma3:4b        # ~3GB, fast responses
# OR if you have more RAM
ollama pull gemma3:12b       # Better understanding
```

### 0.2 Python Dependencies
```
# requirements.txt
fastapi
uvicorn
openai-whisper
ollama
apscheduler
python-dotenv
pydantic
httpx
aiofiles
ffmpeg-python
```

```bash
pip install -r requirements.txt

# Also install ffmpeg (needed by Whisper)
sudo apt install ffmpeg        # Linux
brew install ffmpeg            # Mac
```

### 0.3 Piper TTS Setup
```bash
# Download Piper binary
wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_x86_64.tar.gz
tar -xzf piper_linux_x86_64.tar.gz
sudo mv piper /usr/local/bin/

# Download Indian English voice
mkdir voices
cd voices
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_IN/female/medium/en_IN-female-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_IN/female/medium/en_IN-female-medium.onnx.json
```

### 0.4 Node Dependencies (WhatsApp Bot)
```bash
cd whatsapp-bot
npm init -y
npm install @whiskeysockets/baileys axios form-data
```

### 0.5 Environment Config
```env
# .env
CLINIC_NAME=Dr. Deepika Singh Clinic
DOCTOR_NAME=Dr. Deepika Singh
CLINIC_SPECIALTY=Gynecologist
CLINIC_ADDRESS=South Delhi (exact address)
CLINIC_PHONE=+91XXXXXXXXXX

# Slot config
SLOT_DURATION_MINUTES=20
MORNING_START=10:00
MORNING_END=13:00
EVENING_START=17:00
EVENING_END=20:00
WORKING_DAYS=Mon,Tue,Wed,Thu,Fri,Sat

# Ollama
OLLAMA_MODEL=gemma3:4b
OLLAMA_HOST=http://localhost:11434

# Paths
PIPER_BINARY=/usr/local/bin/piper
PIPER_VOICE=./voices/en_IN-female-medium.onnx
AUDIO_CACHE_DIR=./audio_cache

# Baileys bot
WHATSAPP_BOT_URL=http://localhost:3001
```

---

## 🗃️ Phase 1 — Database Layer

### File: `backend/database.py`

**Tables to create:**

```sql
-- Patients table
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Appointments table
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id),
    phone TEXT NOT NULL,
    patient_name TEXT NOT NULL,
    date TEXT NOT NULL,            -- YYYY-MM-DD
    time TEXT NOT NULL,            -- HH:MM
    reason TEXT,
    status TEXT DEFAULT 'booked', -- booked | confirmed | checked_in | no_show | cancelled | rescheduled
    reminder_sent INTEGER DEFAULT 0,
    followup_sent INTEGER DEFAULT 0,
    followup_response TEXT,        -- reschedule | found_doctor | callback | unwell
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Conversation history table (for Gemma context)
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    role TEXT NOT NULL,            -- user | assistant
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Blocked slots (manual blocks by clinic)
CREATE TABLE IF NOT EXISTS blocked_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time TEXT,                     -- NULL = full day block
    reason TEXT
);
```

**Functions to implement:**
- `get_db()` — SQLite connection
- `init_db()` — Create all tables
- `get_patient(phone)` — Fetch or create patient
- `get_appointments(date)` — All bookings for a day
- `get_available_slots(date)` — Compare booked vs all slots
- `create_appointment(phone, name, date, time, reason)` — Book slot
- `update_appointment_status(id, status)` — Update status
- `get_upcoming_appointments()` — For reminder scheduler
- `get_past_unconfirmed(minutes_ago)` — For no-show detection
- `save_conversation(phone, role, content)` — Store chat history
- `get_conversation_history(phone, limit=10)` — Last N messages
- `clear_old_conversations(phone)` — After booking complete

---

## 🤖 Phase 2 — AI Brain (Gemma Agent)

### File: `backend/agent.py`

**The system prompt (most important part):**
```python
SYSTEM_PROMPT = """
You are Priya, a friendly appointment booking assistant for {clinic_name}.
Doctor: {doctor_name}, {specialty}, {address}.

AVAILABLE HOURS: Monday to Saturday
- Morning: 10:00 AM to 1:00 PM
- Evening: 5:00 PM to 8:00 PM
- Each slot is 20 minutes. Sundays closed.

YOUR JOB:
1. Greet warmly on first message
2. Understand what the patient wants
3. Collect required info step by step (don't ask everything at once)
4. Confirm booking clearly
5. Handle Hindi and English both naturally

INTENTS YOU HANDLE:
- BOOK: Patient wants a new appointment
- RESCHEDULE: Change existing appointment
- CANCEL: Cancel appointment
- STATUS: Check their appointment details
- NO_SHOW_RESPONSE: Patient replied to missed appointment follow-up
- QUERY: General question about clinic
- UNKNOWN: Cannot understand

REQUIRED FOR BOOKING: name, date, time (or preference like morning/evening)
OPTIONAL: reason for visit

RULES:
- Always be warm and respectful
- If slot is taken, suggest nearest available
- Confirm all details before finalising
- After booking, summarise clearly with date, time, doctor name
- For NO_SHOW_RESPONSE, be empathetic, never pushy

Respond ONLY in this JSON format (no extra text, no markdown):
{
  "intent": "BOOK|RESCHEDULE|CANCEL|STATUS|NO_SHOW_RESPONSE|QUERY|UNKNOWN|INCOMPLETE",
  "patient_name": "string or null",
  "date": "YYYY-MM-DD or null",
  "time": "HH:MM or null",
  "time_preference": "morning|evening|null",
  "reason": "string or null",
  "needs_more_info": true or false,
  "booking_ready": true or false,
  "no_show_response_type": "reschedule|found_doctor|callback|unwell|null",
  "language": "hindi|english|hinglish",
  "reply": "Your friendly message to the patient"
}
"""
```

**Functions to implement:**
- `async get_ai_response(phone, user_message, available_slots)` — Main function
  - Load conversation history from DB
  - Build context with available slots injected
  - Call Gemma via Ollama
  - Parse JSON response
  - Save to conversation history
  - Return parsed result
- `parse_date_from_text(text)` — Handle "Monday", "kal", "next week" etc
- `get_slots_context(date)` — Format available slots as text for prompt

---

## 📅 Phase 3 — Booking Engine

### File: `backend/booking.py`

**Slot generation logic:**
```python
def generate_all_slots(date: str) -> list[str]:
    """Generate all 20-min slots for a given date"""
    # Returns: ["10:00", "10:20", "10:40", ..., "12:40", "17:00", ..., "19:40"]

def get_available_slots(date: str) -> list[str]:
    """All slots minus booked minus blocked"""
    all_slots = generate_all_slots(date)
    booked = get_booked_slots(date)      # from DB
    blocked = get_blocked_slots(date)    # from DB
    return [s for s in all_slots if s not in booked and s not in blocked]

def find_best_slot(date: str, preference: str) -> str | None:
    """Given morning/evening preference, return first available slot"""

def find_next_available_date(from_date: str, preference: str) -> tuple[str, str]:
    """If a date is full, find next available date + slot"""
```

**Functions to implement:**
- `book_appointment(phone, name, date, time, reason)` → appointment_id
- `cancel_appointment(phone, date)` → bool
- `reschedule_appointment(appt_id, new_date, new_time)` → bool
- `get_patient_appointments(phone)` → list
- `check_slot_conflict(date, time)` → bool
- `format_appointment_confirmation(appt)` → nicely formatted string

---

## 🎙️ Phase 4 — Voice Processing

### File: `backend/stt.py` — Speech to Text

```python
import whisper

model = whisper.load_model("small")   # ~500MB, good for Indian accents
# Options: tiny (fast) | small (balanced) | medium (accurate) | large (best)

async def transcribe_audio(audio_path: str) -> str:
    """
    Convert voice note to text
    - Handles WhatsApp .ogg / .mp4 audio formats
    - Detects language automatically (Hindi + English)
    - Returns transcribed text
    """
    result = model.transcribe(
        audio_path,
        language=None,          # Auto-detect Hindi/English
        task="transcribe"
    )
    return result["text"].strip()

async def convert_audio_format(input_path: str) -> str:
    """Convert WhatsApp ogg/opus to wav for Whisper using ffmpeg"""
```

### File: `backend/tts.py` — Text to Speech

```python
import subprocess, hashlib, os

async def generate_voice_reply(text: str) -> str:
    """
    Convert text to voice note using Piper TTS
    - Checks cache first (same text = same file)
    - Returns path to .wav file
    - File is sent back to patient as WhatsApp voice note
    """
    cache_key = hashlib.md5(text.encode()).hexdigest()
    output_path = f"./audio_cache/{cache_key}.wav"
    
    if os.path.exists(output_path):
        return output_path   # Serve from cache
    
    subprocess.run([
        "piper",
        "--model", PIPER_VOICE,
        "--output_file", output_path
    ], input=text.encode(), check=True)
    
    return output_path
```

---

## ⏰ Phase 5 — Scheduler (Reminders + No-Show)

### File: `backend/scheduler.py`

**Three types of scheduled jobs:**

#### Job 1 — Appointment Reminder (24 hours before)
```
Trigger: 24 hours before each appointment
Action:
  → Send WhatsApp text reminder
  → Send voice note reminder (Piper TTS)
  
Message:
"Hi [Name]! 👋 Reminder for your appointment tomorrow at [Time] 
with Dr. Deepika. Reply CONFIRM to confirm or CANCEL to cancel. 🏥"
```

#### Job 2 — Same-Day Reminder (2 hours before)
```
Trigger: 2 hours before appointment
Action:
  → Send final WhatsApp reminder text only
  
Message:
"Hi [Name], your appointment is in 2 hours at [Time]. 
Clinic address: [Address]. See you soon! 🙏"
```

#### Job 3 — No-Show Detection (45 min after appointment)
```
Trigger: 45 minutes after appointment time
Condition: Status is still 'booked' (not 'checked_in')
Action:
  → Mark status as 'no_show'
  → Send WhatsApp follow-up with options
  → If no reply in 3 hours → send voice note follow-up
  
Message:
"Hi [Name] 👋, we noticed you missed your appointment with 
Dr. Deepika today at [Time]. Hope you're okay!

Please let us know:
1️⃣ I'd like to reschedule
2️⃣ I consulted another doctor
3️⃣ I'll call back to reschedule later
4️⃣ I'm unwell and need help

Just reply with 1, 2, 3, or 4 🙏"
```

#### Job 4 — No-Reply Voice Follow-Up (3 hours after no-show message)
```
Trigger: 3 hours after no-show WhatsApp sent, if no response
Action:
  → Generate voice note with Piper TTS
  → Send as WhatsApp voice note
  
Script:
"Hi [Name], this is Dr. Deepika's clinic. We missed you today.
We just want to make sure you're okay. Please reply to 
our WhatsApp message when you get a chance. Take care!"
```

**Functions to implement:**
- `init_scheduler()` — Start APScheduler, add recurring jobs
- `schedule_appointment_reminders(appointment)` — Add 24h + 2h jobs
- `schedule_no_show_check(appointment)` — Add 45-min post-appt job
- `schedule_voice_followup(phone, name)` — Add 3-hour delayed voice
- `run_daily_reminder_scan()` — Cron: every morning, schedule today's reminders
- `cancel_scheduled_jobs(appt_id)` — When appointment cancelled

---

## 💬 Phase 6 — FastAPI Backend

### File: `backend/main.py`

**Endpoints:**

```
POST /webhook/message
  ← Called by Baileys bot when new WhatsApp message arrives
  Body: { phone, message_text, audio_path (optional) }
  Flow:
    1. If audio → transcribe with Whisper
    2. Get AI response from Gemma
    3. If booking_ready → book_appointment()
    4. Generate voice reply with Piper TTS
    5. Return { text_reply, audio_path }

POST /webhook/button-reply  
  ← When patient presses 1/2/3/4 in no-show follow-up
  Body: { phone, button_number }
  Flow:
    1. Map button to response type
    2. If 1 (reschedule) → start rebooking flow
    3. If 2 (found doctor) → log, send kind farewell
    4. If 3 (callback) → log, send acknowledgement
    5. If 4 (unwell) → send clinic number + empathy

GET /appointments/today
  ← Dashboard: today's appointment list

GET /appointments/date/{date}
  ← Appointments for specific date

POST /appointments/{id}/checkin
  ← Mark patient as checked in (cancel no-show job)

POST /slots/block
  ← Block a date/time (doctor on leave etc)
  Body: { date, time (optional), reason }

GET /slots/available/{date}
  ← Get all available slots for a date

GET /stats/no-shows
  ← No-show analytics (count, reasons, dates)

GET /health
  ← Health check endpoint
```

### File: `backend/whatsapp_sender.py`

```python
# Calls Baileys bot API to send messages
async def send_text(phone: str, text: str)
async def send_voice_note(phone: str, audio_path: str)
async def send_button_message(phone: str, text: str, buttons: list[str])
```

---

## 📱 Phase 7 — WhatsApp Bot (Baileys)

### File: `whatsapp-bot/index.js`

**What it does:**
- Starts up, shows QR code in terminal
- Scan with clinic's WhatsApp number
- Listens for ALL incoming messages
- Routes to FastAPI backend
- Sends back replies (text + voice notes)
- Exposes local API for Python to call it

**Flow:**
```
Incoming message
    ↓
Is it a voice note?
    Yes → Download audio file → Send path to FastAPI
    No  → Send text to FastAPI
    ↓
FastAPI returns { text_reply, audio_path }
    ↓
Send text reply to patient
If audio_path → Send as voice note (PTT = true = plays as voice note)
```

**Expose local HTTP server** so Python can call it:
```
POST http://localhost:3001/send/text    — Send text message
POST http://localhost:3001/send/audio  — Send voice note
POST http://localhost:3001/send/buttons — Send message with buttons
```

---

## 🖥️ Phase 8 — Simple Admin Dashboard (Optional)

A minimal HTML+JS page served by FastAPI to let clinic staff:

- View today's appointments
- Mark patients as checked in
- Block dates/times (doctor leave)
- View no-show stats
- Manually trigger a follow-up message

**Tech:** FastAPI static files + plain HTML/CSS/JS (no framework needed)

**Pages:**
- `/admin` — Today's schedule
- `/admin/calendar` — Week view
- `/admin/no-shows` — No-show history + responses
- `/admin/block` — Block slots

---

## 🚀 Phase 9 — Startup & Deployment

### File: `start.sh`
```bash
#!/bin/bash
echo "🏥 Starting Clinic AI Agent..."

# Start Ollama (if not running)
ollama serve &

# Start FastAPI backend
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &

# Start WhatsApp bot
cd ../whatsapp-bot
node index.js &

echo "✅ All services running"
echo "📱 Scan the QR code above with clinic WhatsApp"
echo "🔗 Admin dashboard: http://localhost:8000/admin"
echo "🤖 API docs: http://localhost:8000/docs"
```

---

## 🔄 Complete Patient Flow (End to End)

```
1. BOOKING FLOW
───────────────
Patient: "Hello, I want to book appointment"
Priya (AI): "Hi! I'm Priya from Dr. Deepika's clinic 😊 
             May I know your name please?"

Patient: "My name is Sunita Sharma"  
Priya: "Hi Sunita ji! What date would you prefer? 
        We're open Mon–Sat, morning 10–1 or evening 5–8."

Patient: "Thursday evening"
Priya: "Thursday 1st May has slots at 5:00, 5:20, 5:40 PM. 
        Which works best for you?"

Patient: "5:20 please"
Priya: "✅ Done! Your appointment is confirmed:
        📅 Thursday, 1st May 2025
        ⏰ 5:20 PM
        👩‍⚕️ Dr. Deepika Singh
        📍 [Address]
        You'll get a reminder tomorrow. See you! 🙏"

[System saves appointment + schedules reminders + no-show check]

2. REMINDER FLOW (24h before)
──────────────────────────────
Priya: "Hi Sunita ji 👋 Reminder: You have an appointment 
        TOMORROW at 5:20 PM with Dr. Deepika.
        Reply CONFIRM to confirm ✅ or CANCEL to cancel ❌"

Patient: "CONFIRM"
Priya: "Thank you! See you tomorrow at 5:20 PM 🙏"

3. NO-SHOW FLOW (if patient doesn't come)
──────────────────────────────────────────
[45 min after 5:20 PM, system detects no check-in]

Priya: "Hi Sunita ji 👋, we noticed you missed your appointment 
        today at 5:20 PM with Dr. Deepika. Hope you're okay! 💙

        Please let us know:
        1️⃣ I'd like to reschedule
        2️⃣ I consulted another doctor  
        3️⃣ I'll call later to reschedule
        4️⃣ I'm unwell and need help"

Patient replies: "1"

Priya: "Of course Sunita ji! Let's find you a new slot.
        What date and time works for you?"

[Rebooking flow starts again]

4. IF NO REPLY (3 hours later)
────────────────────────────────
[Sends voice note]
"Hi Sunita ji, this is Dr. Deepika's clinic. We noticed you 
 missed your appointment. We hope you're doing well. 
 Please WhatsApp us when you're free to reschedule. 
 Take care! 🙏"
```

---

## 📊 Data Tracked (For Clinic Insights)

The system automatically logs:
- Total bookings per week/month
- No-show rate (%)
- No-show reasons breakdown (reschedule / found other doctor / unwell)
- Peak booking times
- Most common reasons for visits
- Response rate to follow-up messages

---

## 🧪 Testing Checklist

Before going live, test these manually via WhatsApp:

- [ ] New patient books appointment (English)
- [ ] New patient books in Hindi/Hinglish
- [ ] Patient sends voice note to book
- [ ] Patient books when morning slot is full → gets evening suggestion
- [ ] Patient books when full day is booked → gets next available date
- [ ] Patient cancels appointment
- [ ] Patient reschedules appointment
- [ ] 24-hour reminder is received
- [ ] 2-hour reminder is received
- [ ] No-show message sent correctly
- [ ] Patient responds to no-show (1, 2, 3, 4)
- [ ] Voice note follow-up sent after no reply
- [ ] Admin can block a date
- [ ] Admin can check in a patient

---

## 🔒 Important Notes

1. **WhatsApp ToS** — Baileys uses WhatsApp Web protocol (unofficial). 
   For production at scale, use Twilio/360dialog official API (~₹2000/month).
   For a small clinic with low volume, Baileys works fine.

2. **Data Privacy** — Patient names and phone numbers are stored locally 
   in SQLite. No data leaves your machine. HIPAA/DPDP compliant by design.

3. **Backup** — Add a daily SQLite backup script to Google Drive or 
   a local folder. Appointments are critical data.

4. **Whisper model size** — Use `small` model for good Hindi/English accuracy.
   If RAM is tight, use `tiny`. If accuracy is critical, use `medium`.

5. **Gemma model** — `gemma3:4b` is fast and good enough. If responses 
   feel off, switch to `gemma3:12b` for better understanding.

---

## 📋 Build Order (Recommended)

```
Week 1:
  [x] Phase 0 — Setup all tools
  [x] Phase 1 — Database layer
  [x] Phase 2 — Gemma AI agent (test in terminal first)
  [x] Phase 3 — Booking engine

Week 2:
  [x] Phase 4 — Whisper STT + Piper TTS
  [x] Phase 6 — FastAPI backend endpoints
  [x] Phase 7 — Baileys WhatsApp bot
  [x] Connect all pieces, test full booking flow

Week 3:
  [x] Phase 5 — Scheduler (reminders + no-show)
  [x] Test all scheduler flows
  [x] Phase 8 — Admin dashboard (optional)
  [x] Phase 9 — Startup script + go live
```

---

*Built for Dr. Deepika Singh's Clinic, South Delhi*  
*Stack: Python + FastAPI + Gemma (Ollama) + Whisper + Piper TTS + Baileys + SQLite*
