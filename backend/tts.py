import hashlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import torch

if torch.cuda.is_available():
    torch.backends.cudnn.benchmark = True
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

from backend.config import settings

logger = logging.getLogger(__name__)

_MODEL_SWAP_TIMEOUT = 3600

_qwen3_model = None
_qwen3_device = None
_qwen3_last_used = 0.0
_qwen3_busy = False

_chatterbox_model = None
_chatterbox_device = None
_chatterbox_last_used = 0.0
_chatterbox_conds_cache = {}
_chatterbox_busy = False


def _ensure_cache_dir():
    os.makedirs(settings.AUDIO_CACHE_DIR, exist_ok=True)


def _detect_tts_language(text: str, language: Optional[str]) -> str:
    if language:
        lang = language.lower()
        if lang in ("hindi", "hinglish"):
            return "Hindi"
        return "English"
    if any("\u0900" <= ch <= "\u097F" for ch in text):
        return "Hindi"
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
    if len(matches) >= 2:
        return "Hindi"
    return "English"


def _find_voice_sample(tts_language: str) -> Optional[str]:
    voice_dir = Path(_PROJECT_ROOT) / "voices" / tts_language.lower()
    if not voice_dir.exists():
        return None
    audio_extensions = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
    for f in sorted(voice_dir.iterdir()):
        if f.suffix.lower() in audio_extensions and f.is_file():
            return str(f)
    return None


def _move_chatterbox_to_cpu():
    global _chatterbox_device, _chatterbox_conds_cache
    if _chatterbox_model is None or _chatterbox_device != "cuda":
        return
    logger.info("Moving Chatterbox from GPU to CPU...")
    t0 = time.time()
    try:
        _chatterbox_model.t3 = _chatterbox_model.t3.to("cpu")
        _chatterbox_model.s3gen = _chatterbox_model.s3gen.to("cpu")
        _chatterbox_model.ve = _chatterbox_model.ve.to("cpu")
        if _chatterbox_model.conds is not None:
            _chatterbox_model.conds = _chatterbox_model.conds.to("cpu")
        _chatterbox_device = "cpu"
        _chatterbox_conds_cache = {}
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info(f"Chatterbox moved to CPU in {time.time()-t0:.1f}s")
    except Exception as e:
        logger.warning(f"Failed to move Chatterbox to CPU: {e}")
        _unload_chatterbox()


def _move_qwen3_to_cpu():
    global _qwen3_device
    if _qwen3_model is None or _qwen3_device != "cuda":
        return
    logger.info("Moving Qwen3 from GPU to CPU...")
    t0 = time.time()
    try:
        _qwen3_model.model = _qwen3_model.model.to("cpu")
        for attr in ['talker', 'speaker_encoder', 'code_predictor']:
            sub = getattr(_qwen3_model, attr, None)
            if sub is not None:
                setattr(_qwen3_model, attr, sub.to("cpu"))
        _qwen3_device = "cpu"
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info(f"Qwen3 moved to CPU in {time.time()-t0:.1f}s")
    except Exception as e:
        logger.warning(f"Failed to move Qwen3 to CPU: {e}")
        _unload_qwen3()


def _free_gpu_for(model_name: str):
    if model_name == "chatterbox":
        _move_qwen3_to_cpu()
    elif model_name == "qwen3":
        _move_chatterbox_to_cpu()


def _move_chatterbox_to_gpu():
    global _chatterbox_device, _chatterbox_conds_cache
    if _chatterbox_model is None or _chatterbox_device == "cuda":
        return
    _free_gpu_for("chatterbox")
    logger.info("Moving Chatterbox from CPU to GPU...")
    t0 = time.time()
    _chatterbox_model.t3 = _chatterbox_model.t3.to("cuda")
    _chatterbox_model.s3gen = _chatterbox_model.s3gen.to("cuda")
    _chatterbox_model.ve = _chatterbox_model.ve.to("cuda")
    _chatterbox_device = "cuda"
    _chatterbox_conds_cache = {}
    logger.info(f"Chatterbox moved to GPU in {time.time()-t0:.1f}s")


