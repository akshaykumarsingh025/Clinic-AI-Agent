import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from backend.config import settings


DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
DIGIT_TRANSLATION = str.maketrans("०१२३४५६७८९", "0123456789")

MONTH_ALIASES = {
    "jan": 1, "january": 1, "janvari": 1, "january": 1, "जनवरी": 1,
    "feb": 2, "february": 2, "farvari": 2, "फरवरी": 2,
    "mar": 3, "march": 3, "मार्च": 3,
    "apr": 4, "april": 4, "aprill": 4, "अप्रैल": 4, "अप्रेल": 4,
    "may": 5, "मई": 5,
    "jun": 6, "june": 6, "जून": 6,
    "jul": 7, "july": 7, "julai": 7, "जुलाई": 7,
    "aug": 8, "august": 8, "agast": 8, "अगस्त": 8,
    "sep": 9, "sept": 9, "september": 9, "sitambar": 9, "सितंबर": 9, "सितम्बर": 9,
    "oct": 10, "october": 10, "aktubar": 10, "अक्टूबर": 10,
    "nov": 11, "november": 11, "navambar": 11, "नवंबर": 11, "नवम्बर": 11,
    "dec": 12, "december": 12, "disambar": 12, "दिसंबर": 12, "दिसम्बर": 12,
}

WEEKDAY_ALIASES = {
    "monday": 0, "mon": 0, "somwar": 0, "somvaar": 0, "सोमवार": 0,
    "tuesday": 1, "tue": 1, "mangalwar": 1, "mangalvaar": 1, "मंगलवार": 1,
    "wednesday": 2, "wed": 2, "budhwar": 2, "budhvaar": 2, "बुधवार": 2,
    "thursday": 3, "thu": 3, "guruwar": 3, "guruvar": 3, "गुरुवार": 3,
    "friday": 4, "fri": 4, "shukrawar": 4, "shukrvar": 4, "शुक्रवार": 4,
    "saturday": 5, "sat": 5, "shanivar": 5, "sanivar": 5, "शनिवार": 5,
    "sunday": 6, "sun": 6, "ravivar": 6, "रविवार": 6,
}

HINDI_NUMBER_WORDS = {
    "ek": 1, "one": 1, "एक": 1,
    "do": 2, "two": 2, "दो": 2,
    "teen": 3, "tin": 3, "three": 3, "तीन": 3,
    "char": 4, "chaar": 4, "four": 4, "चार": 4,
    "panch": 5, "paanch": 5, "five": 5, "पांच": 5, "पाँच": 5,
    "che": 6, "chhe": 6, "six": 6, "छह": 6,
    "saat": 7, "seven": 7, "सात": 7,
    "aath": 8, "eight": 8, "आठ": 8,
    "nau": 9, "nine": 9, "नौ": 9,
    "das": 10, "ten": 10, "दस": 10,
    "gyarah": 11, "eleven": 11, "ग्यारह": 11,
    "barah": 12, "baarah": 12, "twelve": 12, "बारह": 12,
}

HINDI_HINTS = {
    "mujhe", "mera", "meri", "naam", "chahiye", "chahia", "chahie", "baje", "bje",
    "kal", "parso", "aaj", "doctor", "dikhana", "milna", "appointment", "fees",
    "फीस", "अपॉइंटमेंट", "डॉक्टर", "नाम", "आज", "कल", "परसों", "बजे",
}

ALLOWED_TOPIC_HINTS = {
    "appointment", "book", "booking", "slot", "available", "availability", "fee", "fees",
    "charge", "consultation", "doctor", "dr", "deepika", "clinic", "address", "location",
    "timing", "time", "cancel", "reschedule", "status", "gyno", "gynec", "gynecologist",
    "pregnancy", "period", "pcos", "fertility", "ivf", "scan", "ultrasound", "patient",
    "emergency", "pain", "bleeding", "labour", "delivery", "urgent", "problem", "dard",
    "report", "prescription", "medicine", "health", "checkup", "sick", "unwell",
    "अपॉइंटमेंट", "बुक", "स्लॉट", "फीस", "डॉक्टर", "दीपिका", "क्लिनिक", "पता",
    "समय", "रद्द", "गर्भ", "पीरियड", "नाम", "बजे", "कल", "परसों", "आज",
    "एमरजन्सी", "इमरजेंसी", "दर्द", "खून", "पेट", "बीमार", "समस्या", "जल्दी",
    "रिपोर्ट", "दवा", "जाँच", "दिखाना",
}

