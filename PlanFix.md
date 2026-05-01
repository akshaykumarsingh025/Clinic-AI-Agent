# PlanFix.md: Clinic Booking Agent System Improvements & Fixes

This document outlines the planned fixes and enhancements for the Automatic Appointment Booking System.

## 1. UI & Configuration Enhancements
*   [x] **Dynamic Clinic Timings:** Added a Settings tab in `admin.html` allowing the clinic staff to dynamically set morning/evening hours and working days. Implemented `update_setting` in `config.py` to write these directly to `.env`.
*   [x] **AI Model Selection Dropdown:** `admin.html` now fetches available models dynamically from the Ollama API (`/api/tags`) and allows selecting the active model, updating `.env` instantly.
*   [ ] **Admin Address & Location:** Add a text area in the Admin UI to input the clinic's exact map location/address to send after booking.

## 2. Voice & Multilingual Handling
*   [x] **Voice Note Processing Error:** Fixed the audio processing error. Removed the manual, redundant `ffmpeg` conversion in `stt.py` because `whisper` handles OGG/OPUS files natively via its own internal ffmpeg hooks. This avoids subset format incompatibility crashes.
*   [ ] **Local TTS Integration:** Migrate the necessary Text-To-Speech (TTS) engine code (Piper or XTTS) directly into this project instead of just bridging to the external `VoiceCloneReels` project.
*   [x] **Strict Language Reply Enforcement:** Updated `nlu.py` to enforce strict language tags (`english` and `hinglish`). Removed the pure `hindi` script output, ensuring all Hindi/Hinglish inputs are met with Hinglish Latin script replies as per the system prompt.
*   [ ] **Current Date/Time Awareness:** Refine the agent's logic to strictly parse relative terms like "kal", "parso", and "aaj".

## 3. Booking Workflow Refinements
*   [x] **Comprehensive Patient Details:** Updated `agent.py` to enforce the collection of Name, Phone Number, Reason, and Government ID (Aadhar/Driving License) before confirmation.
*   [x] **WhatsApp Buttons Fallback:** Replaced interactive `sendButtons` with a plain text numbered menu in `scheduler.py` to bypass Meta's blocks on unofficial API buttons.

## 4. System Robustness & Maintenance
*   [x] **Message Queuing / Rate Limiting:** Introduced `user_locks = defaultdict(asyncio.Lock)` in `main.py` per phone number. This prevents identical parallel LLM queries if a user sends 5 rapid messages.
*   [x] **Concurrent Users Handling:** FastAPI handles concurrent requests natively, and the async lock guarantees safe state for each individual user's conversation.
*   [x] **Scheduler Persistence (SQLiteJobStore):** Updated `scheduler.py` to use a `SQLAlchemyJobStore`.
*   [x] **Audio Cache Cleanup Job:** Added automated cleanup in `scheduler.py` to delete old `.wav` and `.ogg` files.
*   [x] **SQLite Concurrency Verification:** `PRAGMA journal_mode=WAL` is active.

## 5. Other Improvements (Identified during review)
*   [x] **Environment Variable Hot-Reloading:** Modified `Settings` class to update properties dynamically and update `.env` via `set_key` without server restart.
*   [ ] **Error Handling in Webhooks:** Improve the `whatsapp-bot/index.js` error handling to gracefully inform the user if the backend is temporarily offline, rather than silently failing.
