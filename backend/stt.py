import os
import logging
import subprocess
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            import numpy
        except ImportError:
            raise ImportError("numpy is required for Whisper. Run: pip install numpy")

        import whisper
        _whisper_model = whisper.load_model("medium")
        logger.info("Whisper model loaded (medium)")
    return _whisper_model


async def convert_audio_format(input_path: str) -> str:
    """Convert any audio format to 16kHz mono WAV for Whisper."""
    output_path = input_path.rsplit(".", 1)[0] + "_converted.wav"

    if os.path.exists(output_path):
        return output_path

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", input_path, "-ar", "16000", "-ac", "1", "-y", output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(f"ffmpeg conversion failed, using original: {stderr.decode()[:200]}")
            return input_path

        return output_path
    except Exception as e:
        logger.warning(f"ffmpeg not available, using original file: {e}")
        return input_path


def _detect_language_hint(text: str) -> Optional[str]:
    devanagari_range = range(0x0900, 0x097F)
    has_devanagari = any(ord(c) in devanagari_range for c in text)
    if has_devanagari:
        return "hi"
    return None


async def transcribe_audio(audio_path: str) -> tuple[str, Optional[str]]:
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    model = _get_whisper_model()

    # Convert OGG to WAV first for better recognition
    converted_path = await convert_audio_format(audio_path)

    result = model.transcribe(
        converted_path,
        language=None,
        task="transcribe",
        fp16=False,
    )

    text = result["text"].strip()
    if not text:
        raise ValueError("Could not transcribe any speech from the audio")

    detected_lang = result.get("language", "en")
    logger.info(f"Whisper detected language: {detected_lang}, text: {text[:60]}")

    lang_hint = None
    if detected_lang in ("hi", "hindi"):
        lang_hint = "hindi"
    elif _detect_language_hint(text):
        lang_hint = "hindi"
    elif detected_lang == "en":
        has_hindi_words = any(w in text.lower() for w in [
            "hai", "hain", "kya", "mujhe", "apointment", "doctor", "clinic",
            "kal", "aaj", "parso", "namaste", "dhanyavad", "sir", "madam",
            "book", "cancel", "time", "date", "morning", "evening",
        ])
        if has_hindi_words:
            lang_hint = "hinglish"
        else:
            lang_hint = "english"
    else:
        lang_hint = "hinglish"

    return text, lang_hint
