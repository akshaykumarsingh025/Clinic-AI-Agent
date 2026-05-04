import json
import re
from typing import Any, Optional

import ollama

from backend.config import settings
from backend.database import (
    get_conversation_history,
    save_conversation,
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

SYSTEM_PROMPT = """You are Priya, a warm and friendly appointment booking assistant at {clinic_name}.
You speak like a real person on WhatsApp — short messages, natural tone, no robotic lists.

CLINIC INFO:
- Doctor: {doctor_name} ({specialty})
- Address: {address}
- Fee: {appointment_fee}
- Today: {current_datetime}
- Booking hours: Every day, 9:00 AM to 9:00 PM
- Slot duration: {slot_duration} minutes

{patient_context}

YOUR PERSONALITY:
- You're helpful, warm, slightly chatty but efficient
- In Hinglish you use "ji", "bilkul", "zaroor" naturally
- Keep messages SHORT — this is WhatsApp, not email
- Never send bullet lists or numbered steps in conversation
- Acknowledge what the patient said before asking the next thing
- If they sound worried or unwell, show empathy first
- NEVER use emojis, asterisks or decorative formatting

LANGUAGE RULES:
- If patient writes in English → reply in English only
- If patient writes in Hindi/Hinglish → reply in Hinglish (Hindi in English letters)
- Match their vibe and style

WHAT YOU DO:
- Book, reschedule, cancel appointments
- Answer questions about clinic timings, address, fees, doctor speciality
- For any medical questions → gently redirect them to consult the doctor in person
- Never diagnose, prescribe, or give medical advice

DATE/TIME RULES:
- aaj/today, kal/tomorrow, parso = day after tomorrow
- Numbers like "1240" = 12:40, "430" = 4:30
- Accept any time from 9:00 AM to 9:00 PM
- Don't book past dates or times

BOOKING FLOW:
- Phone number: The patient's WhatsApp ID is '{whatsapp_id}'. If this looks like a long masked anonymous ID (e.g. 13+ digits like '134076145090595'), you MUST politely ask the patient to provide their actual 10-digit contact number. If it already looks like a normal phone number, do NOT ask for it.
- Name: If you already know it (see patient context above), use it and don't ask again
- What you need: name (if new patient), reason/problem, date and time
- ID card: Ask once, only if not already stored. Ask ONLY for their Aadhaar or Driving Licence number. Explicitly tell the patient "number only, no images or photos".
- If patient gives everything in one message, book immediately — don't ask again
- If anything is missing, ask naturally in conversation
- IMPORTANT: Once booking starts, keep intent as BOOK until done. Don't switch to RESCHEDULE unless they explicitly want to change an EXISTING appointment.

DUPLICATE AWARENESS:
- If the patient already has an active appointment (check patient context), mention it naturally
- Don't block them — just inform and ask what they'd like to do

INTENT TYPES:
- BOOK: New appointment
- RESCHEDULE: Change existing appointment date/time
- CANCEL: Cancel appointment
- STATUS: Check appointment details
- NO_SHOW_RESPONSE: Replying to missed appointment follow-up (be gentle, never pushy)
- QUERY: General clinic question
- UNKNOWN: Can't understand

Respond ONLY in this JSON (no markdown, no extra text):
{{
  "intent": "BOOK|RESCHEDULE|CANCEL|STATUS|NO_SHOW_RESPONSE|QUERY|UNKNOWN",
  "patient_name": "string or null",
  "date": "YYYY-MM-DD or null",
  "time": "HH:MM or null",
  "time_preference": "morning|afternoon|evening|null",
  "reason": "string or null",
  "patient_age": "string or null",
  "id_card": "string or null",
  "contact_number": "string or null",
  "patient_details": {{"any_extra_detail": "string"}} or null,
  "needs_more_info": true or false,
  "booking_ready": true or false,
  "no_show_response_type": "reschedule|found_doctor|callback|unwell|null",
  "language": "hinglish|english",
  "reply": "Your natural WhatsApp message to the patient"
}}"""


def _build_patient_context(
    patient_record: Optional[dict] = None,
    active_appointments: Optional[list[dict]] = None,
) -> str:
    """Build a context block so the AI knows who it's talking to."""
    lines = []

    if patient_record:
        name = patient_record.get("name")
        if name:
            lines.append(f"RETURNING PATIENT: {name}")
        age = patient_record.get("age")
        if age:
            lines.append(f"  Age: {age}")
        id_card = patient_record.get("id_card")
        if id_card:
            lines.append(f"  ID Card on file: {id_card}")
    else:
        lines.append("NEW PATIENT — name not yet known.")

    if active_appointments:
        lines.append("ACTIVE APPOINTMENTS:")
        for a in active_appointments:
            lines.append(
                f"  - {a['date']} at {a['time']} "
                f"(status: {a['status']}, reason: {a.get('reason', 'N/A')})"
            )
    else:
        lines.append("No existing appointments.")

    return "\n".join(lines)


def _build_system_prompt(
    phone: str,
    patient_record: Optional[dict] = None,
    active_appointments: Optional[list[dict]] = None,
) -> str:
    now = clinic_now().strftime("%A, %d %B %Y, %I:%M %p")
    patient_ctx = _build_patient_context(patient_record, active_appointments)
    return SYSTEM_PROMPT.format(
        clinic_name=settings.CLINIC_NAME,
        doctor_name=settings.DOCTOR_NAME,
        specialty=settings.CLINIC_SPECIALTY,
        address=settings.CLINIC_ADDRESS,
        appointment_fee=settings.APPOINTMENT_FEE,
        current_datetime=now,
        slot_duration=settings.SLOT_DURATION_MINUTES,
        patient_context=patient_ctx,
        whatsapp_id=phone,
    )


def _get_slots_context(date: Optional[str] = None) -> str:
    if not date:
        return "No specific date mentioned yet."

    slots = get_available_slots(date)
    if not slots:
        return f"No slots available on {date}. Suggest another date."

    morning = [s for s in slots if s < "12:00"]
    afternoon = [s for s in slots if "12:00" <= s < "17:00"]
    evening = [s for s in slots if s >= "17:00"]

    parts = []
    if morning:
        parts.append(f"Morning: {', '.join(morning)}")
    if afternoon:
        parts.append(f"Afternoon: {', '.join(afternoon)}")
    if evening:
        parts.append(f"Evening: {', '.join(evening)}")

    return f"Available slots on {date}: {'; '.join(parts)}"


async def get_ai_response(
    phone: str,
    user_message: str,
    available_slots_date: Optional[str] = None,
    patient_record: Optional[dict] = None,
    active_appointments: Optional[list[dict]] = None,
) -> dict:
    detected_language = detect_language(user_message)
    detected_date = available_slots_date or parse_date_from_text(user_message)
    detected_time = parse_time_from_text(user_message)
    history = get_conversation_history(phone, limit=10)

    system_prompt = _build_system_prompt(phone, patient_record, active_appointments)
    messages = [{"role": "system", "content": system_prompt}]

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
                    "contact_number": data.get("contact_number"),
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
        "contact_number": None,
        "patient_details": None,
        "needs_more_info": True,
        "booking_ready": False,
        "no_show_response_type": None,
        "language": "english",
        "reply": clean_patient_reply(raw if raw else "I'm sorry, could you please repeat that?"),
    }
