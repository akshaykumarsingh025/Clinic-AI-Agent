import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional
import asyncio
from collections import defaultdict
import httpx
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from backend.config import settings
from backend.database import (
    init_db,
    get_appointments,
    update_appointment_status,
    get_patient,
    get_patient_appointments,
    get_no_show_stats,
    block_slot,
    get_appointment_by_id,
    update_followup_response,
    save_conversation,
    clear_old_conversations,
)
from backend.models import WebhookMessage, ButtonReply, BlockSlotRequest, GoogleSheetSyncRequest
from backend.agent import get_ai_response
from backend.integrations import export_appointments_xlsx, sync_appointments_to_google_sheet, sync_appointment_to_google_calendar
from backend.nlu import (
    appointment_scope_reply,
    clean_patient_reply,
    detect_language,
    is_past_slot,
    is_probably_appointment_related,
    parse_date_from_text,
    parse_time_from_text,
)
from backend.booking import (
    book_appointment,
    cancel_appointment,
    reschedule_appointment,
    get_available_slots,
    find_best_slot,
    find_next_available_date,
    format_appointment_confirmation,
)
from backend.scheduler import (
    init_scheduler,
    schedule_appointment_reminders,
    schedule_no_show_check,
    cancel_scheduled_jobs,
)
from backend.stt import transcribe_audio
from backend.tts import generate_voice_reply

