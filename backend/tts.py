import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from backend.config import settings


def _ensure_cache_dir():
    os.makedirs(settings.AUDIO_CACHE_DIR, exist_ok=True)


def _voiceclone_python(project_dir: Path) -> str:
    if settings.VOICECLONE_PYTHON:
        return settings.VOICECLONE_PYTHON
    venv_python = project_dir / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _voiceclone_language(text: str, language: Optional[str]) -> str:
    configured = (settings.VOICECLONE_LANGUAGE or "auto").lower()
    if configured in ("hindi", "english"):
        return configured.title()
    if language == "hindi" or any("\u0900" <= ch <= "\u097F" for ch in text):
        return "Hindi"
    return "English"


def _voiceclone_voice_sample(project_dir: Path, language: str) -> str:
    if settings.VOICECLONE_VOICE_SAMPLE:
        return settings.VOICECLONE_VOICE_SAMPLE
    if language == "Hindi":
        return str(project_dir / "Voices" / "deepikaHindiVoice.wav")
    return str(project_dir / "Voices" / "deepikaVoice.mp3")


def _generate_with_voiceclone(text: str, output_path: str, language: Optional[str]) -> Optional[str]:
    project_dir = Path(settings.VOICECLONE_PROJECT_DIR)
    if not project_dir.exists():
        return None

    voice_language = _voiceclone_language(text, language)
    voice_sample = _voiceclone_voice_sample(project_dir, voice_language)
    if not os.path.exists(voice_sample):
        return None

    request = {
        "text": text,
        "language": voice_language,
        "ref_audio_path": voice_sample,
        "ref_text": settings.VOICECLONE_REF_TEXT,
        "output_path": output_path,
        "project_dir": str(project_dir),
    }

    bridge_path = Path(__file__).with_name("voiceclone_bridge.py")
    python_exe = _voiceclone_python(project_dir)

    fd, request_path = tempfile.mkstemp(prefix="voiceclone_request_", suffix=".json", dir=settings.AUDIO_CACHE_DIR)
    os.close(fd)
    try:
        with open(request_path, "w", encoding="utf-8") as f:
            json.dump(request, f, ensure_ascii=False)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [python_exe, str(bridge_path), request_path],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        try:
            os.remove(request_path)
        except OSError:
            pass

    return None


async def generate_voice_reply(text: str, language: Optional[str] = None) -> Optional[str]:
    _ensure_cache_dir()

    provider = (settings.TTS_PROVIDER or "piper").lower()
    cache_basis = f"{provider}|{settings.PIPER_VOICE}|{settings.VOICECLONE_VOICE_SAMPLE}|{language}|{text}"
    cache_key = hashlib.md5(cache_basis.encode("utf-8")).hexdigest()
    output_path = os.path.join(settings.AUDIO_CACHE_DIR, f"{cache_key}.wav")

    if os.path.exists(output_path):
        return output_path

    if provider == "voiceclone":
        generated = _generate_with_voiceclone(text, output_path, language)
        if generated:
            return generated

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
