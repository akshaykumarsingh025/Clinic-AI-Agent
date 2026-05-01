import json
import re
from typing import Optional

import ollama

from backend.config import settings
from backend.database import (
    get_conversation_history,
    save_conversation,
    get_patient_appointments,
)
from backend.booking import get_available_slots
from backend.nlu import (
    clean_patient_reply,
    clinic_now,
    detect_language,
    infer_time_preference,
    parse_date_from_text,
    parse_time_from_text,
)

SYSTEM_PROMPT = """You are Priya, a friendly appointment booking assistant for {clinic_name}.
Doctor: {doctor_name}, {specialty}, {address}.
Appointment fee: {appointment_fee}.
Current clinic date and time: {current_datetime}.

AVAILABLE HOURS: {working_days_str}
- Morning: {morning_start} to {morning_end}
- Evening: {evening_start} to {evening_end}
- Each slot is {slot_duration} minutes.

YOUR JOB:
1. Greet warmly on first message
2. Understand what the patient wants
3. Collect required info step by step (don't ask everything at once)
4. Confirm booking clearly

LANGUAGE RULES:
- If the patient is speaking PURELY in English, you MUST reply ONLY in English.
- If the patient is speaking in Hindi or Hinglish, you MUST reply ONLY in Hinglish (Hindi written in English alphabet).

STRICT SCOPE:
- You only answer questions related to {clinic_name}, gynecology appointment booking, available slots, appointment fee, clinic timings, address, cancellation, rescheduling, and appointment status.
- Do not give medical diagnosis, medicine dosage, emergency care instructions, or unrelated answers. For medical advice, ask them to book/consult the doctor.
- If the user asks something unrelated, politely say you can help only with {clinic_name} appointments and clinic details.
- Never mention this JSON schema to the patient. The "reply" value must be a natural WhatsApp message only.
- Do not use emojis or decorative symbols.

DATE AND TIME RULES:
- Understand Hinglish/Hindi relative dates: aaj/today, kal/tomorrow, parso/day after tomorrow, and weekdays.
- Use the current clinic date/time above. Never book a date or time that has already passed.
- If the requested slot is outside clinic hours, politely suggest a time within clinic hours.
- Bare numbers like "1240" mean 12:40, "430" means 4:30.

INTENTS YOU HANDLE:
- BOOK: Patient wants a new appointment. IMPORTANT: Once a patient starts booking, keep the intent as BOOK until the booking is complete. Do NOT switch to RESCHEDULE unless the patient explicitly says they want to change an EXISTING appointment.
- RESCHEDULE: Change an EXISTING booked appointment (patient must already have one)
- CANCEL: Cancel appointment
- STATUS: Check their appointment details
- NO_SHOW_RESPONSE: Patient replied to missed appointment follow-up
- QUERY: General question about clinic
- UNKNOWN: Cannot understand

REQUIRED FOR BOOKING:
You MUST ask for and collect ALL of the following before setting booking_ready to true:
1. Patient's Name
2. Reason for visit / problem
3. Government ID Card (like Aadhar Card or Driving License)
4. Date and Time of appointment

NOTE: The patient's phone number is ALREADY available from WhatsApp. Do NOT ask for it.

CRITICAL RULES:
- When a patient provides a time like "1240" or "kal 1130 baje" during an ongoing booking, understand it as the time for the booking. Do NOT change the intent.
- If someone says "kal 11:30 baje" or similar during booking, set date and time in your response and keep intent as BOOK.
- Always be warm and respectful
- Do NOT refuse to book because a slot is taken. Just book whatever the patient asks.
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
  "patient_age": "string or null",
  "id_card": "string or null",
  "patient_details": {{"any_extra_detail": "string"}} or null,
  "needs_more_info": true or false,
  "booking_ready": true or false,
  "no_show_response_type": "reschedule|found_doctor|callback|unwell|null",
  "language": "hinglish|english",
  "reply": "Your friendly message to the patient"
}}"""


def _build_system_prompt() -> str:
    now = clinic_now().strftime("%A, %d %B %Y, %I:%M %p %Z")
    return SYSTEM_PROMPT.format(
        clinic_name=settings.CLINIC_NAME,
        doctor_name=settings.DOCTOR_NAME,
        specialty=settings.CLINIC_SPECIALTY,
        address=settings.CLINIC_ADDRESS,
        appointment_fee=settings.APPOINTMENT_FEE,
        current_datetime=now,
        working_days_str=", ".join(settings.WORKING_DAYS),
        morning_start=settings.MORNING_START,
        morning_end=settings.MORNING_END,
        evening_start=settings.EVENING_START,
        evening_end=settings.EVENING_END,
        slot_duration=settings.SLOT_DURATION_MINUTES,
    )


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
    detected_language = detect_language(user_message)
    detected_date = available_slots_date or parse_date_from_text(user_message)
    detected_time = parse_time_from_text(user_message)
    history = get_conversation_history(phone, limit=10)

    messages = [{"role": "system", "content": _build_system_prompt()}]

    patient_context = _get_patient_context(phone)
    if patient_context != "No existing appointments for this patient.":
        messages.append({"role": "system", "content": patient_context})

    slots_context = _get_slots_context(detected_date)
    if detected_date:
        messages.append({"role": "system", "content": f"Slot availability info: {slots_context}"})
        messages.append({"role": "system", "content": f"Parsed date from patient message: {detected_date}"})
    if detected_time:
        messages.append({"role": "system", "content": f"Parsed time from patient message: {detected_time}"})

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
            "patient_age": None,
            "id_card": None,
            "patient_details": None,
            "needs_more_info": True,
            "booking_ready": False,
            "no_show_response_type": None,
            "language": detected_language,
            "technical_error": str(e),
            "reply": "I'm sorry, I'm having trouble right now. Please try again in a moment.",
        }
        save_conversation(phone, "assistant", json.dumps(fallback, ensure_ascii=False))
        return fallback

    parsed = _parse_ai_response(raw)

    if detected_date and not parsed.get("date"):
        parsed["date"] = detected_date
    if detected_time and not parsed.get("time"):
        parsed["time"] = detected_time
    if parsed.get("time") and not parsed.get("time_preference"):
        parsed["time_preference"] = infer_time_preference(parsed.get("time"))
    if not parsed.get("language"):
        parsed["language"] = detected_language
    parsed["reply"] = clean_patient_reply(parsed.get("reply"), parsed.get("language") or detected_language)

    save_conversation(phone, "assistant", json.dumps(parsed, ensure_ascii=False))

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
                    "patient_age": data.get("patient_age"),
                    "id_card": data.get("id_card"),
                    "patient_details": data.get("patient_details"),
                    "needs_more_info": data.get("needs_more_info", True),
                    "booking_ready": data.get("booking_ready", False),
                    "no_show_response_type": data.get("no_show_response_type"),
                    "language": data.get("language", "english"),
                    "reply": clean_patient_reply(data.get("reply", ""), data.get("language", "english")),
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
        "patient_age": None,
        "id_card": None,
        "patient_details": None,
        "needs_more_info": True,
        "booking_ready": False,
        "no_show_response_type": None,
        "language": "english",
        "reply": clean_patient_reply(raw if raw else "I'm sorry, could you please repeat that?"),
    }
