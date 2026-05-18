# Chatterbox TTS package - bundled from ResembleAI/chatterbox
# Only the multilingual TTS model is used for Hindi voice cloning.
# Use lazy imports to avoid loading unused submodules.

from .mtl_tts import ChatterboxMultilingualTTS, SUPPORTED_LANGUAGES

__all__ = ["ChatterboxMultilingualTTS", "SUPPORTED_LANGUAGES"]
