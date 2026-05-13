import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.database import get_all_appointments, init_db

logger = logging.getLogger(__name__)


BOOKING_COLUMNS = [
    "Appointment ID",
    "Created At",
    "Updated At",
    "Status",
    "Patient Name",
    "Phone",
    "Location",
    "Date",
    "Time",
    "Reason",
    "Consultation Type",
    "Patient Age",
    "ID Card",
    "Payment Status",
    "Appointment Details",
    "Patient Record Age",
    "Patient Record Location",
    "Patient Record ID Card",
    "Patient Record Details",
    "Reminder Sent",
    "Followup Sent",
    "Followup Response",
]


def _stringify(value) -> str:
    if value is None:
        return ""
    return str(value)


def appointment_rows() -> list[list[str]]:
    init_db()
    rows = []
    for appt in get_all_appointments():
        rows.append([
            _stringify(appt.get("id")),
            _stringify(appt.get("created_at")),
            _stringify(appt.get("updated_at")),
            _stringify(appt.get("status")),
            _stringify(appt.get("patient_name")),
            _stringify(appt.get("phone")),
            _stringify(appt.get("patient_location") or appt.get("patient_record_location", "")),
            _stringify(appt.get("date")),
            _stringify(appt.get("time")),
            _stringify(appt.get("reason")),
            _stringify(appt.get("consultation_type", "")),
            _stringify(appt.get("patient_age")),
            _stringify(appt.get("id_card")),
            _stringify(appt.get("payment_status", "")),
            _stringify(appt.get("details_json")),
            _stringify(appt.get("patient_record_age")),
            _stringify(appt.get("patient_record_location", "")),
            _stringify(appt.get("patient_record_id_card")),
            _stringify(appt.get("patient_record_details_json")),
            _stringify(appt.get("reminder_sent")),
            _stringify(appt.get("followup_sent")),
            _stringify(appt.get("followup_response")),
        ])
    return rows


