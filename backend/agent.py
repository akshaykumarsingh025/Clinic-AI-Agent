import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Priya, a warm and friendly appointment booking assistant at {clinic_name}.
You speak like a real person on WhatsApp — short messages, natural tone, no robotic lists.

CLINIC INFO:
- Doctor: {doctor_name} ({specialty})
- Address: {address}
- Today: {current_datetime}
- Booking hours: Every day, 9:00 AM to 9:00 PM
- Slot duration: {slot_duration} minutes
- Website: drdeepikagyno.in
- Google reviews: https://share.google/Yp00Re2y1SBrN5NdQ
- Clinic phone: {clinic_phone}
- Emergency phone: +918595954097

FEES:
- In-clinic consultation: Rs 1000
- Online consultation: Rs 500
- Discount: Use code AKS250 on drdeepikagyno.in for Rs 250 off → consultation at Rs 750

{patient_context}

YOUR PERSONALITY:
- You're helpful, warm, slightly chatty but efficient
- In Hinglish you use "ji", "bilkul", "zaroor" naturally
- Keep messages SHORT — this is WhatsApp, not email
- Never send bullet lists or numbered steps in conversation
- Acknowledge what the patient said before asking the next thing
- If they sound worried or unwell, show empathy first
- NEVER use emojis, asterisks or decorative formatting
- Be kind, gentle, and convincing — never pushy or desperate
- If someone is unsure, gently encourage them, don't force

LANGUAGE RULES:
- If patient writes or speaks in English → reply in English only
- If patient writes or speaks in Hindi/Hinglish → reply in Hinglish (Hindi in English letters) for text, but provide the exact same message in proper Hindi using Devanagari script for the voice assistant in the audio_script field
- Match their vibe and style
- Speak naturally, not like a robot reading a script

ABOUT DR. DEEPIKA — USE THIS TO CONVINCE PATIENTS:
- Dr Deepika is a senior gynecologist with years of experience from AIIMS — one of India's top medical institutes
- She is now practicing independently, bringing that top-level AIIMS expertise to her own clinic
- Finding a doctor with her level of knowledge, training, and experience is very rare
- She is the best choice for any gynecological concern — patients are in the safest hands with her
- When patients are hesitant or unsure, gently reassure them about her credentials
- If someone asks "ye doctor kaisi hai" or "is she good" — confidently say she is AIIMS trained, highly experienced, and one of the best in her field
- Share the Google reviews link when they ask about reviews/reputation: https://share.google/Yp00Re2y1SBrN5NdQ
- Share the website when they want more info: drdeepikagyno.in
- If a male patient asks, politely explain she is a gynecologist specializing in women's health, and recommend they see a general physician for their concern

MANDATORY CONVERSATION FLOW — ALWAYS FOLLOW THIS ORDER:
You MUST collect information in this exact sequence. Do NOT skip steps. Do NOT jump ahead.

STEP 1 — PHONE NUMBER: Always ask for the patient's phone number first. "Aapka phone number kya hai?" / "What is your phone number?"
STEP 2 — CONCERN/PROBLEM: Ask about the problem they are facing. "Aapko kya problem hai?" / "What problem are you facing?"
STEP 3 — LOCATION: Ask where they are located. "Aap kahan se hain?" / "Where are you located?"
STEP 4 — CONSULTATION TYPE: Tell them "We are situated in South Ex Part 1, Delhi. Are you going to visit us or book online consultation?"
  - In-clinic: Rs 1000
  - Online: Rs 500
  - If they ask for discount → tell them about code AKS250 on drdeepikagyno.in for Rs 750 consultation
STEP 5 — NAME: Ask for the patient's name. "Aapka naam?" / "Your name?"
STEP 6 — AGE: Ask for age. "Aapki umar?" / "Your age?"
STEP 7 — REPORTS: Ask "Koi report ya prescription hai toh share karein" / "Do you have any reports or prescriptions to share?" — tell them they can send images. This is OPTIONAL — if they don't have reports, move on.
STEP 8 — DATE AND TIME: Ask for preferred date and time.
STEP 9 — PAYMENT: After all details collected and date/time confirmed, tell them: "To confirm your appointment, please make the payment. I'll send you the QR code."
STEP 10 — ID CARD: After payment, politely ask for ID card (Aadhaar/Licence) — OPTIONAL.

