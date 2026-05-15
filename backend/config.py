import os
from pathlib import Path
from dotenv import load_dotenv, set_key

load_dotenv()
load_dotenv(Path(__file__).parent.parent / "googlekey" / ".env")

ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


class Settings:
    def __init__(self):
        self.CLINIC_NAME: str = os.getenv("CLINIC_NAME", "Dr. Deepika Singh Clinic")
        self.DOCTOR_NAME: str = os.getenv("DOCTOR_NAME", "Dr. Deepika Singh")
        self.CLINIC_SPECIALTY: str = os.getenv("CLINIC_SPECIALTY", "Gynecologist")
        self.CLINIC_ADDRESS: str = os.getenv("CLINIC_ADDRESS", "F-11, South Extension Part 1 New Delhi, 110049")
        self.CLINIC_PHONE: str = os.getenv("CLINIC_PHONE", "+91XXXXXXXXXX")
        self.APPOINTMENT_FEE: str = os.getenv("APPOINTMENT_FEE", "Please confirm with the clinic")
        self.CLINIC_TIMEZONE: str = os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata")

        self.SLOT_DURATION_MINUTES: int = int(os.getenv("SLOT_DURATION_MINUTES", "20"))
        self.MORNING_START: str = os.getenv("MORNING_START", "10:00")
        self.MORNING_END: str = os.getenv("MORNING_END", "13:00")
        self.EVENING_START: str = os.getenv("EVENING_START", "17:00")
        self.EVENING_END: str = os.getenv("EVENING_END", "20:00")
        self.WORKING_DAYS: list[str] = os.getenv("WORKING_DAYS", "Mon,Tue,Wed,Thu,Fri,Sat").split(",")

        self.OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
        self.OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

        self.PIPER_BINARY: str = os.getenv("PIPER_BINARY", "piper")
        self.PIPER_VOICE: str = os.getenv("PIPER_VOICE", "./voices/en_IN-female-medium.onnx")
        self.AUDIO_CACHE_DIR: str = os.getenv("AUDIO_CACHE_DIR", "./audio_cache")
        self.SEND_AUDIO_REPLIES_FOR_TEXT: bool = os.getenv("SEND_AUDIO_REPLIES_FOR_TEXT", "true").lower() in ("1", "true", "yes", "on")
        self.TTS_PROVIDER: str = os.getenv("TTS_PROVIDER", "auto")  # auto|piper|voiceclone|qwen3|chatterbox
        self.QWEN3_TTS_MODEL: str = os.getenv("QWEN3_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
        self.VOICECLONE_PROJECT_DIR: str = os.getenv("VOICECLONE_PROJECT_DIR", r"D:\Software\Projects\VoiceCloneReels")
        self.VOICECLONE_PYTHON: str = os.getenv("VOICECLONE_PYTHON", "")
        self.VOICECLONE_VOICE_SAMPLE: str = os.getenv("VOICECLONE_VOICE_SAMPLE", "")
        self.VOICECLONE_LANGUAGE: str = os.getenv("VOICECLONE_LANGUAGE", "auto")
        self.VOICECLONE_REF_TEXT: str = os.getenv("VOICECLONE_REF_TEXT", "")

        self.WHATSAPP_BOT_URL: str = os.getenv("WHATSAPP_BOT_URL", "http://localhost:3001")
        self.FASTAPI_HOST: str = os.getenv("FASTAPI_HOST", "0.0.0.0")
        self.FASTAPI_PORT: int = int(os.getenv("FASTAPI_PORT", "8000"))
        self.OLLAMA_VISION_MODEL: str = os.getenv("OLLAMA_VISION_MODEL", "")
        self.QR_CODE_PATH: str = os.getenv("QR_CODE_PATH", "./static/qr_code.png")
        self.DOCTOR_PHONE: str = os.getenv("DOCTOR_PHONE", "+918595954097")
        self.CLINIC_WHATSAPP: str = os.getenv("CLINIC_WHATSAPP", "+919871208803")

        self.DB_PATH: str = os.getenv("DB_PATH", "./database/clinic.db")
        self.EXPORT_DIR: str = os.getenv("EXPORT_DIR", "./exports")
        self.GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
        self.GOOGLE_SHEET_GID: int = int(os.getenv("GOOGLE_SHEET_GID", "0")) if os.getenv("GOOGLE_SHEET_GID", "") != "" else None
        self.GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "") or str(Path(__file__).parent.parent / "googlekey" / "service_account.json")
        self.GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self.GOOGLE_DRIVE_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

    @property
    def WORKING_DAY_INDICES(self) -> set[int]:
        day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        return {day_map[d] for d in self.WORKING_DAYS if d in day_map}
        
    def update_setting(self, key: str, value: str):
        if hasattr(self, key):
            if key == "WORKING_DAYS":
                setattr(self, key, value.split(","))
            elif key in ("SLOT_DURATION_MINUTES", "FASTAPI_PORT"):
                setattr(self, key, int(value))
            elif key == "SEND_AUDIO_REPLIES_FOR_TEXT":
                setattr(self, key, value.lower() in ("1", "true", "yes", "on"))
            else:
                setattr(self, key, value)
            set_key(ENV_FILE_PATH, key, value)


settings = Settings()
