import json
import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional
import asyncio
from collections import defaultdict
import httpx
import ollama
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from backend.config import settings
from backend.database import (
    init_db,
    get_appointments,
    update_appointment_status,
    update_appointment_payment,
    update_appointment_reports,
    save_patient_document,
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
from backend.image_reader import read_image_with_classification

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
    try:
        client = ollama.Client(host=settings.OLLAMA_HOST)
        client.list()
        logger.info(f"Ollama connection verified at {settings.OLLAMA_HOST}")
    except Exception as e:
        logger.warning(f"Ollama not reachable at {settings.OLLAMA_HOST}: {e}. AI responses will fail until Ollama is running.")
    init_scheduler()
    logger.info("Scheduler started")
    yield


app = FastAPI(title="Clinic AI Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_api_key(x_api_key: str = Header(None)):
    """Simple API key check for sensitive endpoints."""
    expected = os.getenv("API_KEY", "")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid API key")


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
        incoming_file = bool(image_path)

        patient_record = get_patient(phone)

        # Proactive greeting for brand new contacts with quick buttons
        is_new_patient = patient_record is None and not incoming_audio and not incoming_file
        greeting_sent = False
        if is_new_patient and user_message.strip():
            greeting = f"Namaste! Welcome to {settings.CLINIC_NAME}. I'm Priya, your appointment assistant. How can I help you today?"
            try:
                from backend.whatsapp_sender import send_button_message as whatsapp_send_buttons
                await whatsapp_send_buttons(phone, greeting, [
                    "Book Appointment",
                    "Check Status",
                    "Cancel Appointment",
                    "Talk to Doctor",
                ])
                greeting_sent = True
            except Exception as greet_err:
                logger.warning(f"Failed to send greeting to {phone}: {greet_err}")
                try:
                    from backend.whatsapp_sender import send_text as whatsapp_send_text
                    await whatsapp_send_text(phone, greeting)
                    greeting_sent = True
                except Exception:
                    pass

        if incoming_file and image_path and os.path.exists(image_path):
            file_ext = os.path.splitext(image_path)[1].lower()
            logger.info(f"File received from {phone}: {image_path} (type: {file_ext})")
            try:
                image_data = await read_image_with_classification(image_path)
                img_type = image_data.get("image_type", "general")
                logger.info(f"Image classified as '{img_type}' for {phone}")
                if image_data.get("success"):
                    save_patient_document(phone, img_type, image_path, image_data.get("data"))
                    img_data = image_data.get("data", {})
                    if img_type == "payment_screenshot" and img_data.get("payment_status") == "success":
                        active_for_payment = [a for a in get_patient_appointments(phone) if a["status"] in ("booked", "confirmed")]
                        if active_for_payment:
                            appt = active_for_payment[-1]
                            update_appointment_payment(appt["id"], "paid", image_path)
                            asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
                            try:
                                from backend.whatsapp_sender import send_text as whatsapp_send_text
                                await whatsapp_send_text(settings.DOCTOR_PHONE, f"Payment screenshot received from {patient_record.get('name', phone) if patient_record else phone}. Amount: {img_data.get('amount', 'N/A')}. Txn: {img_data.get('transaction_id', 'N/A')}")
                            except Exception as doc_err:
                                logger.warning(f"Failed to notify doctor about payment: {doc_err}")
                    elif img_type == "id_card" and img_data:
                        if not user_message.strip():
                            user_message = f"[Patient sent ID card image - {img_data.get('id_type', 'ID')}: {img_data.get('id_number', 'N/A')}, Name: {img_data.get('name', 'N/A')}]"
                    elif img_type in ("prescription", "report") and img_data:
                        active_for_reports = [a for a in get_patient_appointments(phone) if a["status"] in ("booked", "confirmed")]
                        if active_for_reports:
                            appt = active_for_reports[-1]
                            update_appointment_reports(appt["id"], img_data)
                        asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
                        if not user_message.strip():
                            user_message = f"[Patient sent {img_type} image]"
                    # Trigger sheet sync for ID card uploads too
                    if img_type == "id_card":
                        asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
                else:
                    img_type = "general"
            except Exception as img_err:
                logger.error(f"Image reading failed for {phone}: {img_err}")
                img_type = "general"
                image_data = None
        else:
            image_data = None

        audio_lang_hint = None
        if audio_path and os.path.exists(audio_path):
            try:
                user_message, audio_lang_hint = await transcribe_audio(audio_path)
                logger.info(f"Transcribed audio from {phone}: {user_message[:50]} (lang: {audio_lang_hint})")
            except Exception as e:
                logger.error(f"STT failed for {phone}: {e}")
                return {"text_reply": "Sorry, I couldn't understand the voice note. Could you please type your message?", "audio_path": None}

        if not user_message.strip() and not incoming_file:
            return {"text_reply": "Hi! How can I help you today?", "audio_path": None}

        language = detect_language(user_message)
        if not is_probably_appointment_related(user_message):
            reply_text = appointment_scope_reply(language)
            save_conversation(phone, "user", user_message)
            save_conversation(phone, "assistant", reply_text)
            audio_reply_path = None
            if incoming_audio:
                try:
                    tts_text = "मैं डॉ दीपिका सिंह क्लिनिक के अपॉइंटमेंट्स, स्लॉट, फीस, टाइमिंग, पता और बुकिंग में मदद करने के लिए हूँ। अपॉइंटमेंट से संबंधित कोई सवाल हो तो बताएं, मैं आपकी मदद करूंगी।" if language == "hinglish" else reply_text
                    audio_reply_path = await generate_voice_reply(tts_text, language=language)
                except Exception:
                    audio_reply_path = None
            return {"text_reply": reply_text, "audio_path": os.path.abspath(audio_reply_path) if audio_reply_path else None}

        # ── Returning patient context ──────────────────────────────
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

        logger.info(f"Calling AI for {phone}: message='{user_message[:80]}', date={detected_date}, time={detected_time}")

        try:
            ai_result = await asyncio.wait_for(
                get_ai_response(
                    phone, user_message, detected_date,
                    patient_record=patient_record,
                    active_appointments=active_appts,
                    no_show_appointments=no_show_appts,
                    image_data=image_data,
                ),
                timeout=180,
            )
        except asyncio.TimeoutError:
            logger.error(f"AI response timed out for {phone} (180s limit)")
            return {"text_reply": "I'm sorry, I'm taking too long to respond. Please try again.", "audio_path": None}
        except Exception as e:
            logger.error(f"AI response failed for {phone}: {e}")
            return {"text_reply": "I'm having trouble right now. Please try again in a moment.", "audio_path": None}

        language = ai_result.get("language") or language
        reply_text = clean_patient_reply(ai_result.get("reply", "How can I help you?"), language)
        audio_script = ai_result.get("audio_script")
        audio_reply_path = None

        intent = ai_result.get("intent", "UNKNOWN")
        booking_ready = ai_result.get("booking_ready", False)
        payment_pending = ai_result.get("payment_pending", False)
        date_str = ai_result.get("date") or detected_date
        time_str = ai_result.get("time") or detected_time
        time_pref = ai_result.get("time_preference")
        patient_name = ai_result.get("patient_name")
        patient_location = ai_result.get("patient_location")
        consultation_type = ai_result.get("consultation_type")
        reason = ai_result.get("reason")
        patient_age = ai_result.get("patient_age")
        id_card = ai_result.get("id_card")
        id_card_image_flag = ai_result.get("id_card_image", False)
        patient_details = ai_result.get("patient_details")
        contact_number = ai_result.get("contact_number")

        id_card_image_path = None
        if incoming_file and image_path:
            id_card_image_path = image_path
            if not id_card:
                id_card = "image_on_file"

        if contact_number:
            if not patient_details:
                patient_details = {}
            patient_details["contact_number"] = contact_number

        if patient_location and patient_record:
            from backend.database import update_patient_location
            update_patient_location(phone, patient_location)

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
                            patient_age=patient_age,
                            patient_location=patient_location,
                            consultation_type=consultation_type,
                            id_card=id_card,
                            details=patient_details,
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

        elif intent == "BOOK" and payment_pending and not booking_ready:
            qr_path = settings.QR_CODE_PATH
            if os.path.exists(qr_path):
                try:
                    from backend.whatsapp_sender import send_text as whatsapp_send_text
                    await whatsapp_send_text(phone, "Please scan the QR code below to make the payment. After payment, send me the screenshot.")
                except Exception as qr_err:
                    logger.warning(f"Failed to send payment info: {qr_err}")
            else:
                reply_text = reply_text or "To confirm your booking, please make the payment. You can pay via UPI to +918595954097. After payment, send me the screenshot."

        elif intent == "EMERGENCY":
            try:
                from backend.whatsapp_sender import send_text as whatsapp_send_text
                await whatsapp_send_text(phone, f"EMERGENCY - Please call Dr. Deepika immediately: +918595954097")
            except Exception as em_err:
                logger.warning(f"Failed to send emergency message: {em_err}")

        elif intent == "CANCEL":
            cancelled = cancel_appointment(phone, date_str)
            if cancelled:
                reply_text = "Your appointment has been cancelled. If you'd like to book again, just let me know!"
                clear_old_conversations(phone)
                asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
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
                        asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
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

        # Audio reply logic:
        # - Patient sent voice note → reply with text + audio
        # - Patient asks for a call / emergency → reply with text + audio
        # - Patient sent text → reply with text only
        send_audio = False
        if incoming_audio:
            send_audio = True
            logger.info(f"Patient sent audio, will generate voice reply for {phone}")
        elif intent in ("EMERGENCY",):
            send_audio = True
        elif user_message and any(w in user_message.lower() for w in ["call", "phone", "ring", "bolna", "baat", "talk"]):
            send_audio = True

        if send_audio:
            try:
                tts_text = audio_script if audio_script and language == "hinglish" else reply_text
                logger.info(f"Generating voice reply for {phone} (lang={language}, provider={settings.TTS_PROVIDER})")
                audio_reply_path = await generate_voice_reply(tts_text, language=language)
                if audio_reply_path:
                    logger.info(f"Voice reply generated: {audio_reply_path}")
                else:
                    logger.warning(f"Voice reply returned None for {phone}")
            except Exception as e:
                logger.error(f"Voice reply failed for {phone}: {e}")
                audio_reply_path = None

        # Convert to absolute path so WhatsApp bot (running from whatsapp-bot/) can find the file
        if audio_reply_path:
            audio_reply_path = os.path.abspath(audio_reply_path)

        logger.info(f"Final reply for {phone}: text_reply='{reply_text[:50]}', audio_path='{audio_reply_path}'")
        return {"text_reply": reply_text, "audio_path": audio_reply_path}


@app.post("/webhook/staff-message")
async def handle_staff_message(payload: WebhookMessage):
    """Track messages sent by clinic staff (fromMe=true in WhatsApp) so AI has context."""
    phone = payload.phone
    message_text = payload.message_text or ""
    if not message_text.strip():
        return {"status": "ignored"}
    save_conversation(phone, "user", message_text, sender_type="staff")
    logger.info(f"Staff message tracked for {phone}: {message_text[:80]}")
    return {"status": "tracked"}


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
        asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
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
    asyncio.get_event_loop().run_in_executor(None, _sync_sheet_background)
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


@app.get("/export/appointments.xlsx", dependencies=[Depends(verify_api_key)])
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


@app.post("/integrations/google-sheets/sync", dependencies=[Depends(verify_api_key)])
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


@app.get("/admin/settings", dependencies=[Depends(verify_api_key)])
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


@app.post("/admin/settings", dependencies=[Depends(verify_api_key)])
async def update_settings(update: SettingsUpdate):
    for key, value in update.dict(exclude_unset=True).items():
        settings.update_setting(key, str(value))
    return {"message": "Settings updated successfully"}


@app.get("/admin/models", dependencies=[Depends(verify_api_key)])
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