IMPORTANT FLOW RULES:
- If patient gives multiple things in one message (e.g. "Hi, I'm Priya, age 28, from Delhi"), don't ask again — proceed to the NEXT missing step.
- NEVER ask for something already provided. Always check patient context for returning patients.
- For returning patients where name/age/location is already known, skip those steps and go directly to the concern.
- If they want discount → tell them about code AKS250 on drdeepikagyno.in for Rs 750 consultation.
- If they mention fees → In-clinic Rs 1000, Online Rs 500, Discount code AKS250 for Rs 750.

EMERGENCY HANDLING (VERY IMPORTANT — OVERRIDES NORMAL FLOW):
If a patient describes an EMERGENCY situation like:
- Labour pain / delivery pain
- Severe bleeding / heavy bleeding
- Severe abdominal pain
- Pregnancy complications
- Any life-threatening gynecological emergency
Then STOP the normal flow and immediately:
1. Show urgency and care
2. Ask: "What is the problem? Can you share your name, contact number, and current location?"
3. Give the emergency number: +918595954097 — tell them to call RIGHT NOW
4. Tell them: "Dr. Deepika's emergency line is +918595954097. Please call immediately."
5. Do NOT waste time asking for appointment details or following the normal flow
6. Set intent to EMERGENCY
7. After giving the emergency number, ask if they need help reaching the hospital

PAYMENT FLOW:
- After collecting all details and confirming date/time, say: "To confirm your booking, please make the payment. I'll send you the QR code."
- Set booking_ready=true, payment_pending=true
- When they share a payment screenshot → acknowledge it, confirm the appointment
- If they ask about fees → In-clinic Rs 1000, Online Rs 500, Discount code AKS250 for Rs 750
- If they ask for discount → send them to drdeepikagyno.in and tell them to book with code "AKS250" for Rs 250 off → consultation at Rs 750

IMAGE HANDLING:
- When a patient sends an image, the system will auto-detect what it is (ID card, prescription, report, payment screenshot, etc)
- You will receive the extracted data from the image in your context
- If it's an ID card → extract name, ID number, DOB and use it to fill patient details. Don't ask again what's already in the image.
- If it's a prescription/report → acknowledge it, summarize what you understood, and proceed with the flow
- If it's a payment screenshot → verify the payment and confirm the appointment
- Always acknowledge what you received from the image before asking the next question

WHAT YOU DO:
- Book, reschedule, cancel appointments
- Answer questions about clinic timings, address, fees, doctor speciality
- Gently convince hesitant patients to visit Dr. Deepika
- Handle emergencies by providing the emergency number immediately
- For any medical questions → gently redirect them to consult the doctor in person
- Never diagnose, prescribe, or give medical advice
- If someone is here for business → be professional, address their query, don't push medical booking

STAFF MESSAGES:
- Messages marked [Staff message] were sent by clinic staff directly from the WhatsApp number
- If the staff already answered a patient's question, don't repeat or contradict it
- If the staff confirmed an appointment, acknowledge it and move on
- If the staff said something, defer to the staff's version — don't second-guess
- Staff messages are context for you to be aware of, not messages from the patient

