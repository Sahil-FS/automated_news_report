# modules/voice_generator.py — Generate speech with Piper TTS via subprocess

import os
import subprocess
import hashlib
import io
import re

from config import PIPER_EXECUTABLE, PIPER_MODEL, AUDIO_DIR

# Pacing variables for dynamic silence insertion
PAUSE_AFTER_SENTENCE = 1.2  # seconds


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


def _generate_silence(duration_ms: int) -> bytes:
    import wave
    import struct
    # Generate silence WAV bytes for given duration in milliseconds
    sample_rate = 22050  # Match Piper's output sample rate
    num_samples = int(sample_rate * duration_ms / 1000)
    silence_data = b'\x00\x00' * num_samples  # 16-bit silence
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence_data)
    return wav_buffer.getvalue()


def _merge_wavs(wav_paths: list[str], output_path: str) -> bool:
    import wave
    # Merge multiple WAV files into one
    if not wav_paths:
        return False
    with wave.open(wav_paths[0], 'rb') as first_wav:
        params = first_wav.getparams()
        combined_data = first_wav.readframes(first_wav.getnframes())
    for path in wav_paths[1:]:
        with wave.open(path, 'rb') as wav_file:
            combined_data += wav_file.readframes(wav_file.getnframes())
    with wave.open(output_path, 'wb') as output_wav:
        output_wav.setparams(params)
        output_wav.writeframes(combined_data)
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

    # Split text into sentence  fragments
    fragments = re.split(r'(?<=[.!?])\s+', text.strip())
    fragments = [f for f in fragments if f.strip()]

    if len(fragments) <= 1:
        # Single fragment — use original logic
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

    else:
        # Multiple fragments — generate each, insert pauses, merge
        temp_wavs = []
        for i, frag in enumerate(fragments):
            frag_hash = hashlib.md5(frag.encode()).hexdigest()[:8]
            frag_wav = os.path.join(AUDIO_DIR, f"scene_{index:02d}_frag_{i}_{frag_hash}.wav")

            cmd = [
                PIPER_EXECUTABLE,
                "--model",       PIPER_MODEL,
                "--length_scale", "1.15",
                "--output_file", frag_wav,
            ]

            try:
                result = subprocess.run(
                    cmd,
                    input=frag.encode("utf-8"),
                    capture_output=True,
                    timeout=60,
                )
                if result.returncode == 0 and os.path.exists(frag_wav) and os.path.getsize(frag_wav) > 1024:
                    temp_wavs.append(frag_wav)
                    # Insert 0.5s silence after each fragment except the last
                    if i < len(fragments) - 1:
                        silence_wav = os.path.join(AUDIO_DIR, f"scene_{index:02d}_silence_{i}.wav")
                        with open(silence_wav, 'wb') as f:
                            f.write(_generate_silence(int(PAUSE_AFTER_SENTENCE * 1000)))
                        temp_wavs.append(silence_wav)
                else:
                    err = result.stderr.decode("utf-8", errors="replace")
                    print(f"[VoiceGen] Fragment {i} error: {err}")
            except Exception as exc:
                print(f"[VoiceGen] Fragment {i} exception: {exc}")

        if temp_wavs:
            if _merge_wavs(temp_wavs, wav_path):
                # Clean up temp files
                for tw in temp_wavs:
                    if os.path.exists(tw):
                        os.remove(tw)
                print(f"[VoiceGen] Merged audio saved -> {wav_path}")
                return wav_path

        print(f"[VoiceGen] Failed to generate merged audio.")
        return None


if __name__ == "__main__":
    path = generate_audio("Hello, this is a test of the Piper text to speech system.", 0)
    print("Result:", path)
