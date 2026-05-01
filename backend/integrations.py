import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.database import get_all_appointments, init_db


BOOKING_COLUMNS = [
    "Appointment ID",
    "Created At",
    "Updated At",
    "Status",
    "Patient Name",
    "Phone",
    "Date",
    "Time",
    "Reason",
    "Patient Age",
    "ID Card",
    "Appointment Details",
    "Patient Record Age",
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
            _stringify(appt.get("date")),
            _stringify(appt.get("time")),
            _stringify(appt.get("reason")),
            _stringify(appt.get("patient_age")),
            _stringify(appt.get("id_card")),
            _stringify(appt.get("details_json")),
            _stringify(appt.get("patient_record_age")),
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
    sheet_url: Optional[str] = None,
    credentials_path: Optional[str] = None,
) -> dict:
    sheet_url = sheet_url or settings.GOOGLE_SHEET_URL
    credentials_path = credentials_path or settings.GOOGLE_SERVICE_ACCOUNT_JSON

    if not sheet_url:
        raise ValueError("Google Sheet URL is missing.")
    if not credentials_path:
        raise ValueError("Google service account JSON path is missing.")
    if not os.path.exists(credentials_path):
        raise FileNotFoundError(f"Google service account file not found: {credentials_path}")

    try:
        import gspread
    except ImportError as exc:
        raise RuntimeError("gspread and google-auth are required. Run: pip install -r requirements.txt") from exc

    client = gspread.service_account(filename=credentials_path)
    spreadsheet = client.open_by_url(sheet_url)
    worksheet = spreadsheet.sheet1  # Use the first worksheet

    # Get existing phone+date+time combos to avoid duplicates
    existing_rows = worksheet.get_all_values()
    existing_keys = set()
    for row in existing_rows[1:]:  # skip header
        if len(row) >= 5:
            existing_keys.add((row[1], row[3], row[4]))  # (Phone, Date, Time)

    # Map our appointments to the user's column format:
    # Name | Phone Number | Email | Date | Time | Reason | Coupon Code | Submitted At | Calendar Status | WhatsApp
    init_db()
    new_rows = []
    for appt in get_all_appointments():
        key = (_stringify(appt.get("phone")), _stringify(appt.get("date")), _stringify(appt.get("time")))
        if key in existing_keys:
            continue
        new_rows.append([
            _stringify(appt.get("patient_name")),
            _stringify(appt.get("phone")),
            "",  # Email — not collected via WhatsApp
            _stringify(appt.get("date")),
            _stringify(appt.get("time")),
            _stringify(appt.get("reason")),
            "",  # Coupon Code — not applicable
            _stringify(appt.get("created_at")),
            _stringify(appt.get("status")),
            "WhatsApp",
        ])

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")

    return {
        "spreadsheet": spreadsheet.title,
        "worksheet": worksheet.title,
        "rows_synced": len(new_rows),
    }

