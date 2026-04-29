import os
import subprocess
import tempfile
from typing import Optional

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model("small")
    return _whisper_model


async def convert_audio_format(input_path: str) -> str:
    output_path = input_path.rsplit(".", 1)[0] + ".wav"

    if os.path.exists(output_path):
        return output_path

    result = subprocess.run(
        ["ffmpeg", "-i", input_path, "-ar", "16000", "-ac", "1", "-y", output_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr}")

    return output_path


async def transcribe_audio(audio_path: str) -> str:
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    ext = os.path.splitext(audio_path)[1].lower()
    if ext in (".ogg", ".opus", ".mp4", ".m4a", ".webm"):
        audio_path = await convert_audio_format(audio_path)

    model = _get_whisper_model()

    result = model.transcribe(
        audio_path,
        language=None,
        task="transcribe",
    )

    text = result["text"].strip()

    if not text:
        raise ValueError("Could not transcribe any speech from the audio")

    return text
