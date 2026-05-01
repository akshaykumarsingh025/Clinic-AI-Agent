# 🔧 Clinic AI Agent - Fixes & Improvements Plan

## Issues to Fix First

### 1. Voice Note Error: "Numpy is not available"
**Problem:** Whisper STT fails with `Numpy is not available` error
**Solution:**
- Add `numpy` to requirements.txt
- Ensure numpy is imported before loading whisper model
- Add better error handling in stt.py

### 2. Hindi/English/Hinglish Language Support
**Problem:** AI might not properly understand Hindi or Hinglish
**Solution:**
- Update system prompt to prioritize Hindi/Hinglish detection
- Add language detection in agent.py and pass to Whisper for better transcription
- Update AI prompt to respond in the same language the patient used

### 3. Clinic Timing Settings in UI
**Problem:** Appointment timing is decided by AI, not configurable
**Solution:**
- Add Settings panel in admin.html with:
  - Morning start/end time
  - Evening start/end time
  - Working days selection
  - Slot duration (default 20 min)
- Save settings to config or database
- Make config.py read from .env OR database

### 4. AI Model Selection from Ollama
**Problem:** No way to select which Ollama model to use
**Solution:**
- Add Settings panel with dropdown to list available Ollama models
- Call `ollama list` to get available models
- Store selected model in .env or database
- Dynamic model selection at runtime

---

## Improvements to Implement

### 5. WhatsApp Buttons Fallback
**Problem:** Meta blocks interactive buttons from unofficial APIs
**Solution:**
- Change sendButtons() in sender.js to send plain text menu:
  ```
  Please reply with:
  1. Reschedule
  2. Found doctor
  3. Call back later
  4. Unwell - need help
  ```
- Keep buttons as backup (some devices still support them)

### 6. Audio Cache Cleanup Job
**Problem:** audio_cache folder grows indefinitely
**Solution:**
- Add cleanup job in scheduler.py to delete .wav/.ogg files older than 7 days
- Run daily at 3 AM

### 7. Scheduler Persistence (APScheduler SQLiteJobStore)
**Problem:** Jobs lost on server restart
**Solution:**
- Configure APScheduler with SQLiteJobStore
- Store jobs in database/apscheduler.db

### 8. SQLite WAL Mode (Already Implemented ✓)
**Status:** Database.py already has `PRAGMA journal_mode=WAL`
**Verified:** No action needed

### 9. Message Queue / Rate Limiter
**Problem:** Multiple rapid messages cause confusion
**Solution:**
- Add per-user lock in main.py
- If user has active conversation, queue or skip new messages
- Add 10-second cooldown between responses per user

---

## Architecture Questions

### 10. Voice Files Location
**Question:** "Is the voice copied here or connected to that project?"
**Answer:** The voice files are stored locally in this project:
- TTS output: `audio_cache/` directory
- STT input: `audio_cache/incoming/` directory
- NOT connected to any other project - completely local storage

---

## Implementation Order

```
Phase 1: Critical Fixes (Before Testing)
├── 1. Fix numpy error for voice notes
├── 2. Add Hindi/Hinglish support
└── 3. Fix WhatsApp buttons fallback

Phase 2: Admin UI Enhancements
├── 4. Add Settings page with clinic timings
├── 5. Add Ollama model selector dropdown
└── 6. Make settings persist to .env

Phase 3: Stability Improvements
├── 7. Audio cache cleanup job
├── 8. APScheduler persistence
└── 9. Message rate limiter

Phase 4: Final Testing
└── Test all flows end-to-end
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `requirements.txt` | Add numpy |
| `backend/stt.py` | Fix numpy import, add language detection |
| `backend/agent.py` | Add Hindi/Hinglish system prompt |
| `backend/config.py` | Add dynamic config loading |
| `backend/scheduler.py` | Add cleanup job, SQLiteJobStore |
| `whatsapp-bot/sender.js` | Change buttons to text menu |
| `backend/admin.html` | Add Settings panel with timings + model selector |
| `backend/main.py` | Add rate limiter per user |

---

## Quick Wins (Same Day)

1. **numpy fix** - Add to requirements.txt
2. **Hindi prompt** - Update system prompt in agent.py
3. **Buttons fallback** - Change sender.js to plain text
4. **Audio cleanup** - Add cron job in scheduler.py