os.makedirs("./logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("./logs/app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized")
    init_scheduler()
    logger.info("Scheduler started")
    yield


app = FastAPI(title="Clinic AI Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "clinic": settings.CLINIC_NAME}


user_locks = defaultdict(asyncio.Lock)


def _sync_sheet_background():
    """Run Google Sheet sync in a thread — never block the webhook."""
    try:
        if settings.GOOGLE_SHEET_ID and settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            sync_appointments_to_google_sheet()
    except Exception as exc:
        logger.warning(f"Google Sheet sync failed: {exc}")


def _sync_calendar_background(appointment):
    """Run Google Calendar sync in a thread."""
    try:
        if settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            sync_appointment_to_google_calendar(appointment)
    except Exception as exc:
        logger.warning(f"Google Calendar sync failed: {exc}")


@app.post("/webhook/message")
async def handle_message(payload: WebhookMessage):
    phone = payload.phone

    async with user_locks[phone]:
        user_message = payload.message_text or ""
        audio_path = payload.audio_path
        image_path = payload.image_path
        incoming_audio = bool(audio_path)
        incoming_image = bool(image_path)

        if incoming_image and image_path and os.path.exists(image_path):
            user_message = "[Patient sent an image — likely an ID card photo]" if not user_message.strip() else user_message
            logger.info(f"Image received from {phone}: {image_path}")

        audio_lang_hint = None
        if audio_path and os.path.exists(audio_path):
            try:
                user_message, audio_lang_hint = await transcribe_audio(audio_path)
                logger.info(f"Transcribed audio from {phone}: {user_message[:50]} (lang: {audio_lang_hint})")
            except Exception as e:
                logger.error(f"STT failed for {phone}: {e}")
                return {"text_reply": "Sorry, I couldn't understand the voice note. Could you please type your message?", "audio_path": None}

        if not user_message.strip() and not incoming_image:
            return {"text_reply": "Hi! How can I help you today?", "audio_path": None}

        language = detect_language(user_message)
        if not is_probably_appointment_related(user_message):
            reply_text = appointment_scope_reply(language)
            save_conversation(phone, "user", user_message)
            save_conversation(phone, "assistant", reply_text)
            audio_reply_path = None
            if incoming_audio:
                try:
                    audio_reply_path = await generate_voice_reply(reply_text, language=language)
                except Exception:
                    audio_reply_path = None
            return {"text_reply": reply_text, "audio_path": audio_reply_path}

        # ── Returning patient context ──────────────────────────────
        patient_record = get_patient(phone)
        active_appts = [
            a for a in get_patient_appointments(phone)
            if a["status"] in ("booked", "confirmed")
        ]
        no_show_appts = [
            a for a in get_patient_appointments(phone)
            if a["status"] == "no_show"
        ]

        detected_date = parse_date_from_text(user_message)
        detected_time = parse_time_from_text(user_message)

        try:
            ai_result = await get_ai_response(
                phone, user_message, detected_date,
                patient_record=patient_record,
                active_appointments=active_appts,
                no_show_appointments=no_show_appts,
            )
        except Exception as e:
            logger.error(f"AI response failed for {phone}: {e}")
            return {"text_reply": "I'm having trouble right now. Please try again in a moment.", "audio_path": None}

        language = ai_result.get("language") or language
        reply_text = clean_patient_reply(ai_result.get("reply", "How can I help you?"), language)
        audio_reply_path = None

        intent = ai_result.get("intent", "UNKNOWN")
        booking_ready = ai_result.get("booking_ready", False)
        date_str = ai_result.get("date") or detected_date
        time_str = ai_result.get("time") or detected_time
        time_pref = ai_result.get("time_preference")
        patient_name = ai_result.get("patient_name")
        reason = ai_result.get("reason")
        patient_age = ai_result.get("patient_age")
        id_card = ai_result.get("id_card")
        id_card_image_flag = ai_result.get("id_card_image", False)
        patient_details = ai_result.get("patient_details")
        contact_number = ai_result.get("contact_number")

        id_card_image_path = None
        if incoming_image and image_path:
            id_card_image_path = image_path
            if not id_card:
                id_card = "image_on_file"

        if contact_number:
            if not patient_details:
                patient_details = {}
            patient_details["contact_number"] = contact_number

        # Use stored name if AI didn't extract one
        if not patient_name and patient_record and patient_record.get("name"):
            patient_name = patient_record["name"]

        if intent == "BOOK" and booking_ready and date_str:
            if not time_str:
                time_str = find_best_slot(date_str, time_pref)

            if not time_str:
                reply_text = "Please let me know what time you'd like for your appointment."
            elif is_past_slot(date_str, time_str):
                reply_text = "That date or time has already passed. Please share a future date and time for the appointment."
            else:
                # ── Duplicate detection ────────────────────────────
                existing_same_slot = [
                    a for a in active_appts
                    if a["date"] == date_str and a["time"] == time_str
                ]
                existing_same_day = [
                    a for a in active_appts
                    if a["date"] == date_str
                ]

                if existing_same_slot:
                    reply_text = (
                        f"You already have an appointment on {date_str} at {time_str}. "
                        f"No need to book again! If you'd like to change the time, just say 'reschedule'."
                    )
                elif existing_same_day:
                    old = existing_same_day[0]
                    reply_text = (
                        f"You already have an appointment on {date_str} at {old['time']}. "
                        f"Would you like to reschedule it to {time_str} instead, or book a second appointment?"
                    )
                else:
                    try:
                        name = patient_name or "Patient"
                        appointment = book_appointment(
                            phone, name, date_str, time_str, reason,
                            patient_age=patient_age, id_card=id_card, details=patient_details,
                            id_card_image_path=id_card_image_path,
                        )
                        reply_text = format_appointment_confirmation(appointment)
                        schedule_appointment_reminders(appointment)
                        schedule_no_show_check(appointment)
                        clear_old_conversations(phone)

                        # Background Google Sheet sync
                        asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
                        # Background Google Calendar sync
                        asyncio.get_event_loop().run_in_executor(None, _sync_calendar_background, appointment)

                    except Exception as e:
                        logger.error(f"Booking failed for {phone}: {e}")
                        reply_text = "Sorry, something went wrong while booking. Please try again."

        elif intent == "CANCEL":
            cancelled = cancel_appointment(phone, date_str)
            if cancelled:
                reply_text = "Your appointment has been cancelled. If you'd like to book again, just let me know!"
                clear_old_conversations(phone)
            else:
                reply_text = "I couldn't find an active appointment to cancel. Could you share the date?"

        elif intent == "RESCHEDULE":
            if active_appts and date_str and time_str:
                appt = active_appts[0]
                if is_past_slot(date_str, time_str):
                    reply_text = "That date or time has already passed. Please share a future date and time for rescheduling."
                else:
                    success = reschedule_appointment(appt["id"], date_str, time_str)
                    if success:
                        new_appts = get_patient_appointments(phone)
                        new_appt = new_appts[-1]
                        reply_text = f"Done! Rescheduled to {date_str} at {time_str}.\n{format_appointment_confirmation(new_appt)}"
                        cancel_scheduled_jobs(appt["id"])
                        schedule_appointment_reminders(new_appt)
                        schedule_no_show_check(new_appt)
                        clear_old_conversations(phone)
                    else:
                        reply_text = "Sorry, couldn't reschedule. Would you like to try a different time?"
            elif active_appts:
                appt = active_appts[0]
                reply_text = (
                    f"Sure! Your current appointment is on {appt['date']} at {appt['time']}. "
                    f"What new date and time would you like?"
                )
            elif no_show_appts:
                old = no_show_appts[-1]
                update_appointment_status(old["id"], "cancelled")
                reply_text = (
                    "I see you had a missed appointment. Let me help you book a fresh one! "
                    "What date and time works for you?"
                )
            else:
                reply_text = "You don't have any active appointments to reschedule. Would you like to book a new one?"

        elif intent == "STATUS":
            if active_appts:
                lines = []
                for a in active_appts:
                    lines.append(f"- {a['date']} at {a['time']} (Status: {a['status']})")
                reply_text = f"Your appointments:\n" + "\n".join(lines)
            else:
                reply_text = "You don't have any active appointments. Would you like to book one?"

        elif intent == "NO_SHOW_RESPONSE":
            ns_type = ai_result.get("no_show_response_type")
            no_show_appts = [a for a in get_patient_appointments(phone) if a["status"] == "no_show" and a["followup_sent"]]
            if no_show_appts and ns_type:
                appt = no_show_appts[-1]
                update_followup_response(appt["id"], ns_type)

                if ns_type == "reschedule":
                    reply_text = "Of course! Let's find you a new slot. What date and time works for you?"
                elif ns_type == "found_doctor":
                    reply_text = "Glad you got the help you needed! Feel free to reach out anytime. Take care!"
                elif ns_type == "callback":
                    reply_text = "No problem! Call us whenever you're ready. Our number is " + settings.CLINIC_PHONE
                elif ns_type == "unwell":
                    reply_text = f"I'm sorry to hear that. Please call us at {settings.CLINIC_PHONE} for immediate assistance. Wishing you a speedy recovery!"

        reply_text = clean_patient_reply(reply_text, language)

        if incoming_audio or settings.SEND_AUDIO_REPLIES_FOR_TEXT:
            try:
                audio_reply_path = await generate_voice_reply(reply_text, language=language)
            except Exception:
                audio_reply_path = None

        logger.info(f"Final reply for {phone}: text_reply='{reply_text}', audio_path='{audio_reply_path}'")
        return {"text_reply": reply_text, "audio_path": audio_reply_path}


@app.post("/webhook/button-reply")
async def handle_button_reply(payload: ButtonReply):
    phone = payload.phone
    button = payload.button_number

    button_map = {1: "reschedule", 2: "found_doctor", 3: "callback", 4: "unwell"}
    response_type = button_map.get(button)

    if not response_type:
        return {"reply": "Sorry, I didn't understand that. Please reply with 1, 2, 3, or 4."}

    all_appts = get_patient_appointments(phone)
    no_show_appts = [a for a in all_appts if a["status"] == "no_show"]

    if not no_show_appts:
        if response_type == "reschedule":
            return {"reply": "Of course! I'll help you book a new appointment. Just tell me the date and time that works for you."}
        return {"reply": "Thank you for your response. Is there anything else I can help with?"}

    appt = no_show_appts[-1]
    if appt.get("followup_sent"):
        update_followup_response(appt["id"], response_type)

    if response_type == "reschedule":
        update_appointment_status(appt["id"], "cancelled")
        return {"reply": "Of course! Let's find you a new slot. What date and time works for you?"}
    elif response_type == "found_doctor":
        return {"reply": "Glad you got the help you needed! Feel free to reach out anytime. Take care!"}
    elif response_type == "callback":
        return {"reply": f"No problem! Call us whenever you're ready at {settings.CLINIC_PHONE}."}
    elif response_type == "unwell":
        return {"reply": f"I'm sorry to hear that. Please call us at {settings.CLINIC_PHONE} for help. Wishing you a speedy recovery!"}
    else:
        return {"reply": "Thank you for letting us know."}


@app.get("/appointments/today")
async def appointments_today():
    today = datetime.now().strftime("%Y-%m-%d")
    return get_appointments(today)


@app.get("/appointments/date/{date}")
async def appointments_by_date(date: str):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    return get_appointments(date)


@app.post("/appointments/{appointment_id}/checkin")
async def check_in(appointment_id: int):
    appt = get_appointment_by_id(appointment_id)
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    update_appointment_status(appointment_id, "checked_in")
    cancel_scheduled_jobs(appointment_id)
    return {"message": "Patient checked in", "appointment_id": appointment_id}


@app.post("/slots/block")
async def block_slot_endpoint(payload: BlockSlotRequest):
    block_slot(payload.date, payload.time, payload.reason)
    return {"message": "Slot blocked", "date": payload.date, "time": payload.time}


@app.get("/slots/available/{date}")
async def available_slots(date: str):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    slots = get_available_slots(date)
    return {"date": date, "available_slots": slots}


@app.get("/stats/no-shows")
async def no_show_stats():
    return get_no_show_stats()


@app.get("/export/appointments.xlsx")
async def export_appointments_excel():
    try:
        path = export_appointments_xlsx()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(path),
    )


@app.post("/integrations/google-sheets/sync")
async def sync_google_sheet(payload: GoogleSheetSyncRequest):
    try:
        return sync_appointments_to_google_sheet(payload.sheet_id, payload.credentials_path, payload.worksheet_gid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SettingsUpdate(BaseModel):
    CLINIC_TIMEZONE: Optional[str] = None
    MORNING_START: Optional[str] = None
    MORNING_END: Optional[str] = None
    EVENING_START: Optional[str] = None
    EVENING_END: Optional[str] = None
    OLLAMA_MODEL: Optional[str] = None
    WORKING_DAYS: Optional[str] = None


@app.get("/admin/settings")
async def get_settings():
    return {
        "CLINIC_TIMEZONE": settings.CLINIC_TIMEZONE,
        "MORNING_START": settings.MORNING_START,
        "MORNING_END": settings.MORNING_END,
        "EVENING_START": settings.EVENING_START,
        "EVENING_END": settings.EVENING_END,
        "OLLAMA_MODEL": settings.OLLAMA_MODEL,
        "OLLAMA_HOST": settings.OLLAMA_HOST,
        "WORKING_DAYS": ",".join(settings.WORKING_DAYS),
    }


@app.post("/admin/settings")
async def update_settings(update: SettingsUpdate):
    for key, value in update.dict(exclude_unset=True).items():
        settings.update_setting(key, str(value))
    return {"message": "Settings updated successfully"}


@app.get("/admin/models")
async def get_ollama_models():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.OLLAMA_HOST}/api/tags", timeout=5.0)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return {"models": models}
    except Exception as e:
        logger.error(f"Failed to fetch Ollama models: {e}")
        return {"models": [settings.OLLAMA_MODEL]}