def export_appointments_xlsx(output_path: Optional[str] = None) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("openpyxl is required for Excel export. Run: pip install -r requirements.txt") from exc

    export_dir = Path(settings.EXPORT_DIR)
    export_dir.mkdir(parents=True, exist_ok=True)
    if not output_path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(export_dir / f"appointments_{stamp}.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "Appointments"
    ws.append(BOOKING_COLUMNS)

    header_fill = PatternFill("solid", fgColor="E8F0FE")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill

    rows = appointment_rows()
    for row in rows:
        ws.append(row)

    ws.freeze_panes = "A2"
    for idx, column in enumerate(BOOKING_COLUMNS, 1):
        values = [column]
        values.extend(_stringify(row[idx - 1]) for row in rows)
        width = min(max(len(value) for value in values) + 2, 60)
        ws.column_dimensions[get_column_letter(idx)].width = width

    wb.save(output_path)
    return os.path.abspath(output_path)


def sync_appointments_to_google_sheet(
    sheet_id: Optional[str] = None,
    credentials_path: Optional[str] = None,
    worksheet_gid: Optional[int] = None,
) -> dict:
    sheet_id = sheet_id or settings.GOOGLE_SHEET_ID
    credentials_path = credentials_path or settings.GOOGLE_SERVICE_ACCOUNT_JSON

    if not sheet_id:
        raise ValueError("Google Sheet ID is missing.")
    if not credentials_path:
        raise ValueError("Google service account JSON path is missing.")
    if not os.path.exists(credentials_path):
        raise FileNotFoundError(f"Google service account file not found: {credentials_path}")

    try:
        import gspread
    except ImportError as exc:
        raise RuntimeError("gspread and google-auth are required. Run: pip install -r requirements.txt") from exc

    client = gspread.service_account(filename=credentials_path)
    spreadsheet = client.open_by_key(sheet_id)

    gid = worksheet_gid if worksheet_gid is not None else settings.GOOGLE_SHEET_GID
    if gid is not None:
        worksheet = spreadsheet.get_worksheet_by_id(gid)
    else:
        worksheet = spreadsheet.sheet1

    if worksheet is None:
        worksheet = spreadsheet.sheet1

    existing_rows = worksheet.get_all_values()
    existing_keys = set()
    for row in existing_rows[1:]:
        if len(row) >= 5:
            existing_keys.add((row[1], row[3], row[4]))

    status_map = {
        "booked": "Created",
        "confirmed": "Confirmed",
        "cancelled": "Cancelled",
        "rescheduled": "Rescheduled",
        "no_show": "No Show",
        "checked_in": "Checked In",
    }

    init_db()
    new_rows = []
    for appt in get_all_appointments():
        key = (_stringify(appt.get("phone")), _stringify(appt.get("date")), _stringify(appt.get("time")))
        if key in existing_keys:
            continue

        contact_number = ""
        details_json = appt.get("details_json")
        if details_json:
            try:
                import json
                parsed = json.loads(details_json)
                contact_number = parsed.get("contact_number", "")
            except Exception:
                pass

        display_phone = contact_number if contact_number else _stringify(appt.get("phone"))

        raw_status = _stringify(appt.get("status"))
        calendar_status = status_map.get(raw_status, raw_status)

        id_card_val = _stringify(appt.get("id_card"))
        id_card_image = appt.get("id_card_image_path")
        if id_card_image:
            id_card_display = "ID image on file"
        elif id_card_val:
            id_card_display = id_card_val
        else:
            id_card_display = ""

        id_photo_url = ""
        if id_card_image and os.path.exists(str(id_card_image)):
            id_photo_url = _upload_id_image_to_drive(
                str(id_card_image), credentials_path,
                _stringify(appt.get("patient_name", "Patient"))
            )

        new_rows.append([
            _stringify(appt.get("patient_name")),
            display_phone,
            _stringify(appt.get("patient_location") or appt.get("patient_record_location", "")),
            _stringify(appt.get("date")),
            _stringify(appt.get("time")),
            _stringify(appt.get("reason")),
            _stringify(appt.get("consultation_type", "")),
            _stringify(appt.get("created_at")),
            calendar_status,
            "WhatsApp",
            id_card_display,
            id_photo_url,
            _stringify(appt.get("payment_status", "")),
        ])

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")

    return {
        "spreadsheet": spreadsheet.title,
        "worksheet": worksheet.title,
        "rows_synced": len(new_rows),
    }


def _upload_id_image_to_drive(image_path: str, credentials_path: str, patient_name: str) -> str:
    if not image_path or not os.path.exists(image_path):
        return ""

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        logger.warning("google-api-python-client required for image upload")
        return ""

    try:
        SCOPES = ['https://www.googleapis.com/auth/drive.file']
        creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)

        filename = f"ID_{patient_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{Path(image_path).suffix}"

        file_metadata = {
            'name': filename,
            'mimeType': 'application/vnd.google-apps.photo',
        }

        media = MediaFileUpload(image_path, mimetype='image/jpeg', resumable=True)
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
        ).execute()

        file_id = uploaded.get('id')
        if not file_id:
            return ""

        service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'},
        ).execute()

        image_url = f"https://drive.google.com/uc?id={file_id}"
        logger.info(f"Uploaded ID image for {patient_name}: {image_url}")
        return image_url

    except Exception as e:
        logger.error(f"Failed to upload ID image: {e}")
        return ""


def sync_appointment_to_google_calendar(appointment: dict) -> dict:
    calendar_id = settings.GOOGLE_CALENDAR_ID
    credentials_path = settings.GOOGLE_SERVICE_ACCOUNT_JSON

    if not calendar_id or calendar_id == "primary":
        calendar_id = "primary"

    if not credentials_path or not os.path.exists(credentials_path):
        logger.warning("Google Calendar Sync: Credentials not found.")
        return {}

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        logger.error("google-api-python-client is required. Run: pip install -r requirements.txt")
        return {}

    SCOPES = ['https://www.googleapis.com/auth/calendar.events']
    
    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)

        date_str = appointment.get("date")
        time_str = appointment.get("time")
        
        # Calculate start and end times
        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=settings.SLOT_DURATION_MINUTES)

        timezone = settings.CLINIC_TIMEZONE
        
        name = appointment.get("patient_name", "Patient")
        reason = appointment.get("reason", "No reason provided")

        contact_number = ""
        details_json = appointment.get("details_json")
        if details_json:
            try:
                import json
                parsed = json.loads(details_json)
                contact_number = parsed.get("contact_number", "")
            except:
                pass

        display_phone = contact_number if contact_number else appointment.get("phone", "")

        event = {
            'summary': f'Appointment: {name}',
            'description': f'Phone: {display_phone}\nReason: {reason}',
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': timezone,
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': timezone,
            },
        }

        event_result = service.events().insert(calendarId=calendar_id, body=event).execute()
        
        logger.info(f"Successfully synced appointment {appointment.get('id')} to Google Calendar.")
        return {
            "status": "success",
            "event_link": event_result.get('htmlLink'),
            "event_id": event_result.get('id')
        }

    except Exception as e:
        logger.error(f"Failed to sync appointment to Google Calendar: {e}")
        return {"status": "error", "message": str(e)}

