import logging
import os
import glob
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from backend.config import settings
from backend.database import (
    get_upcoming_appointments,
    get_past_unconfirmed,
    update_appointment_status,
    mark_reminder_sent,
    mark_followup_sent,
    update_followup_response,
    get_patient_appointments,
    get_appointment_by_id,
    get_appointments,
)

logger = logging.getLogger(__name__)

jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///database/jobs.sqlite')
}
scheduler = AsyncIOScheduler(jobstores=jobstores)


async def _send_reminder_24h(appointment: dict):
    from backend.whatsapp_sender import send_text

    name = appointment["patient_name"]
    time_display = appointment["time"]

    text = (
        f"Hi {name}! Reminder for your appointment tomorrow at {time_display} "
        f"with {settings.DOCTOR_NAME}. Reply CONFIRM to confirm or CANCEL to cancel."
    )

    try:
        await send_text(appointment["phone"], text)
        mark_reminder_sent(appointment["id"])
        logger.info(f"24h reminder sent for appointment {appointment['id']}")
    except Exception as e:
        logger.error(f"Failed to send 24h reminder for appointment {appointment['id']}: {e}")


async def _send_reminder_2h(appointment: dict):
    from backend.whatsapp_sender import send_text

    name = appointment["patient_name"]
    time_display = appointment["time"]

    text = (
        f"Hi {name}, your appointment is in 2 hours at {time_display}. "
        f"Clinic address: {settings.CLINIC_ADDRESS}. See you soon!"
    )

    try:
        await send_text(appointment["phone"], text)
        logger.info(f"2h reminder sent for appointment {appointment['id']}")
    except Exception as e:
        logger.error(f"Failed to send 2h reminder for appointment {appointment['id']}: {e}")


async def _send_no_show_followup(appointment: dict):
    from backend.whatsapp_sender import send_text

    name = appointment["patient_name"]
    time_display = appointment["time"]

    text = (
        f"Hi {name}, we noticed you missed your appointment today at {time_display} "
        f"with {settings.DOCTOR_NAME}. Hope you're okay!\n\n"
        f"Please let us know:\n"
        f"1. I'd like to reschedule\n"
        f"2. I consulted another doctor\n"
        f"3. I'll call back to reschedule later\n"
        f"4. I'm unwell and need help\n\n"
        f"Just reply with 1, 2, 3, or 4"
    )

    try:
        update_appointment_status(appointment["id"], "no_show")
        await send_text(appointment["phone"], text)
        mark_followup_sent(appointment["id"])
        logger.info(f"No-show followup sent for appointment {appointment['id']}")
    except Exception as e:
        logger.error(f"Failed to send no-show followup for appointment {appointment['id']}: {e}")


async def _send_voice_followup(phone: str, name: str, appointment_id: int):
    from backend.whatsapp_sender import send_voice_note
    from backend.tts import generate_voice_reply

    appt = get_appointment_by_id(appointment_id)
    if not appt or appt["status"] != "no_show" or appt.get("followup_response"):
        return

    voice_text = (
        f"Hi {name}, this is {settings.CLINIC_NAME}. We missed you today. "
        f"We hope you're doing well. Please WhatsApp us when you're free to reschedule. Take care!"
    )

    try:
        audio_path = await generate_voice_reply(voice_text)
        if audio_path:
            await send_voice_note(phone, audio_path)
            logger.info(f"Voice followup sent to {phone}")
    except Exception as e:
        logger.error(f"Failed to send voice followup to {phone}: {e}")


def schedule_appointment_reminders(appointment: dict):
    try:
        appt_datetime = datetime.strptime(
            f"{appointment['date']} {appointment['time']}", "%Y-%m-%d %H:%M"
        )
    except (ValueError, KeyError):
        logger.error(f"Invalid appointment datetime for scheduling: {appointment}")
        return

    reminder_24h_time = appt_datetime - timedelta(hours=24)
    if reminder_24h_time > datetime.now():
        scheduler.add_job(
            _send_reminder_24h,
            trigger=DateTrigger(run_date=reminder_24h_time),
            args=[appointment],
            id=f"reminder_24h_{appointment['id']}",
            replace_existing=True,
        )

    reminder_2h_time = appt_datetime - timedelta(hours=2)
    if reminder_2h_time > datetime.now():
        scheduler.add_job(
            _send_reminder_2h,
            trigger=DateTrigger(run_date=reminder_2h_time),
            args=[appointment],
            id=f"reminder_2h_{appointment['id']}",
            replace_existing=True,
        )