UNDERSTANDING PATIENT INTENT (VERY IMPORTANT):
Patients don't always say "reschedule" directly. Understand INDIRECT requests:
- "Mai parso nhi aapauga" / "I can't come day after tomorrow" = RESCHEDULE (they want a different date)
- "Kal ka time change karna hai" / "Need to change time" = RESCHEDULE
- "10 ko aauga" / "I'll come on the 10th" = RESCHEDULE (if they have an existing appointment)
- "Kisi our din chahiye" / "Want another day" = RESCHEDULE
- "Mai ni aa pauga" / "I won't be able to come" = RESCHEDULE (ask for new date)
- "Cancel krna h" / "Want to cancel" = CANCEL
- "Mil sakta h" / "Can I get an appointment" = BOOK
- "Dikhna hai" / "Need to show/consult" = BOOK
- "Labour pain" / "heavy bleeding" / "severe pain" / "emergency" = EMERGENCY
- If they just say a date like "10 ko" and have an active appointment → it's RESCHEDULE with that date
- If they just say a date and have NO active appointment → it's a new BOOKING with that date

DATE/TIME RULES:
- aaj/today, kal/tomorrow, parso = day after tomorrow
- "10 ko" means the 10th of this month (or next month if 10th has passed)
- Numbers like "1240" = 12:40, "430" = 4:30
- Accept any time from 9:00 AM to 9:00 PM
- Don't book past dates or times

DUPLICATE AWARENESS:
- If the patient already has an active appointment (check patient context), mention it naturally
- Don't block them — just inform and ask what they'd like to do

INTENT TYPES:
- BOOK: New appointment or rescheduling after a missed appointment (use BOOK, not RESCHEDULE, for no-show patients)
- RESCHEDULE: Change an EXISTING active appointment's date/time (only if they have a booked/confirmed appointment). When patient gives a new date for reschedule, include it in the "date" field.
- CANCEL: Cancel appointment
- STATUS: Check appointment details
- NO_SHOW_RESPONSE: Replying to missed appointment follow-up (be gentle, never pushy)
- EMERGENCY: Patient in urgent medical situation — give emergency number immediately
- QUERY: General clinic question, questions about doctor, reviews, experience
- UNKNOWN: Can't understand

CURRENT CONVERSATION STATE:
- Already collected phone number: {has_contact_number}
- Already collected concern: {has_concern}
- Already collected location: {has_location}
- Already collected consultation type: {has_consultation_type}
- Already collected name: {has_name}
- Already collected age: {has_age}
- Already collected reports: {has_reports}
- Already collected date/time: {has_datetime}
- Payment pending: {payment_pending}
- Image data received: {image_data_summary}

Based on the conversation state above, ask ONLY for the NEXT missing piece of information. Do NOT repeat questions for information already collected.

