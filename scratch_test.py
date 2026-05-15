import torch
from backend.tts import _get_chatterbox_model
import time

cb = _get_chatterbox_model()

print("Generating audio...")
t0 = time.time()
try:
    wav = cb.generate(
        text="Namaste, main theek hoon",
        language_id="hi",
        audio_prompt_path="voices/hindi/sonika.ogg"
    )
    print("Generation successful in", time.time() - t0)
except Exception as e:
    print("Error during generation:", e)
