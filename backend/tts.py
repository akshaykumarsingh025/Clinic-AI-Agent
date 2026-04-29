import hashlib
import os
import subprocess
from typing import Optional

from backend.config import settings


def _ensure_cache_dir():
    os.makedirs(settings.AUDIO_CACHE_DIR, exist_ok=True)


async def generate_voice_reply(text: str) -> Optional[str]:
    _ensure_cache_dir()

    cache_key = hashlib.md5(text.encode()).hexdigest()
    output_path = os.path.join(settings.AUDIO_CACHE_DIR, f"{cache_key}.wav")

    if os.path.exists(output_path):
        return output_path

    if not os.path.exists(settings.PIPER_VOICE):
        return None

    if not os.path.exists(settings.PIPER_VOICE + ".json"):
        return None

    try:
        process = subprocess.run(
            [
                settings.PIPER_BINARY,
                "--model", settings.PIPER_VOICE,
                "--output_file", output_path,
            ],
            input=text.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        if os.path.exists(output_path):
            return output_path
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return None
