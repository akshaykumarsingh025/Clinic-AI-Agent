import sqlite3
import os
import json
from typing import Any, Optional
from backend.config import settings

DB_PATH = settings.DB_PATH


def _ensure_db_dir():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)


def get_db() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _json_or_none(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def init_db():
    _ensure_db_dir()
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            name TEXT,
            age TEXT,
            location TEXT,
            id_card TEXT,
            extra_details_json TEXT,
            id_card_image_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER REFERENCES patients(id),
            phone TEXT NOT NULL,
            patient_name TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            reason TEXT,
            patient_age TEXT,
            patient_location TEXT,
            consultation_type TEXT,
            id_card TEXT,
            details_json TEXT,
            status TEXT DEFAULT 'booked',
            payment_status TEXT DEFAULT 'pending',
            payment_screenshot_path TEXT,
            reports_data_json TEXT,
            id_card_image_path TEXT,
            reminder_sent INTEGER DEFAULT 0,
            followup_sent INTEGER DEFAULT 0,
            followup_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS blocked_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT,
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS patient_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            appointment_id INTEGER,
            document_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            extracted_data_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date);
        CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments(phone);
        CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
        CREATE INDEX IF NOT EXISTS idx_conversations_phone ON conversations(phone);
        CREATE INDEX IF NOT EXISTS idx_patient_documents_phone ON patient_documents(phone);
    """)

    _ensure_column(conn, "patients", "age", "TEXT")
    _ensure_column(conn, "patients", "location", "TEXT")
    _ensure_column(conn, "patients", "id_card", "TEXT")
    _ensure_column(conn, "patients", "extra_details_json", "TEXT")
    _ensure_column(conn, "patients", "id_card_image_path", "TEXT")
    _ensure_column(conn, "appointments", "patient_age", "TEXT")
    _ensure_column(conn, "appointments", "patient_location", "TEXT")
    _ensure_column(conn, "appointments", "consultation_type", "TEXT")
    _ensure_column(conn, "appointments", "id_card", "TEXT")
    _ensure_column(conn, "appointments", "details_json", "TEXT")
    _ensure_column(conn, "appointments", "id_card_image_path", "TEXT")
    _ensure_column(conn, "appointments", "payment_status", "TEXT DEFAULT 'pending'")
    _ensure_column(conn, "appointments", "payment_screenshot_path", "TEXT")
    _ensure_column(conn, "appointments", "reports_data_json", "TEXT")

    conn.commit()
    conn.close()


def get_patient(phone: str) -> Optional[dict]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM patients WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_patient(
    phone: str,
    name: Optional[str] = None,
    age: Optional[str] = None,
    location: Optional[str] = None,
    id_card: Optional[str] = None,
    extra_details: Any = None,
    id_card_image_path: Optional[str] = None,
) -> dict:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO patients (phone, name, age, location, id_card, extra_details_json, id_card_image_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (phone, name, age, location, id_card, _json_or_none(extra_details), id_card_image_path),
    )
    if name:
        conn.execute("UPDATE patients SET name = ? WHERE phone = ? AND (name IS NULL OR name = '')", (name, phone))
    if age:
        conn.execute("UPDATE patients SET age = ? WHERE phone = ? AND (age IS NULL OR age = '')", (age, phone))
    if location:
        conn.execute("UPDATE patients SET location = ? WHERE phone = ? AND (location IS NULL OR location = '')", (location, phone))
    if id_card:
        conn.execute("UPDATE patients SET id_card = ? WHERE phone = ? AND (id_card IS NULL OR id_card = '')", (id_card, phone))
    if id_card_image_path:
        conn.execute("UPDATE patients SET id_card_image_path = ? WHERE phone = ? AND (id_card_image_path IS NULL OR id_card_image_path = '')", (id_card_image_path, phone))
    if extra_details:
        conn.execute(
            "UPDATE patients SET extra_details_json = COALESCE(extra_details_json, ?) WHERE phone = ?",
            (_json_or_none(extra_details), phone),
        )
    row = conn.execute("SELECT * FROM patients WHERE phone = ?", (phone,)).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def update_patient_location(phone: str, location: str):
    conn = get_db()
    try:
        conn.execute("UPDATE patients SET location = ? WHERE phone = ?", (location, phone))
        conn.commit()
    finally:
        conn.close()


def get_appointments(date: str) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM appointments WHERE date = ? ORDER BY time",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_appointments() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT
            a.*,
            p.age AS patient_record_age,
            p.location AS patient_record_location,
            p.id_card AS patient_record_id_card,
            p.extra_details_json AS patient_record_details_json
        FROM appointments a
        LEFT JOIN patients p ON p.id = a.patient_id
        ORDER BY a.date DESC, a.time DESC, a.id DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_appointment(
    phone: str,
    name: str,
    date: str,
    time: str,
    reason: Optional[str] = None,
    patient_age: Optional[str] = None,
    patient_location: Optional[str] = None,
    consultation_type: Optional[str] = None,
    id_card: Optional[str] = None,
    details: Any = None,
    id_card_image_path: Optional[str] = None,
    payment_status: Optional[str] = None,
    payment_screenshot_path: Optional[str] = None,
    reports_data: Any = None,
) -> int:
    conn = get_db()
    patient = get_patient(phone)
    if not patient:
        create_patient(phone, name, patient_age, patient_location, id_card, details, id_card_image_path)
    else:
        if name and (not patient.get("name") or patient["name"] == ""):
            conn.execute("UPDATE patients SET name = ? WHERE phone = ?", (name, phone))
        if patient_age and (not patient.get("age") or patient["age"] == ""):
            conn.execute("UPDATE patients SET age = ? WHERE phone = ?", (patient_age, phone))
        if patient_location and (not patient.get("location") or patient["location"] == ""):
            conn.execute("UPDATE patients SET location = ? WHERE phone = ?", (patient_location, phone))
        if id_card and (not patient.get("id_card") or patient["id_card"] == ""):
            conn.execute("UPDATE patients SET id_card = ? WHERE phone = ?", (id_card, phone))
        if id_card_image_path and (not patient.get("id_card_image_path") or patient["id_card_image_path"] == ""):
            conn.execute("UPDATE patients SET id_card_image_path = ? WHERE phone = ?", (id_card_image_path, phone))
        if details and (not patient.get("extra_details_json") or patient["extra_details_json"] == ""):
            conn.execute("UPDATE patients SET extra_details_json = ? WHERE phone = ?", (_json_or_none(details), phone))

    patient = get_patient(phone)
    patient_id = patient["id"]

    cursor = conn.execute(
        """
        INSERT INTO appointments
        (patient_id, phone, patient_name, date, time, reason, patient_age, patient_location, consultation_type, id_card, details_json, id_card_image_path, payment_status, payment_screenshot_path, reports_data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (patient_id, phone, name, date, time, reason, patient_age, patient_location, consultation_type, id_card, _json_or_none(details), id_card_image_path, payment_status or "pending", payment_screenshot_path, _json_or_none(reports_data)),
    )
    appointment_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return appointment_id