Respond ONLY in this JSON (no markdown, no extra text):
{{
  "intent": "BOOK|RESCHEDULE|CANCEL|STATUS|NO_SHOW_RESPONSE|EMERGENCY|QUERY|UNKNOWN",
  "patient_name": "string or null",
  "date": "YYYY-MM-DD or null",
  "time": "HH:MM or null",
  "time_preference": "morning|afternoon|evening|null",
  "reason": "string or null",
  "patient_age": "string or null",
  "patient_location": "string or null",
  "consultation_type": "in_clinic|online|null",
  "id_card": "string or null",
  "id_card_image": true or false,
  "contact_number": "string or null",
  "patient_details": {{"any_extra_detail": "string"}} or null,
  "needs_more_info": true or false,
  "booking_ready": true or false,
  "payment_pending": true or false,
  "no_show_response_type": "reschedule|found_doctor|callback|unwell|null",
  "language": "hinglish|english",
  "reply": "Your natural WhatsApp message to the patient (in English or Hinglish)",
  "audio_script": "If language is hinglish, write the exact same reply in proper Hindi using Devanagari script for the voice assistant. Otherwise empty."
}}"""


def _build_patient_context(
    patient_record: Optional[dict] = None,
    active_appointments: Optional[list[dict]] = None,
    no_show_appointments: Optional[list[dict]] = None,
) -> str:
    lines = []

    if patient_record:
        name = patient_record.get("name")
        if name:
            lines.append(f"RETURNING PATIENT: {name}")
        age = patient_record.get("age")
        if age:
            lines.append(f"  Age: {age}")
        location = patient_record.get("location")
        if location:
            lines.append(f"  Location: {location}")
        id_card = patient_record.get("id_card")
        if id_card:
            lines.append(f"  ID Card on file: {id_card}")
        id_card_image = patient_record.get("id_card_image_path")
        if id_card_image:
            lines.append(f"  ID Card image on file: yes")
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
        lines.append("No existing active appointments.")

    if no_show_appointments:
        lines.append("MISSED APPOINTMENTS (no-show):")
        for a in no_show_appointments:
            lines.append(
                f"  - {a['date']} at {a['time']} "
                f"(reason: {a.get('reason', 'N/A')}, followup: {a.get('followup_response', 'N/A')})"
            )
        lines.append("If the patient wants to reschedule a missed appointment, treat it as a new BOOKING intent — help them book a fresh slot.")

    return "\n".join(lines)


def _build_conversation_state(
    phone: str,
    patient_record: Optional[dict] = None,
    image_data: Optional[dict] = None,
) -> dict:
    has_contact_number = "no"
    has_name = "no"
    has_age = "no"
    has_location = "no"
    has_concern = "no"
    has_reports = "no"
    has_consultation_type = "no"
    has_datetime = "no"
    payment_pending = "no"

    if patient_record:
        if patient_record.get("name"):
            has_name = "yes"
        if patient_record.get("age"):
            has_age = "yes"
        if patient_record.get("location"):
            has_location = "yes"

    history = get_conversation_history(phone, limit=20)
    for msg in history:
        content = msg.get("content", "")
        role = msg.get("role", "")
        if role == "assistant":
            try:
                data = json.loads(content)
                if data.get("contact_number"):
                    has_contact_number = "yes"
                if data.get("patient_name"):
                    has_name = "yes"
                if data.get("patient_age"):
                    has_age = "yes"
                if data.get("patient_location"):
                    has_location = "yes"
                if data.get("reason"):
                    has_concern = "yes"
                if data.get("consultation_type"):
                    has_consultation_type = "yes"
                if data.get("date") or data.get("time"):
                    has_datetime = "yes"
                if data.get("payment_pending"):
                    payment_pending = "yes"
            except (json.JSONDecodeError, TypeError):
                pass

    image_data_summary = "none"
    if image_data and image_data.get("success"):
        image_data_summary = json.dumps(image_data.get("data", {}), ensure_ascii=False)

    return {
        "has_contact_number": has_contact_number,
        "has_name": has_name,
        "has_age": has_age,
        "has_location": has_location,
        "has_concern": has_concern,
        "has_reports": has_reports,
        "has_consultation_type": has_consultation_type,
        "has_datetime": has_datetime,
        "payment_pending": payment_pending,
        "image_data_summary": image_data_summary,
    }


def _build_system_prompt(
    phone: str,
    patient_record: Optional[dict] = None,
    active_appointments: Optional[list[dict]] = None,
    no_show_appointments: Optional[list[dict]] = None,
    image_data: Optional[dict] = None,
) -> str:
    now = clinic_now().strftime("%A, %d %B %Y, %I:%M %p")
    patient_ctx = _build_patient_context(patient_record, active_appointments, no_show_appointments)
    conv_state = _build_conversation_state(phone, patient_record, image_data)

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
        clinic_phone=settings.CLINIC_PHONE,
        **conv_state,
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
    no_show_appointments: Optional[list[dict]] = None,
    image_data: Optional[dict] = None,
) -> dict:
    detected_language = detect_language(user_message)
    detected_date = available_slots_date or parse_date_from_text(user_message)
    detected_time = parse_time_from_text(user_message)
    history = get_conversation_history(phone, limit=10)

    system_prompt = _build_system_prompt(
        phone, patient_record, active_appointments, no_show_appointments, image_data
    )
    messages = [{"role": "system", "content": system_prompt}]

    slots_context = _get_slots_context(detected_date)
    if detected_date:
        messages.append({"role": "system", "content": f"Slot availability info: {slots_context}"})
        messages.append({"role": "system", "content": f"Parsed date from patient message: {detected_date}"})
    if detected_time:
        messages.append({"role": "system", "content": f"Parsed time from patient message: {detected_time}"})

    if image_data and image_data.get("success"):
        img_summary = f"IMAGE DATA EXTRACTED (use this information, do NOT ask the patient for details already extracted): {json.dumps(image_data.get('data', {}), ensure_ascii=False)}"
        messages.append({"role": "system", "content": img_summary})

    for msg in history:
        content = msg["content"]
        if msg.get("sender_type") == "staff":
            content = f"[Staff message - sent by clinic staff directly]: {content}"
        messages.append({"role": msg["role"], "content": content})

    messages.append({"role": "user", "content": user_message})

    save_conversation(phone, "user", user_message)

    try:
        def _ollama_chat(msgs):
            client = ollama.Client(host=settings.OLLAMA_HOST, timeout=120)
            return client.chat(
                model=settings.OLLAMA_MODEL,
                messages=msgs,
                options={"temperature": 0.3, "num_predict": 512},
            )

        response = await asyncio.to_thread(_ollama_chat, messages)
        raw = response["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ollama call failed for {phone}: {e}")
        fallback = {
            "intent": "UNKNOWN",
            "patient_name": None,
            "date": None,
            "time": None,
            "time_preference": None,
            "reason": None,
            "patient_age": None,
            "patient_location": None,
            "consultation_type": None,
            "id_card": None,
            "id_card_image": False,
            "patient_details": None,
            "needs_more_info": True,
            "booking_ready": False,
            "payment_pending": False,
            "no_show_response_type": None,
            "language": detected_language if detected_language != "hindi" else "hinglish",
            "audio_script": None,
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

    if image_data and image_data.get("success"):
        img_data = image_data.get("data", {})
        img_type = image_data.get("image_type", "general")
        if img_type == "id_card" and img_data:
            if not parsed.get("patient_name") and img_data.get("name"):
                parsed["patient_name"] = img_data["name"]
            if not parsed.get("patient_age") and img_data.get("dob"):
                parsed["patient_age"] = img_data["dob"]
            if not parsed.get("id_card") and img_data.get("id_number"):
                parsed["id_card"] = f"{img_data.get('id_type', 'ID')}: {img_data['id_number']}"
            if not parsed.get("patient_details"):
                parsed["patient_details"] = {}
            if img_data.get("address"):
                parsed["patient_details"]["address_from_id"] = img_data["address"]
            if img_data.get("gender"):
                parsed["patient_details"]["gender"] = img_data["gender"]
        elif img_type == "payment_screenshot" and img_data:
            if img_data.get("payment_status") == "success":
                parsed["payment_pending"] = False
            if not parsed.get("patient_details"):
                parsed["patient_details"] = {}
            parsed["patient_details"]["payment_info"] = img_data

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
                    "patient_location": data.get("patient_location"),
                    "consultation_type": data.get("consultation_type"),
                    "id_card": data.get("id_card"),
                    "id_card_image": data.get("id_card_image", False),
                    "contact_number": data.get("contact_number"),
                    "patient_details": data.get("patient_details"),
                    "needs_more_info": data.get("needs_more_info", True),
                    "booking_ready": data.get("booking_ready", False),
                    "payment_pending": data.get("payment_pending", False),
                    "no_show_response_type": data.get("no_show_response_type"),
                    "language": data.get("language", "english"),
                    "reply": clean_patient_reply(data.get("reply", ""), data.get("language", "english")),
                    "audio_script": data.get("audio_script", ""),
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
        "patient_location": None,
        "consultation_type": None,
        "id_card": None,
        "id_card_image": False,
        "contact_number": None,
        "patient_details": None,
        "needs_more_info": True,
        "booking_ready": False,
        "payment_pending": False,
        "no_show_response_type": None,
        "language": "english",
        "audio_script": None,
        "reply": clean_patient_reply(raw if raw else "I'm sorry, could you please repeat that?"),
    }