UNRELATED_TOPIC_HINTS = {
    "cricket", "movie", "song", "joke", "weather", "news", "politics", "stock", "share",
    "recipe", "homework", "coding", "code", "programming", "travel", "hotel", "flight",
    "astrology", "kundli", "lottery", "game", "bitcoin", "crypto",
}


def clinic_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(settings.CLINIC_TIMEZONE))
    except Exception:
        return datetime.now()


def _clean_text(text: str) -> str:
    return (text or "").translate(DIGIT_TRANSLATION).lower().strip()


def _has_word(text: str, aliases: set[str] | tuple[str, ...] | list[str]) -> bool:
    for alias in aliases:
        alias_l = alias.lower()
        if DEVANAGARI_RE.search(alias_l):
            if alias_l in text:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(alias_l)}(?![a-z0-9])", text):
            return True
    return False


def _coerce_year(year: Optional[str], month: int, day: int, today) -> tuple[int, bool]:
    explicit = bool(year)
    if year:
        value = int(year)
        if value < 100:
            value += 2000
        return value, explicit

    value = today.year
    try:
        candidate = datetime(value, month, day).date()
        if candidate < today:
            value += 1
    except ValueError:
        pass
    return value, explicit


def _try_build_date(day: int, month: int, year: Optional[str], today) -> Optional[str]:
    resolved_year, _ = _coerce_year(year, month, day, today)
    try:
        return datetime(resolved_year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_date_from_text(text: str, now: Optional[datetime] = None) -> Optional[str]:
    text_lower = _clean_text(text)
    if not text_lower:
        return None

    current = now or clinic_now()
    today = current.date()

    if _has_word(text_lower, ("day after tomorrow", "parso", "parson", "parsoh", "परसो", "परसों")):
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    if _has_word(text_lower, ("today", "aaj", "aj", "आज")):
        return today.strftime("%Y-%m-%d")
    if _has_word(text_lower, ("tomorrow", "tmrw", "kal", "कल")):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    month_names = sorted(MONTH_ALIASES, key=len, reverse=True)
    month_alt = "|".join(re.escape(m) for m in month_names)

    day_month = re.search(
        rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_alt})(?:\s*,?\s*(\d{{2,4}}))?",
        text_lower,
    )
    if day_month:
        day = int(day_month.group(1))
        month = MONTH_ALIASES[day_month.group(2)]
        parsed = _try_build_date(day, month, day_month.group(3), today)
        if parsed:
            return parsed

    month_day = re.search(
        rf"({month_alt})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{2,4}}))?",
        text_lower,
    )
    if month_day:
        month = MONTH_ALIASES[month_day.group(1)]
        day = int(month_day.group(2))
        parsed = _try_build_date(day, month, month_day.group(3), today)
        if parsed:
            return parsed

    numeric = re.search(r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})(?:\s*[./-]\s*(\d{2,4}))?(?!\d)", text_lower)
    if numeric:
        day = int(numeric.group(1))
        month = int(numeric.group(2))
        parsed = _try_build_date(day, month, numeric.group(3), today)
        if parsed:
            return parsed

    for day_name, day_idx in WEEKDAY_ALIASES.items():
        if _has_word(text_lower, (day_name,)):
            days_ahead = (day_idx - today.weekday()) % 7
            if days_ahead == 0 or _has_word(text_lower, ("next", "agle", "agla", "अगले", "अगला")):
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return None


