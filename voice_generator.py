# modules/voice_generator.py — Generate speech with Piper TTS via subprocess

import os
import subprocess
import hashlib

from config import PIPER_EXECUTABLE, PIPER_MODEL, AUDIO_DIR


def _check_piper() -> bool:
    """Return True if the Piper executable and model file exist."""
    if not os.path.isfile(PIPER_EXECUTABLE):
        print(
            f"[VoiceGen] Piper executable not found at '{PIPER_EXECUTABLE}'.\n"
            "  Download from https://github.com/rhasspy/piper/releases\n"
            "  and update PIPER_EXECUTABLE in config.py."
        )
        return False
    if not os.path.isfile(PIPER_MODEL):
        print(
            f"[VoiceGen] Piper model not found at '{PIPER_MODEL}'.\n"
            "  Download en_US-lessac-medium.onnx from:\n"
            "  https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium\n"
            "  and update PIPER_MODEL in config.py."
        )
        return False
    return True


def generate_audio(text: str, index: int) -> str | None:
    """
    Use Piper TTS to synthesise *text* and write a WAV file to AUDIO_DIR.

    Command issued:
        echo "text" | piper --model <model> --output_file <wav>

    Returns the WAV path on success, None on failure.
    """
    if not _check_piper():
        return None

    safe = hashlib.md5(text.encode()).hexdigest()[:8]
    wav_path = os.path.join(AUDIO_DIR, f"scene_{index:02d}_{safe}.wav")

    # FORCE RE-GENERATION — no cache reuse
    if os.path.exists(wav_path):
        os.remove(wav_path)

    print(f"[VoiceGen] Generating audio for scene {index}: {text[:60]}…")

    cmd = [
        PIPER_EXECUTABLE,
        "--model",       PIPER_MODEL,
        "--length_scale", "1.15",
        "--output_file", wav_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")
            print(f"[VoiceGen] Piper error (rc={result.returncode}): {err}")
            return None
    except FileNotFoundError:
        print(f"[VoiceGen] Cannot execute '{PIPER_EXECUTABLE}' — not found.")
        return None
    except subprocess.TimeoutExpired:
        print("[VoiceGen] Piper timed out.")
        return None
    except Exception as exc:
        print(f"[VoiceGen] Unexpected error: {exc}")
        return None

    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1024:
        print(f"[VoiceGen] Audio saved -> {wav_path}")
        return wav_path

    print(f"[VoiceGen] Output file missing or empty after Piper run.")
    return None


if __name__ == "__main__":
    path = generate_audio("Hello, this is a test of the Piper text to speech system.", 0)
    print("Result:", path)