def _move_qwen3_to_gpu():
    global _qwen3_device
    if _qwen3_model is None or _qwen3_device == "cuda":
        return
    _free_gpu_for("qwen3")
    logger.info("Moving Qwen3 from CPU to GPU...")
    t0 = time.time()
    _qwen3_model.model = _qwen3_model.model.to("cuda")
    for attr in ['talker', 'speaker_encoder', 'code_predictor']:
        sub = getattr(_qwen3_model, attr, None)
        if sub is not None:
            setattr(_qwen3_model, attr, sub.to("cuda"))
    _qwen3_device = "cuda"
    logger.info(f"Qwen3 moved to GPU in {time.time()-t0:.1f}s")


def _unload_qwen3():
    global _qwen3_model, _qwen3_device
    if _qwen3_model is not None:
        del _qwen3_model
        _qwen3_model = None
    _qwen3_device = None
    import gc; gc.collect()
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _unload_chatterbox():
    global _chatterbox_model, _chatterbox_device, _chatterbox_conds_cache
    if _chatterbox_model is not None:
        del _chatterbox_model
        _chatterbox_model = None
    _chatterbox_device = None
    _chatterbox_conds_cache = {}
    import gc; gc.collect()
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _get_qwen3_model():
    global _qwen3_model, _qwen3_device, _qwen3_last_used

    if _qwen3_model is not None:
        _move_qwen3_to_gpu()
        _qwen3_last_used = time.time()
        return _qwen3_model

    logger.info("Loading Qwen3-TTS model...")
    t0 = time.time()
    try:
        from qwen_tts import Qwen3TTSModel

        _free_gpu_for("qwen3")

        _qwen3_model = Qwen3TTSModel.from_pretrained(
            settings.QWEN3_TTS_MODEL,
            device_map="cuda",
            dtype=torch.bfloat16,
        )
        _qwen3_device = "cuda"

        _qwen3_last_used = time.time()
        logger.info(f"Qwen3-TTS model loaded on GPU in {time.time()-t0:.1f}s")
    except Exception as e:
        logger.error(f"Failed to load Qwen3-TTS model: {e}")
        _qwen3_model = None
        _qwen3_device = None
        raise
    return _qwen3_model


def _get_chatterbox_model():
    global _chatterbox_model, _chatterbox_device, _chatterbox_last_used

    if _chatterbox_model is not None:
        _move_chatterbox_to_gpu()
        _chatterbox_last_used = time.time()
        return _chatterbox_model

    logger.info("Loading Chatterbox TTS model...")
    t0 = time.time()
    try:
        from chatterbox_patched.mtl_tts import ChatterboxMultilingualTTS

        _free_gpu_for("chatterbox")

        _chatterbox_model = ChatterboxMultilingualTTS.from_pretrained("cuda")
        _chatterbox_device = "cuda"

        _chatterbox_last_used = time.time()
        logger.info(f"Chatterbox TTS model loaded on GPU in {time.time()-t0:.1f}s")
    except Exception as e:
        logger.error(f"Failed to load Chatterbox TTS model: {e}")
        _chatterbox_model = None
        _chatterbox_device = None
        raise
    return _chatterbox_model


def _warmup_qwen3():
    try:
        model = _get_qwen3_model()
        ref_audio = _find_voice_sample("english")
        if ref_audio and os.path.exists(ref_audio):
            logger.info("Qwen3 warmup: generating sample audio...")
            wavs, sr = model.generate_voice_clone(
                text="Hello",
                language="English",
                ref_audio=str(ref_audio),
                x_vector_only_mode=True,
            )
            logger.info(f"Qwen3 warmup complete (sr={sr})")
        else:
            logger.info("Qwen3 warmup: model loaded (no ref audio for full warmup)")
    except Exception as e:
        logger.warning(f"Qwen3 warmup failed (non-fatal): {e}")
        _unload_qwen3()


