from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class WebhookMessage(BaseModel):
    phone: str
    message_text: Optional[str] = None
    audio_path: Optional[str] = None
    image_path: Optional[str] = None


class ButtonReply(BaseModel):
    phone: str
    button_number: int


class BlockSlotRequest(BaseModel):
    date: str
    time: Optional[str] = None
    reason: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    phone: str
    patient_name: str
    date: str
    time: str
    reason: Optional[str] = None
    patient_age: Optional[str] = None
    id_card: Optional[str] = None
    details_json: Optional[str] = None
    status: str
    reminder_sent: bool
    followup_sent: bool
    followup_response: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AIResponse(BaseModel):
    intent: str
    patient_name: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    time_preference: Optional[str] = None
    reason: Optional[str] = None
    patient_age: Optional[str] = None
    patient_location: Optional[str] = None
    consultation_type: Optional[str] = None
    id_card: Optional[str] = None
    patient_details: Optional[dict] = None
    needs_more_info: bool = False
    booking_ready: bool = False
    payment_pending: bool = False
    no_show_response_type: Optional[str] = None
    language: str = "english"
    reply: str


class GoogleSheetSyncRequest(BaseModel):
    sheet_id: Optional[str] = None
    credentials_path: Optional[str] = None
    worksheet_gid: Optional[int] = None
