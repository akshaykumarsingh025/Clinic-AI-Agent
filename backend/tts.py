import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import torch

if torch.cuda.is_available():
    torch.backends.cudnn.benchmark = True

from backend.config import settings

logger = logging.getLogger(__name__)

# Singleton model instances (lazy loaded)
_qwen3_model = None
_chatterbox_model = None


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


def _detect_tts_language(text: str, language: Optional[str]) -> str:
    """Detect language for TTS provider selection."""
    if language:
        lang = language.lower()
        if lang in ("hindi", "hinglish"):
            return "Hindi"
        return "English"
    # Devanagari characters = Hindi
    if any("\u0900" <= ch <= "\u097F" for ch in text):
        return "Hindi"
    # Common Hinglish words (distinctly Hindi, not English loanwords)
    hinglish_words = {
        "hai", "hain", "hoon", "ho", "tha", "thi", "the",
        "kya", "kaise", "kaisa", "kaun", "kab", "kahan",
        "mujhe", "mujhko", "hum", "ham", "hamko", "hamein",
        "aap", "aapko", "tum", "tumko", "usko", "unhein",
        "mera", "meri", "mere", "apna", "apni", "apne",
        "nahi", "nahin", "haan", "ji",
        "bilkul", "zaroor", "accha", "theek", "dhanyavaad",
        "namaste", "namaskar", "sahab",
        "madam", "behen", "bhai", "didi",
        "karna", "karenge", "karunga", "karungi",
        "chahiye", "chahta", "chahti", "dena", "dijiye",
        "batao", "bataiye", "sunao", "suniye",
        "dawai", "dawa", "baccha", "bacche", "garbh",
        "haanji", "achha", "theek hai", "bilkul sahi",
    }
    import re
    lower_text = text.lower()
    words = set(re.findall(r'[a-z]+', lower_text))
    matches = words & hinglish_words
    # Require at least 2 Hindi words to avoid false positives on English text
    if len(matches) >= 2:
        return "Hindi"
    return "English"


def _find_voice_sample(tts_language: str) -> Optional[str]:
    """Find a voice sample file from voices/hindi/ or voices/english/ folder."""
    voice_dir = Path("voices") / tts_language.lower()
    if not voice_dir.exists():
        return None
    audio_extensions = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
    for f in sorted(voice_dir.iterdir()):
        if f.suffix.lower() in audio_extensions and f.is_file():
            return str(f)
    return None


def _get_qwen3_model():
    """Lazy load Qwen3 TTS model (singleton)."""
    global _qwen3_model, _chatterbox_model
    if _chatterbox_model is not None:
        logger.info("Unloading Chatterbox model to free VRAM for Qwen3...")
        del _chatterbox_model
        _chatterbox_model = None
        import gc; gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception: pass

    if _qwen3_model is None:
        try:
            import torch
            from qwen_tts import Qwen3TTSModel

            device = "cuda"
            dtype = torch.float16 # Using float16 instead of bfloat16 as float16 is widely supported on CUDA
            logger.info(f"Loading Qwen3-TTS model on {device}...")
            _qwen3_model = Qwen3TTSModel.from_pretrained(
                settings.QWEN3_TTS_MODEL,
                device_map=device,
                torch_dtype=dtype,
            )

            
            try:
                # Log device to verify if parameters exist
                dev = next(_qwen3_model.parameters()).device
                logger.info(f"Qwen3-TTS model loaded on {device}. Found parameter on {dev}")
            except Exception:
                logger.info(f"Qwen3-TTS model loaded on {device}")
        except Exception as e:
            logger.error(f"Failed to load Qwen3-TTS model: {e}")
            raise
    return _qwen3_model


def _get_chatterbox_model():
    """Lazy load Chatterbox TTS model (singleton) on GPU with optimized settings."""
    global _chatterbox_model, _qwen3_model
    if _qwen3_model is not None:
        logger.info("Unloading Qwen3 model to free VRAM for Chatterbox...")
        del _qwen3_model
        _qwen3_model = None
        import gc; gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception: pass

    if _chatterbox_model is None:
        try:
            import torch
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS

            if not torch.cuda.is_available():
                logger.warning("CUDA not available, but forcing Chatterbox to run on cuda as requested.")
            device = "cuda"
            logger.info(f"Loading Chatterbox TTS model on {device}...")
            _chatterbox_model = ChatterboxMultilingualTTS.from_pretrained(device)

            # Explicitly force to CUDA
            if hasattr(_chatterbox_model, "t3"):
                _chatterbox_model.t3.to(device)
            if hasattr(_chatterbox_model, "s3gen"):
                _chatterbox_model.s3gen.to(device)
            if hasattr(_chatterbox_model, "ve"):
                _chatterbox_model.ve.to(device)

            try:
                # Log device to verify
                dev_t3 = next(_chatterbox_model.t3.parameters()).device
                dev_s3 = next(_chatterbox_model.s3gen.parameters()).device
                logger.info(f"Chatterbox TTS model loaded on {device} (t3={dev_t3}, s3={dev_s3}) (max_new_tokens=300)")
            except Exception:
                logger.info(f"Chatterbox TTS model loaded on {device} (max_new_tokens=300)")

            # Speed up: reduce max_new_tokens from 1000 to 300
            _original_inference = _chatterbox_model.t3.inference

            def _fast_inference(*args, **kwargs):
                kwargs['max_new_tokens'] = 300
                return _original_inference(*args, **kwargs)

            _chatterbox_model.t3.inference = _fast_inference
        except Exception as e:
            logger.error(f"Failed to load Chatterbox TTS model: {e}")
            raise
    return _chatterbox_model


