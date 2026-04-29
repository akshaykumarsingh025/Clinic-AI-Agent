import json
import re
from datetime import datetime, timedelta
from typing import Optional

import ollama

from backend.config import settings
from backend.database import (
    get_conversation_history,
    save_conversation,
    get_patient_appointments,
)
from backend.booking import get_available_slots, find_best_slot

SYSTEM_PROMPT = """You are Priya, a friendly appointment booking assistant for {clinic_name}.
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
{{
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
}}"""


def _build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(
        clinic_name=settings.CLINIC_NAME,
        doctor_name=settings.DOCTOR_NAME,
        specialty=settings.CLINIC_SPECIALTY,
        address=settings.CLINIC_ADDRESS,
    )


def parse_date_from_text(text: str) -> Optional[str]:
    text_lower = text.lower().strip()
    today = datetime.today()

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "mon": 0, "tue": 1, "wed": 2, "thu": 3,
        "fri": 4, "sat": 5, "sun": 6,
    }

    if text_lower in ("today", "aaj", "aj"):
        return today.strftime("%Y-%m-%d")
    if text_lower in ("tomorrow", "kal"):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if text_lower in ("day after tomorrow", "parso", "parsoh"):
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    for day_name, day_idx in day_map.items():
        if day_name in text_lower:
            days_ahead = (day_idx - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            if "next" in text_lower:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    date_match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-]?(\d{2,4})?", text_lower)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year_str = date_match.group(3)
        year = int(year_str) if year_str else today.year
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def _get_slots_context(date: Optional[str] = None) -> str:
    if not date:
        return "No specific date mentioned yet."

    slots = get_available_slots(date)
    if not slots:
        return f"No slots available on {date}. Suggest another date."

    morning = [s for s in slots if s < "13:00"]
    evening = [s for s in slots if s >= "13:00"]

    parts = []
    if morning:
        parts.append(f"Morning slots: {', '.join(morning)}")
    if evening:
        parts.append(f"Evening slots: {', '.join(evening)}")

    return f"Available slots on {date}: {'; '.join(parts)}"


def _get_patient_context(phone: str) -> str:
    appointments = get_patient_appointments(phone)
    active = [
        a for a in appointments
        if a["status"] in ("booked", "confirmed")
    ]

    if not active:
        return "No existing appointments for this patient."

    lines = ["Patient has these active appointments:"]
    for a in active:
        lines.append(f"  - {a['date']} at {a['time']} (status: {a['status']}, reason: {a.get('reason', 'N/A')})")
    return "\n".join(lines)


async def get_ai_response(phone: str, user_message: str, available_slots_date: Optional[str] = None) -> dict:
    history = get_conversation_history(phone, limit=10)

    messages = [{"role": "system", "content": _build_system_prompt()}]

    patient_context = _get_patient_context(phone)
    if patient_context != "No existing appointments for this patient.":
        messages.append({"role": "system", "content": patient_context})

    slots_context = _get_slots_context(available_slots_date)
    if available_slots_date:
        messages.append({"role": "system", "content": f"Slot availability info: {slots_context}"})

    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    save_conversation(phone, "user", user_message)

    try:
        client = ollama.Client(host=settings.OLLAMA_HOST)
        response = client.chat(
            model=settings.OLLAMA_MODEL,
            messages=messages,
            options={"temperature": 0.3, "num_predict": 512},
        )
        raw = response["message"]["content"].strip()
    except Exception as e:
        fallback = {
            "intent": "UNKNOWN",
            "patient_name": None,
            "date": None,
            "time": None,
            "time_preference": None,
            "reason": None,
            "needs_more_info": True,
            "booking_ready": False,
            "no_show_response_type": None,
            "language": "english",
            "reply": f"I'm sorry, I'm having trouble right now. Please try again in a moment. (Error: {str(e)[:50]})",
        }
        save_conversation(phone, "assistant", json.dumps(fallback))
        return fallback

    parsed = _parse_ai_response(raw)

    date_text = parsed.get("date") or parse_date_from_text(user_message)
    if date_text and not parsed.get("date"):
        parsed["date"] = date_text

    save_conversation(phone, "assistant", json.dumps(parsed))

    return parsed


def _parse_ai_response(raw: str) -> dict:
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if json_match:
        try:
            data = json.loads(json_match.group())
            required_keys = ["intent", "reply"]
            if all(k in data for k in required_keys):
                return {
                    "intent": data.get("intent", "UNKNOWN"),
                    "patient_name": data.get("patient_name"),
                    "date": data.get("date"),
                    "time": data.get("time"),
                    "time_preference": data.get("time_preference"),
                    "reason": data.get("reason"),
                    "needs_more_info": data.get("needs_more_info", True),
                    "booking_ready": data.get("booking_ready", False),
                    "no_show_response_type": data.get("no_show_response_type"),
                    "language": data.get("language", "english"),
                    "reply": data.get("reply", ""),
                }
        except json.JSONDecodeError:
            pass

    return {
        "intent": "UNKNOWN",
        "patient_name": None,
        "date": None,
        "time": None,
        "time_preference": None,
        "reason": None,
        "needs_more_info": True,
        "booking_ready": False,
        "no_show_response_type": None,
        "language": "english",
        "reply": raw if raw else "I'm sorry, could you please repeat that?",
    }