def _warmup_chatterbox():
    try:
        model = _get_chatterbox_model()
        ref_audio = _find_voice_sample("hindi")
        if ref_audio and os.path.exists(ref_audio):
            logger.info("Chatterbox warmup: generating sample audio...")
            _chatterbox_prepare_conditionals_cached(ref_audio, exaggeration=0.5)
            wav = model.generate(
                "Hello",
                language_id="hi",
                cfg_weight=0.3,
                temperature=0.7,
            )
            logger.info("Chatterbox warmup complete")
        else:
            logger.info("Chatterbox warmup: model loaded (no ref audio for full warmup)")
    except Exception as e:
        logger.warning(f"Chatterbox warmup failed (non-fatal): {e}")
        _unload_chatterbox()


def _chatterbox_prepare_conditionals_cached(ref_audio_path: str, exaggeration: float = 0.5):
    global _chatterbox_conds_cache

    if _chatterbox_model is None:
        return

    cache_key = f"{ref_audio_path}|{exaggeration}"
    if cache_key in _chatterbox_conds_cache:
        _chatterbox_model.conds = _chatterbox_conds_cache[cache_key]
        return

    logger.info(f"Preparing Chatterbox conditionals for {ref_audio_path}...")
    t0 = time.time()
    _chatterbox_model.prepare_conditionals(ref_audio_path, exaggeration=exaggeration)
    logger.info(f"Conditionals prepared in {time.time()-t0:.1f}s")

    _chatterbox_conds_cache[cache_key] = _chatterbox_model.conds


