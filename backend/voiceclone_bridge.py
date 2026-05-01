import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: voiceclone_bridge.py request.json", file=sys.stderr)
        return 2

    request_path = Path(sys.argv[1])
    with open(request_path, "r", encoding="utf-8") as f:
        request = json.load(f)

    project_dir = Path(request["project_dir"]).resolve()
    if not project_dir.exists():
        print(f"VoiceClone project not found: {project_dir}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(project_dir))

    text = request["text"]
    language = request.get("language") or "English"
    ref_audio_path = Path(request["ref_audio_path"])
    output_path = Path(request["output_path"])
    ref_text = request.get("ref_text") or ""

    if not ref_text and language == "English":
        try:
            from config import DEFAULT_VOICE_REF_TEXT
            ref_text = DEFAULT_VOICE_REF_TEXT
        except Exception:
            ref_text = ""

    if language == "Hindi":
        from chatterbox_tts_client import ChatterboxTTSClient
        client = ChatterboxTTSClient()
    else:
        from qwen_tts_client import Qwen3TTSClient
        client = Qwen3TTSClient()

    generated_path = client.generate_voice_audio(
        text=text,
        ref_audio_path=ref_audio_path,
        language=language,
        ref_text=ref_text,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(generated_path, output_path)
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
