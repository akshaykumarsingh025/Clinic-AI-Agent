import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    CLINIC_NAME: str = os.getenv("CLINIC_NAME", "Dr. Deepika Singh Clinic")
    DOCTOR_NAME: str = os.getenv("DOCTOR_NAME", "Dr. Deepika Singh")
    CLINIC_SPECIALTY: str = os.getenv("CLINIC_SPECIALTY", "Gynecologist")
    CLINIC_ADDRESS: str = os.getenv("CLINIC_ADDRESS", "South Delhi")
    CLINIC_PHONE: str = os.getenv("CLINIC_PHONE", "+91XXXXXXXXXX")

    SLOT_DURATION_MINUTES: int = int(os.getenv("SLOT_DURATION_MINUTES", "20"))
    MORNING_START: str = os.getenv("MORNING_START", "10:00")
    MORNING_END: str = os.getenv("MORNING_END", "13:00")
    EVENING_START: str = os.getenv("EVENING_START", "17:00")
    EVENING_END: str = os.getenv("EVENING_END", "20:00")
    WORKING_DAYS: list[str] = os.getenv("WORKING_DAYS", "Mon,Tue,Wed,Thu,Fri,Sat").split(",")

    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma3:4b")
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    PIPER_BINARY: str = os.getenv("PIPER_BINARY", "piper")
    PIPER_VOICE: str = os.getenv("PIPER_VOICE", "./voices/en_IN-female-medium.onnx")
    AUDIO_CACHE_DIR: str = os.getenv("AUDIO_CACHE_DIR", "./audio_cache")

    WHATSAPP_BOT_URL: str = os.getenv("WHATSAPP_BOT_URL", "http://localhost:3001")
    FASTAPI_HOST: str = os.getenv("FASTAPI_HOST", "0.0.0.0")
    FASTAPI_PORT: int = int(os.getenv("FASTAPI_PORT", "8000"))

    DB_PATH: str = os.getenv("DB_PATH", "./database/clinic.db")

    @property
    def WORKING_DAY_INDICES(self) -> set[int]:
        day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        return {day_map[d] for d in self.WORKING_DAYS if d in day_map}


settings = Settings()