def schedule_no_show_check(appointment: dict):
    try:
        appt_datetime = datetime.strptime(
            f"{appointment['date']} {appointment['time']}", "%Y-%m-%d %H:%M"
        )
    except (ValueError, KeyError):
        return

    no_show_time = appt_datetime + timedelta(minutes=45)
    if no_show_time > datetime.now():
        scheduler.add_job(
            _send_no_show_followup,
            trigger=DateTrigger(run_date=no_show_time),
            args=[appointment],
            id=f"noshow_{appointment['id']}",
            replace_existing=True,
        )

        voice_followup_time = no_show_time + timedelta(hours=3)
        scheduler.add_job(
            _send_voice_followup,
            trigger=DateTrigger(run_date=voice_followup_time),
            args=[appointment["phone"], appointment["patient_name"], appointment["id"]],
            id=f"voice_followup_{appointment['id']}",
            replace_existing=True,
        )


def cancel_scheduled_jobs(appointment_id: int):
    job_ids = [
        f"reminder_24h_{appointment_id}",
        f"reminder_2h_{appointment_id}",
        f"noshow_{appointment_id}",
        f"voice_followup_{appointment_id}",
    ]
    for job_id in job_ids:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass


async def run_daily_reminder_scan():
    logger.info("Running daily reminder scan...")
    appointments = get_upcoming_appointments()

    for appt in appointments:
        try:
            appt_datetime = datetime.strptime(
                f"{appt['date']} {appt['time']}", "%Y-%m-%d %H:%M"
            )

            now = datetime.now()

            if appt_datetime - timedelta(hours=24) > now:
                schedule_appointment_reminders(appt)

            if appt_datetime + timedelta(minutes=45) > now:
                schedule_no_show_check(appt)

        except Exception as e:
            logger.error(f"Error scheduling reminders for appointment {appt.get('id')}: {e}")

    logger.info(f"Daily scan complete. Processed {len(appointments)} appointments.")


async def run_no_show_scan():
    unconfirmed = get_past_unconfirmed(minutes_ago=45)
    for appt in unconfirmed:
        try:
            await _send_no_show_followup(appt)
        except Exception as e:
            logger.error(f"Error in no-show scan for appointment {appt.get('id')}: {e}")

async def send_daily_summary():
    """Send morning summary of today's appointments to the doctor."""
    from backend.whatsapp_sender import send_text

    today = datetime.now().strftime("%Y-%m-%d")
    appointments = get_appointments(today)

    if not appointments:
        return

    booked = [a for a in appointments if a["status"] in ("booked", "confirmed")]
    checked_in = [a for a in appointments if a["status"] == "checked_in"]

    summary = f"Good morning! Today's summary ({today}):\n"
    summary += f"Total appointments: {len(booked) + len(checked_in)}\n"
    if booked:
        summary += "\nUpcoming:\n"
        for a in booked:
            summary += f"  {a['time']} - {a['patient_name']} ({a.get('reason', 'consultation')})\n"
    if checked_in:
        summary += f"\nAlready checked in: {len(checked_in)}\n"

    try:
        doctor_phone = settings.DOCTOR_PHONE
        if doctor_phone:
            await send_text(doctor_phone, summary)
            logger.info(f"Daily summary sent to doctor ({len(appointments)} appointments)")
    except Exception as e:
        logger.error(f"Failed to send daily summary: {e}")


async def cleanup_audio_cache():
    logger.info("Running audio cache cleanup...")
    cache_dir = settings.AUDIO_CACHE_DIR
    if not os.path.exists(cache_dir):
        return

    now = time.time()
    cutoff = now - (7 * 86400) # 7 days
    
    deleted_count = 0
    for ext in ("*.wav", "*.ogg"):
        for file_path in glob.glob(os.path.join(cache_dir, ext)):
            try:
                if os.path.isfile(file_path) and os.stat(file_path).st_mtime < cutoff:
                    os.remove(file_path)
                    deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting cached file {file_path}: {e}")
                
    logger.info(f"Audio cache cleanup complete. Deleted {deleted_count} files.")


def init_scheduler():
    # Ensure DB directory exists for the SQLiteJobStore
    os.makedirs("database", exist_ok=True)
    
    scheduler.add_job(
        run_daily_reminder_scan,
        trigger=CronTrigger(hour=8, minute=0),
        id="daily_reminder_scan",
        replace_existing=True,
    )

    scheduler.add_job(
        run_no_show_scan,
        trigger=CronTrigger(minute="*/30"),
        id="no_show_scan",
        replace_existing=True,
    )

    scheduler.add_job(
        cleanup_audio_cache,
        trigger=CronTrigger(hour=3, minute=0), # Run daily at 3 AM
        id="cleanup_audio_cache",
        replace_existing=True,
    )

    scheduler.add_job(
        send_daily_summary,
        trigger=CronTrigger(hour=8, minute=30), # Daily at 8:30 AM
        id="daily_summary",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started")