def _generate_with_qwen3(text: str, output_path: str, language: Optional[str]) -> Optional[str]:
    """Generate TTS audio using Qwen3-TTS (direct, no subprocess). Best for English."""
    try:
        model = _get_qwen3_model()
        tts_lang = _detect_tts_language(text, language)

        # Get reference audio: try English folder first, then fallback to config
        ref_audio = _find_voice_sample("english") or settings.VOICECLONE_VOICE_SAMPLE or ""
        ref_text = settings.VOICECLONE_REF_TEXT or ""

        logger.info(f"Qwen3-TTS generating: lang={tts_lang}, text={text[:60]}...")

        kwargs = {
            "text": text,
            "language": tts_lang,
            "ref_audio": str(ref_audio) if ref_audio else None,
            "ref_text": ref_text if ref_text else None,
        }
        if ref_audio and not ref_text:
            kwargs["x_vector_only_mode"] = True

        wavs, sr = model.generate_voice_clone(**kwargs)

        import soundfile as sf
        sf.write(output_path, wavs[0], sr)

        if os.path.exists(output_path):
            logger.info(f"Qwen3-TTS audio saved: {output_path}")
            return output_path
    except Exception as e:
        logger.error(f"Qwen3-TTS generation failed: {e}")
    return None


def _generate_with_chatterbox(text: str, output_path: str, language: Optional[str]) -> Optional[str]:
    """Generate TTS audio using Chatterbox Multilingual TTS (direct, no subprocess). Best for Hindi."""
    try:
        model = _get_chatterbox_model()
        tts_lang = _detect_tts_language(text, language)

        # Get reference audio: try Hindi folder first, then fallback to config
        ref_audio = _find_voice_sample("hindi") or settings.VOICECLONE_VOICE_SAMPLE or ""
        if not ref_audio or not os.path.exists(ref_audio):
            logger.warning("No reference audio for Chatterbox voice cloning. Put a .wav file in voices/hindi/")
            return None

        language_code_map = {"Hindi": "hi", "English": "en"}
        lang_code = language_code_map.get(tts_lang, "hi")

        logger.info(f"Chatterbox generating: lang={tts_lang}, text={text[:60]}...")

        # Chunk text if needed (Chatterbox has a ~300 char limit)
        max_chars = 300
        chunks = []
        if len(text) <= max_chars:
            chunks = [text]
        else:
            import re
            sentences = re.split(r"(?<=[\u0964.!?])\s+", text)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) + 1 <= max_chars:
                    current = (current + " " + sent).strip() if current else sent
                else:
                    if current:
                        chunks.append(current)
                    current = sent
            if current:
                chunks.append(current)

        import numpy as np
        import soundfile as sf

        all_audio = []
        for chunk in chunks:
            wav = model.generate(
                chunk,
                language_id=lang_code,
                audio_prompt_path=str(ref_audio),
                exaggeration=0.5,
                temperature=0.8,
                cfg_weight=0.5,
            )
            chunk_audio = wav.squeeze(0)
            if hasattr(chunk_audio, "cpu"):
                chunk_audio = chunk_audio.cpu()
            if hasattr(chunk_audio, "numpy"):
                chunk_audio = chunk_audio.numpy()
            all_audio.append(chunk_audio)

        if len(all_audio) > 1:
            full_audio = np.concatenate(all_audio)
        else:
            full_audio = all_audio[0]

        sf.write(output_path, full_audio, model.sr)

        if os.path.exists(output_path):
            logger.info(f"Chatterbox audio saved: {output_path}")
            return output_path
    except Exception as e:
        logger.error(f"Chatterbox generation failed: {e}")
    return None


def _generate_with_voiceclone(text: str, output_path: str, language: Optional[str]) -> Optional[str]:
    """Generate TTS via VoiceClone subprocess bridge."""
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


def _generate_with_piper(text: str, output_path: str) -> Optional[str]:
    """Generate TTS using Piper binary."""
    if not os.path.exists(settings.PIPER_VOICE):
        return None
    if not os.path.exists(settings.PIPER_VOICE + ".json"):
        return None

    try:
        subprocess.run(
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


async def generate_voice_reply(text: str, language: Optional[str] = None) -> Optional[str]:
    """Generate voice reply using the configured TTS provider."""
    _ensure_cache_dir()

    provider = (settings.TTS_PROVIDER or "piper").lower()
    cache_basis = f"{provider}|{settings.PIPER_VOICE}|{settings.VOICECLONE_VOICE_SAMPLE}|{language}|{text}"
    cache_key = hashlib.md5(cache_basis.encode("utf-8")).hexdigest()
    output_path = os.path.join(settings.AUDIO_CACHE_DIR, f"{cache_key}.wav")

    if os.path.exists(output_path):
        return output_path

    # Smart auto-routing: pick best provider per language
    if provider == "auto":
        tts_lang = _detect_tts_language(text, language)
        if tts_lang == "Hindi":
            provider = "chatterbox"
        else:
            provider = "qwen3"

    if provider == "qwen3":
        return _generate_with_qwen3(text, output_path, language)
    elif provider == "chatterbox":
        return _generate_with_chatterbox(text, output_path, language)
    elif provider == "voiceclone":
        return _generate_with_voiceclone(text, output_path, language)
    elif provider == "piper":
        return _generate_with_piper(text, output_path)

    return None


def unload_tts_models():
    """Unload all TTS models from memory."""
    global _qwen3_model, _chatterbox_model

    if _qwen3_model is not None:
        del _qwen3_model
        _qwen3_model = None
        logger.info("Qwen3-TTS model unloaded")

    if _chatterbox_model is not None:
        del _chatterbox_model
        _chatterbox_model = None
        logger.info("Chatterbox model unloaded")

    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
