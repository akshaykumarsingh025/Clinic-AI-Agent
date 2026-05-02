# Implementation Plan v2 — Clinic AI Agent

## Overview

This plan covers all audit fixes, user-requested features, and quality improvements.
Changes are ordered so each step builds on the last without breaking the system.

---

## Phase 1: Bug Fixes (Critical)

### 1.1 Fix race condition in `main.py`
**File:** `backend/main.py` (lines 98-103)
**Problem:** `async with user_locks[phone]` only covers one line — the rest of the webhook handler runs unlocked. Two messages from the same user can produce duplicate bookings.
**Fix:** Move the entire handler body inside the lock block.

### 1.2 Fix clinic hours — single 9 AM to 9 PM window
**Files:** `.env`, `backend/config.py`, `backend/agent.py`
**Problem:** Current `.env` has split morning/evening sessions that don't match reality. User wants 9 AM to 9 PM daily, no split.
**Fix:**
- Change `.env`: `MORNING_START=09:00`, `MORNING_END=21:00`, remove/ignore evening fields
- Update `booking.py` `generate_all_slots()` to support single continuous window
- Update system prompt to say "9:00 AM to 9:00 PM"

### 1.3 Background Google Sheets sync
**File:** `backend/main.py` (line 183-188)
**Problem:** `sync_appointments_to_google_sheet()` blocks the webhook for 2-5 seconds while the patient waits.
**Fix:** Run in a background thread via `asyncio.to_thread()`.

---

## Phase 2: Duplicate Detection and Returning Patients

### 2.1 Duplicate appointment detection
**File:** `backend/booking.py`
**Logic:**
- Before creating a new appointment, check if same phone + same date already has an active appointment
- If yes, return a flag to the webhook handler: `"duplicate_found": True`
- The webhook replies: "Aapka appointment 2 May ko 11:00 baje already booked hai. Kya aap reschedule karna chahenge ya naya appointment book karna hai?"
- If same phone + same date + same time: definitely a duplicate, don't create

### 2.2 Returning patient awareness
**File:** `backend/main.py`, `backend/agent.py`
**Logic:**
- When processing BOOK intent, check `get_patient_appointments(phone)` for active bookings
- Inject into the AI context: "This patient already has an active appointment on X at Y"
- The AI can then say "Welcome back Rekha! I see you already have an appointment on May 5 at 11 AM. Would you like to book another one, or reschedule the existing one?"
- Check patient record for name match — if phone already has a name, don't ask again

### 2.3 Smart patient identity reuse
**File:** `backend/main.py`, `backend/agent.py`
**Logic:**
- On first message, look up `get_patient(phone)` 
- If patient exists with name, ID card, etc. — inject into AI context: "Returning patient: Rekha Singh, ID: 9876865487"
- The AI skips asking for name, ID again and jumps straight to: "Hi Rekha! Kaise help kar sakti hoon aaj?"
- Only ask for missing fields

---

## Phase 3: Natural Chat Personality

### 3.1 Rewrite system prompt for warmth
**File:** `backend/agent.py`
**Changes to SYSTEM_PROMPT:**
- Remove robotic step-by-step feel
- Add personality traits: warm, slightly chatty, uses "ji" in Hinglish, empathetic
- Add examples of natural responses
- Key personality rules:
  - Use "ji" suffix in Hinglish ("Rekha ji", "bilkul")
  - Short, WhatsApp-style messages (not paragraphs)
  - Don't repeat info the patient already gave
  - If patient gives date+time in first message, don't ask again
  - Acknowledge emotions ("samajh sakti hoon", "koi baat nahi")

### 3.2 Remove unnecessary data collection
**File:** `backend/agent.py`
- Phone: Already from WhatsApp — never ask
- Name: If returning patient — skip
- ID Card: Ask only once, store in patient record
- Only ask for: reason + date/time (if not already provided)

---

## Phase 4: UI Improvements

### 4.1 Ollama model dropdown
**File:** `clinic_control.py`
**Current:** Text entry for OLLAMA_MODEL
**Fix:**
- Replace `_setting_row(clinic, 3, "OLLAMA_MODEL", ...)` with a Combobox
- On Settings tab load, call `http://localhost:11434/api/tags` to get model list
- Populate Combobox with model names
- Add a "Refresh Models" button next to dropdown
- Fallback: if Ollama unreachable, show current value as only option

---

## Phase 5: Code Quality and Reliability

### 5.1 Database context managers
**File:** `backend/database.py`
**Fix:** Wrap all DB functions with try/finally or context manager to prevent connection leaks on exceptions.

### 5.2 Conversation cleanup after booking
**File:** `backend/main.py`
**Fix:** After successful booking/cancellation/reschedule, call `clear_old_conversations(phone)` to prevent context pollution on next interaction.

### 5.3 Fee in appointment confirmation
**File:** `backend/booking.py`
**Fix:** Add appointment fee from `settings.APPOINTMENT_FEE` to the confirmation message.

### 5.4 Pin dependency versions
**File:** `requirements.txt`
**Fix:** Run `pip freeze` and pin exact versions for all dependencies.

---

## Phase 6: Documentation and Safety

### 6.1 Create `.env.example`
All keys with placeholder values and comments explaining each.

### 6.2 Create `README.md`
Sections: Features, Architecture diagram (text), Prerequisites, Installation, Configuration, Running, API Endpoints, Troubleshooting.

---

## File Change Summary

| File | Changes |
|---|---|
| `backend/main.py` | Fix lock, duplicate detection, returning patient context, background sync, conversation cleanup |
| `backend/agent.py` | Rewrite prompt for natural personality, patient context injection, skip known fields |
| `backend/booking.py` | Duplicate check, single 9-9 window, fee in confirmation |
| `backend/database.py` | Context managers, helper for active appointment lookup |
| `backend/config.py` | Simplify to single booking window |
| `clinic_control.py` | Model dropdown with Ollama API |
| `.env` | Update hours to 09:00-21:00 |
| `requirements.txt` | Pin versions |
| `.env.example` | New file |
| `README.md` | New file |

---

## Implementation Order

```
Phase 1 (bugs) -> Phase 2 (logic) -> Phase 3 (chat) -> Phase 4 (UI) -> Phase 5 (quality) -> Phase 6 (docs)
```

Each phase is independently testable. After each phase the system should still work correctly.