def _normalize_hour(hour: int, minute: int, text_lower: str, suffix: str = "") -> Optional[str]:
    if minute < 0 or minute > 59:
        return None

    suffix = (suffix or "").lower()
    has_pm = suffix == "pm" or _has_word(text_lower, ("pm", "p.m", "shaam", "sham", "evening", "dopahar", "afternoon", "raat", "शाम", "दोपहर", "रात"))
    has_am = suffix == "am" or _has_word(text_lower, ("am", "a.m", "subah", "morning", "सुबह"))

    if hour > 23:
        return None

    if has_pm and hour < 12:
        hour += 12
    elif has_am and hour == 12:
        hour = 0
    elif not has_am and not has_pm and 1 <= hour <= 8:
        # In clinic booking chat, "1 baje" through "8 baje" usually means afternoon/evening.
        hour += 12

    if hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_time_from_text(text: str) -> Optional[str]:
    text_lower = _clean_text(text)
    if not text_lower:
        return None

    clock = re.search(r"(?<!\d)([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)\s*(am|pm)?(?!\d)", text_lower)
    if clock:
        return _normalize_hour(int(clock.group(1)), int(clock.group(2)), text_lower, clock.group(3) or "")

    suffix_time = re.search(r"(?<!\d)(1[0-2]|0?[1-9])\s*(am|pm)(?![a-z])", text_lower)
    if suffix_time:
        return _normalize_hour(int(suffix_time.group(1)), 0, text_lower, suffix_time.group(2))

    # Bare 3-4 digit times: 1240 → 12:40, 430 → 4:30, 1130 → 11:30
    bare_digits = re.search(r"(?<![a-z\d])(\d{3,4})(?:\s*(?:baje|bja|bje|bjey|baja|बजे))?(?![a-z\d])", text_lower)
    if bare_digits:
        digits = bare_digits.group(1)
        if len(digits) == 3:
            h, m = int(digits[0]), int(digits[1:])
        else:
            h, m = int(digits[:2]), int(digits[2:])
        if 0 <= m <= 59 and 0 <= h <= 23:
            result = _normalize_hour(h, m, text_lower)
            if result:
                return result

    baje_time = re.search(r"(?<!\d)(1[0-2]|0?[1-9])\s*(?:baje|bja|bje|bjey|baja|बजे)(?![a-z])", text_lower)
    if baje_time:
        return _normalize_hour(int(baje_time.group(1)), 0, text_lower)

    number_alt = "|".join(re.escape(k) for k in sorted(HINDI_NUMBER_WORDS, key=len, reverse=True))
    word_time = re.search(rf"(?<![a-z])({number_alt})\s*(?:baje|bja|bje|bjey|baja|बजे|o'clock)(?![a-z])", text_lower)
    if word_time:
        return _normalize_hour(HINDI_NUMBER_WORDS[word_time.group(1)], 0, text_lower)

    return None


def infer_time_preference(time_str: Optional[str]) -> Optional[str]:
    if not time_str:
        return None
    try:
        hour = int(time_str.split(":", 1)[0])
    except (ValueError, IndexError):
        return None
    if hour < 13:
        return "morning"
    if hour >= 17:
        return "evening"
    return None


def detect_language(text: str) -> str:
    text_lower = _clean_text(text)
    if DEVANAGARI_RE.search(text_lower):
        return "hinglish"
    if any(_has_word(text_lower, (hint,)) for hint in HINDI_HINTS):
        return "hinglish"
    return "english"


def is_probably_appointment_related(text: str) -> bool:
    text_lower = _clean_text(text)
    if not text_lower:
        return True
    if any(_has_word(text_lower, (hint,)) for hint in ALLOWED_TOPIC_HINTS):
        return True
    if any(_has_word(text_lower, (hint,)) for hint in UNRELATED_TOPIC_HINTS):
        return False
    if _has_word(text_lower, ("hi", "hello", "hey", "namaste", "नमस्ते")) and len(text_lower.split()) <= 4:
        return True
    return True


def appointment_scope_reply(language: str) -> str:
    if language == "hinglish":
        return "Main Dr. Deepika Singh Clinic ke appointments, slots, fees, timing, address aur booking help ke liye hoon. Appointment se related sawal batayein, main help kar dungi."
    return "I can help with Dr. Deepika Singh Clinic appointments, available slots, fees, timings, address, cancellations and rescheduling. Please ask an appointment-related question."


def _extract_json_reply(raw: str) -> Optional[str]:
    text = raw.strip()
    candidates = [text]
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        candidates.append(json_match.group())

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("reply"), str):
            return data["reply"]
    return None


def clean_patient_reply(reply: Any, language: str = "english") -> str:
    if isinstance(reply, dict):
        reply = reply.get("reply", "")
    elif isinstance(reply, list):
        reply = " ".join(str(item) for item in reply)
    else:
        reply = str(reply or "")

    reply = reply.strip()
    extracted = _extract_json_reply(reply)
    if extracted:
        reply = extracted.strip()

    reply = re.sub(r"```(?:json)?", "", reply, flags=re.IGNORECASE).replace("```", "")
    reply = re.sub(r"[\U0001F300-\U0001FAFF]", "", reply)
    reply = re.sub(r"\s+\n", "\n", reply)
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()

    if not reply or reply.startswith("{") or '"intent"' in reply or '"booking_ready"' in reply:
        return appointment_scope_reply(language)

    return reply


def is_past_slot(date_str: Optional[str], time_str: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    if not date_str:
        return False
    current = now or clinic_now()
    try:
        appointment_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False

    if appointment_date < current.date():
        return True
    if appointment_date > current.date() or not time_str:
        return False

    try:
        appointment_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return False
    return appointment_time <= current.time().replace(second=0, microsecond=0)