def update_appointment_status(appointment_id: int, status: str):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE appointments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, appointment_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_appointment_payment(appointment_id: int, payment_status: str, payment_screenshot_path: Optional[str] = None):
    conn = get_db()
    try:
        if payment_screenshot_path:
            conn.execute(
                "UPDATE appointments SET payment_status = ?, payment_screenshot_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (payment_status, payment_screenshot_path, appointment_id),
            )
        else:
            conn.execute(
                "UPDATE appointments SET payment_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (payment_status, appointment_id),
            )
        conn.commit()
    finally:
        conn.close()


def update_appointment_reports(appointment_id: int, reports_data: Any):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE appointments SET reports_data_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (_json_or_none(reports_data), appointment_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_patient_document(
    phone: str,
    document_type: str,
    file_path: str,
    extracted_data: Any = None,
    appointment_id: Optional[int] = None,
) -> int:
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO patient_documents (phone, appointment_id, document_type, file_path, extracted_data_json) VALUES (?, ?, ?, ?, ?)",
            (phone, appointment_id, document_type, file_path, _json_or_none(extracted_data)),
        )
        doc_id = cursor.lastrowid
        conn.commit()
        return doc_id
    finally:
        conn.close()


def get_patient_documents(phone: str, document_type: Optional[str] = None) -> list[dict]:
    conn = get_db()
    try:
        if document_type:
            rows = conn.execute(
                "SELECT * FROM patient_documents WHERE phone = ? AND document_type = ? ORDER BY created_at DESC",
                (phone, document_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM patient_documents WHERE phone = ? ORDER BY created_at DESC",
                (phone,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_upcoming_appointments() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM appointments WHERE status IN ('booked', 'confirmed') AND date >= date('now') ORDER BY date, time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_past_unconfirmed(minutes_ago: int = 45) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM appointments
        WHERE status = 'booked'
        AND datetime(date || ' ' || time || ':00') < datetime('now', '-' || ? || ' minutes')
        AND date >= date('now', '-1 day')
        """,
        (minutes_ago,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_conversation(phone: str, role: str, content: str):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO conversations (phone, role, content) VALUES (?, ?, ?)",
            (phone, role, content),
        )
        conn.commit()
    finally:
        conn.close()


def get_conversation_history(phone: str, limit: int = 10) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT role, content FROM conversations WHERE phone = ? ORDER BY id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def clear_old_conversations(phone: str):
    conn = get_db()
    conn.execute(
        "DELETE FROM conversations WHERE phone = ? AND id NOT IN (SELECT id FROM conversations WHERE phone = ? ORDER BY id DESC LIMIT 2)",
        (phone, phone),
    )
    conn.commit()
    conn.close()


def get_patient_appointments(phone: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM appointments WHERE phone = ? ORDER BY date DESC, time DESC",
        (phone,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_appointment_by_id(appointment_id: int) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def block_slot(date: str, time: Optional[str] = None, reason: Optional[str] = None):
    conn = get_db()
    conn.execute(
        "INSERT INTO blocked_slots (date, time, reason) VALUES (?, ?, ?)",
        (date, time, reason),
    )
    conn.commit()
    conn.close()


def get_blocked_slots(date: str) -> list[str]:
    conn = get_db()
    rows = conn.execute(
        "SELECT time FROM blocked_slots WHERE date = ? AND time IS NOT NULL",
        (date,),
    ).fetchall()
    conn.close()
    return [r["time"] for r in rows]


def is_day_blocked(date: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM blocked_slots WHERE date = ? AND time IS NULL",
        (date,),
    ).fetchone()
    conn.close()
    return row is not None


def get_no_show_stats() -> dict:
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM appointments WHERE status != 'cancelled'").fetchone()["c"]
    no_shows = conn.execute("SELECT COUNT(*) as c FROM appointments WHERE status = 'no_show'").fetchone()["c"]

    reason_rows = conn.execute(
        "SELECT followup_response, COUNT(*) as count FROM appointments WHERE status = 'no_show' AND followup_response IS NOT NULL GROUP BY followup_response"
    ).fetchall()

    recent = conn.execute(
        "SELECT * FROM appointments WHERE status = 'no_show' ORDER BY updated_at DESC LIMIT 20"
    ).fetchall()

    conn.close()
    return {
        "total_appointments": total,
        "total_no_shows": no_shows,
        "no_show_rate": round(no_shows / max(total, 1) * 100, 1),
        "reasons_breakdown": {r["followup_response"]: r["count"] for r in reason_rows},
        "recent_no_shows": [dict(r) for r in recent],
    }


def mark_reminder_sent(appointment_id: int):
    conn = get_db()
    conn.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()


def mark_followup_sent(appointment_id: int):
    conn = get_db()
    conn.execute("UPDATE appointments SET followup_sent = 1 WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()


def update_followup_response(appointment_id: int, response: str):
    conn = get_db()
    conn.execute(
        "UPDATE appointments SET followup_response = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (response, appointment_id),
    )
    conn.commit()
    conn.close()
