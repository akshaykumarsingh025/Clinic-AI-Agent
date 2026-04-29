from datetime import datetime, timedelta
from typing import Optional

from backend.config import settings
from backend.database import (
    create_appointment,
    get_appointment_by_id,
    update_appointment_status,
    get_patient_appointments,
    get_appointments,
    is_day_blocked,
    get_blocked_slots,
)


def generate_all_slots(date: str) -> list[str]:
    date_obj = datetime.strptime(date, "%Y-%m-%d")

    if date_obj.weekday() not in settings.WORKING_DAY_INDICES:
        return []

    if is_day_blocked(date):
        return []

    slots = []

    morning_start = datetime.strptime(settings.MORNING_START, "%H:%M")
    morning_end = datetime.strptime(settings.MORNING_END, "%H:%M")
    current = morning_start
    while current < morning_end:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=settings.SLOT_DURATION_MINUTES)

    evening_start = datetime.strptime(settings.EVENING_START, "%H:%M")
    evening_end = datetime.strptime(settings.EVENING_END, "%H:%M")
    current = evening_start
    while current < evening_end:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=settings.SLOT_DURATION_MINUTES)

    blocked = get_blocked_slots(date)
    slots = [s for s in slots if s not in blocked]

    return slots


def get_booked_slots(date: str) -> list[str]:
    appointments = get_appointments(date)
    return [
        a["time"]
        for a in appointments
        if a["status"] in ("booked", "confirmed")
    ]


def get_available_slots(date: str) -> list[str]:
    all_slots = generate_all_slots(date)
    booked = get_booked_slots(date)
    return [s for s in all_slots if s not in booked]


def find_best_slot(date: str, preference: Optional[str] = None) -> Optional[str]:
    available = get_available_slots(date)
    if not available:
        return None

    if preference == "morning":
        morning = [s for s in available if s < "13:00"]
        return morning[0] if morning else available[0]
    elif preference == "evening":
        evening = [s for s in available if s >= "13:00"]
        return evening[0] if evening else available[0]

    return available[0]


def find_next_available_date(from_date: str, preference: Optional[str] = None) -> Optional[tuple[str, str]]:
    date_obj = datetime.strptime(from_date, "%Y-%m-%d")

    for i in range(1, 31):
        check_date = date_obj + timedelta(days=i)
        date_str = check_date.strftime("%Y-%m-%d")

        if check_date.weekday() not in settings.WORKING_DAY_INDICES:
            continue

        slot = find_best_slot(date_str, preference)
        if slot:
            return (date_str, slot)

    return None


def book_appointment(phone: str, name: str, date: str, time: str, reason: Optional[str] = None) -> dict:
    if check_slot_conflict(date, time):
        raise ValueError(f"Slot {time} on {date} is already booked")

    available = get_available_slots(date)
    if time not in available:
        raise ValueError(f"Slot {time} on {date} is not available")

    appointment_id = create_appointment(phone, name, date, time, reason)
    appointment = get_appointment_by_id(appointment_id)
    return appointment


def cancel_appointment(phone: str, date: Optional[str] = None) -> bool:
    appointments = get_patient_appointments(phone)
    active = [a for a in appointments if a["status"] in ("booked", "confirmed")]

    if date:
        active = [a for a in active if a["date"] == date]

    if not active:
        return False

    for a in active:
        update_appointment_status(a["id"], "cancelled")

    return True


def reschedule_appointment(appointment_id: int, new_date: str, new_time: str) -> bool:
    appointment = get_appointment_by_id(appointment_id)
    if not appointment:
        return False

    if appointment["status"] not in ("booked", "confirmed"):
        return False

    if check_slot_conflict(new_date, new_time):
        return False

    update_appointment_status(appointment_id, "rescheduled")

    new_id = create_appointment(
        appointment["phone"],
        appointment["patient_name"],
        new_date,
        new_time,
        appointment.get("reason"),
    )
    return True


def get_patient_appointments_list(phone: str) -> list[dict]:
    return get_patient_appointments(phone)


def check_slot_conflict(date: str, time: str) -> bool:
    booked = get_booked_slots(date)
    return time in booked


def format_appointment_confirmation(appt: dict) -> str:
    date_obj = datetime.strptime(appt["date"], "%Y-%m-%d")
    day_name = date_obj.strftime("%A")
    formatted_date = date_obj.strftime("%d %B %Y")

    time_obj = datetime.strptime(appt["time"], "%H:%M")
    formatted_time = time_obj.strftime("%I:%M %p")

    return (
        f"Appointment confirmed:\n"
        f"  Date: {day_name}, {formatted_date}\n"
        f"  Time: {formatted_time}\n"
        f"  Doctor: {settings.DOCTOR_NAME}\n"
        f"  Clinic: {settings.CLINIC_NAME}\n"
        f"  Address: {settings.CLINIC_ADDRESS}\n"
        f"You will receive a reminder before your appointment."
    )


def format_time_display(time_str: str) -> str:
    try:
        t = datetime.strptime(time_str, "%H:%M")
        return t.strftime("%I:%M %p")
    except ValueError:
        return time_str