def _generate_with_qwen3(text: str, output_path: str, language: Optional[str]) -> Optional[str]:
    global _qwen3_busy
    _qwen3_busy = True
    try:
        t_start = time.time()
        model = _get_qwen3_model()
        tts_lang = _detect_tts_language(text, language)

        ref_audio = _find_voice_sample("english")

        logger.info(f"Qwen3-TTS generating: lang={tts_lang}, text={text[:60]}...")

        kwargs = {
            "text": text,
            "language": tts_lang,
        }
        if ref_audio:
            kwargs["ref_audio"] = str(ref_audio)
            kwargs["x_vector_only_mode"] = True

        with torch.inference_mode():
            wavs, sr = model.generate_voice_clone(**kwargs)

        import numpy as np
        import soundfile as sf

        wav_data = wavs[0]
        if hasattr(wav_data, "cpu"):
            wav_data = wav_data.cpu()
        if hasattr(wav_data, "numpy"):
            wav_data = wav_data.numpy()
        elif not isinstance(wav_data, np.ndarray):
            wav_data = np.array(wav_data)

        if wav_data.ndim > 1:
            wav_data = wav_data.squeeze()

        sf.write(output_path, wav_data, sr)

        if os.path.exists(output_path):
            elapsed = time.time() - t_start
            logger.info(f"Qwen3-TTS audio saved in {elapsed:.1f}s: {output_path}")
            return output_path
    except Exception as e:
        logger.error(f"Qwen3-TTS generation failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _qwen3_busy = False
        global _qwen3_last_used
        _qwen3_last_used = time.time()
    return None


def _generate_with_chatterbox(text: str, output_path: str, language: Optional[str]) -> Optional[str]:
    global _chatterbox_busy
    _chatterbox_busy = True
    try:
        t_start = time.time()
        model = _get_chatterbox_model()
        tts_lang = _detect_tts_language(text, language)

        ref_audio = _find_voice_sample("hindi")
        if not ref_audio or not os.path.exists(ref_audio):
            logger.warning("No reference audio for Chatterbox. Put a .wav file in voices/hindi/")
            return None

        language_code_map = {"Hindi": "hi", "English": "en"}
        lang_code = language_code_map.get(tts_lang, "hi")

        logger.info(f"Chatterbox generating: lang={tts_lang}, text={text[:60]}...")

        _chatterbox_prepare_conditionals_cached(ref_audio, exaggeration=0.5)

        max_chars = 250
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
        for i, chunk in enumerate(chunks):
            t_chunk = time.time()
            with torch.inference_mode():
                wav = model.generate(
                    chunk,
                    language_id=lang_code,
                    cfg_weight=0.3,
                    temperature=0.7,
                )
            chunk_audio = wav.squeeze(0)
            if hasattr(chunk_audio, "cpu"):
                chunk_audio = chunk_audio.cpu()
            if hasattr(chunk_audio, "numpy"):
                chunk_audio = chunk_audio.numpy()
            all_audio.append(chunk_audio)
            logger.info(f"Chatterbox chunk {i+1}/{len(chunks)} done in {time.time()-t_chunk:.1f}s")

        if len(all_audio) > 1:
            full_audio = np.concatenate(all_audio)
        else:
            full_audio = all_audio[0]

        sf.write(output_path, full_audio, model.sr)

        if os.path.exists(output_path):
            elapsed = time.time() - t_start
            logger.info(f"Chatterbox audio saved in {elapsed:.1f}s: {output_path}")
            return output_path
    except Exception as e:
        logger.error(f"Chatterbox generation failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _chatterbox_busy = False
        global _chatterbox_last_used
        _chatterbox_last_used = time.time()
    return None


def _generate_with_piper(text: str, output_path: str) -> Optional[str]:
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
    _ensure_cache_dir()

    provider = (settings.TTS_PROVIDER or "piper").lower()
    cache_basis = f"{provider}|{settings.PIPER_VOICE}|{language}|{text}"
    cache_key = hashlib.md5(cache_basis.encode("utf-8")).hexdigest()
    output_path = os.path.join(settings.AUDIO_CACHE_DIR, f"{cache_key}.wav")

    if os.path.exists(output_path):
        return output_path

    import asyncio

    if provider == "auto":
        tts_lang = _detect_tts_language(text, language)
        if tts_lang == "Hindi":
            provider = "chatterbox"
        else:
            provider = "qwen3"

    result = None
    if provider == "chatterbox":
        result = await asyncio.to_thread(_generate_with_chatterbox, text, output_path, language)
        if result is None:
            logger.warning("Chatterbox failed, falling back to Qwen3...")
            result = await asyncio.to_thread(_generate_with_qwen3, text, output_path, language)
    elif provider == "qwen3":
        result = await asyncio.to_thread(_generate_with_qwen3, text, output_path, language)
        if result is None:
            logger.warning("Qwen3 failed, falling back to Chatterbox...")
            result = await asyncio.to_thread(_generate_with_chatterbox, text, output_path, language)
    elif provider == "piper":
        result = await asyncio.to_thread(_generate_with_piper, text, output_path)

    return result


def _idle_model_cleanup():
    now = time.time()
    if (_qwen3_model is not None
            and _qwen3_device == "cuda"
            and not _qwen3_busy
            and now - _qwen3_last_used > _MODEL_SWAP_TIMEOUT):
        _move_qwen3_to_cpu()

    if (_chatterbox_model is not None
            and _chatterbox_device == "cuda"
            and not _chatterbox_busy
            and now - _chatterbox_last_used > _MODEL_SWAP_TIMEOUT):
        _move_chatterbox_to_cpu()


def unload_tts_models():
    global _qwen3_model, _qwen3_device
    global _chatterbox_model, _chatterbox_device, _chatterbox_conds_cache

    if _qwen3_model is not None:
        del _qwen3_model
        _qwen3_model = None
        _qwen3_device = None
        logger.info("Qwen3-TTS model unloaded")

    if _chatterbox_model is not None:
        del _chatterbox_model
        _chatterbox_model = None
        _chatterbox_device = None
        _chatterbox_conds_cache = {}
        logger.info("Chatterbox model unloaded")

    try:
        import gc; gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
