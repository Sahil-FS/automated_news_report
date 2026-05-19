# modules/voice_generator.py -- Generate speech with Piper TTS via subprocess

import os
import subprocess
import hashlib
import io
import re

from config import PIPER_EXECUTABLE, PIPER_MODEL, AUDIO_DIR

import hashlib as _hashlib
import datetime as _datetime

# ── Voice roulette - alternates narrator per run ──────────────────────
# Uses the current date's day-of-year as a deterministic toggle so
# every run on a new day gets a different voice, and all scenes within
# one run use the same voice (consistent narration per video).
#
# Kokoro voice codes:
#   af_nova   - professional American female (neutral authority)
#   am_adam   - professional American male   (firm authority)
#   af_sarah  - warm American female         (human interest)
#   bm_george - British male                 (BBC-style gravitas)
#
# If Kokoro is not installed these values are ignored and Piper is used.
_VOICE_POOL = ["af_nova", "am_adam", "af_sarah", "bm_george"]
_RUN_VOICE  = _VOICE_POOL[_datetime.date.today().timetuple().tm_yday
                           % len(_VOICE_POOL)]

# ── Context-aware voice and speed selection ────────────────────────────
# Phase 9: authoritative voices for hard news, warm for human interest.
# Speed range: 1.00 (deliberate/authoritative) to 1.10 (energetic).
_AUTHORITATIVE_VOICE = "bm_george"   # BBC-style gravitas for hard news
_WARM_VOICE          = "af_nova"     # Warm professional female for softer stories
_FALLBACK_VOICE      = _RUN_VOICE    # Day-of-year rotation as fallback

_CONTEXT_VOICE_MAP = {
    "war":         "am_adam",       # firm male voice for conflict coverage
    "tense":       "bm_george",     # BBC gravitas for tense situations
    "disaster":    "bm_george",     # authoritative for emergencies
    "politics":    "am_adam",       # firm authority for political coverage
    "serious":     "bm_george",     # BBC gravitas for serious topics
    "positive":    "af_sarah",      # warm for good news
    "business":    "af_nova",       # professional neutral for business
    "informative": "af_nova",       # professional neutral for tech/science
    "neutral":     _FALLBACK_VOICE, # day-rotation for neutral
    "general":     _FALLBACK_VOICE, # day-rotation for general
}

# Dynamic speed: slower for authoritative hard news, faster for positive/business
_CONTEXT_SPEED_MAP = {
    "war":         1.05,   # deliberate pace for conflict
    "tense":       1.05,   # deliberate for tense
    "disaster":    1.05,   # clear and calm for emergencies
    "politics":    1.08,   # slight urgency for political
    "serious":     1.05,   # measured for serious
    "positive":    1.12,   # energetic for good news
    "business":    1.10,   # brisk for business
    "informative": 1.10,   # normal for informative
    "neutral":     1.10,   # standard
    "general":     1.10,   # standard
}

print(f"[VoiceGen] Run voice: '{_RUN_VOICE}' (context-aware override available)")

# To enable Kokoro TTS (better voice quality, same ONNX-based offline execution):
# pip install kokoro>=0.9.4 soundfile
# Windows: winget install espeak-ng  (required phoneme backend)
# Then re-run -- Kokoro activates automatically, Piper stays as fallback
KOKORO_AVAILABLE = False
_KOKORO_FAIL_REASON = ""

try:
    from kokoro import KPipeline
    import soundfile as _sf_test
    import numpy as _np_test
    KOKORO_AVAILABLE = True
    print("[VoiceGen] Kokoro TTS: AVAILABLE")
except ImportError as _ke:
    _KOKORO_FAIL_REASON = str(_ke)
    KOKORO_AVAILABLE = False
    print(f"[VoiceGen] Kokoro TTS: NOT AVAILABLE -- {_KOKORO_FAIL_REASON}")
    print("[VoiceGen] To install Kokoro run these commands in your .venv:")
    print("  .venv\\Scripts\\pip.exe install kokoro>=0.9.4 soundfile")
    print("  winget install espeak-ng  (required phoneme backend)")
except Exception as _ke:
    _KOKORO_FAIL_REASON = str(_ke)
    KOKORO_AVAILABLE = False
    print(f"[VoiceGen] Kokoro TTS: ERROR during import -- {_KOKORO_FAIL_REASON}")

_kokoro_pipeline = None

# Pacing variables for dynamic silence insertion
PAUSE_AFTER_SENTENCE = 0.4  # seconds


import threading
_KOKORO_INIT_LOCK = threading.Lock()

def _get_kokoro_pipeline():
    global _kokoro_pipeline, KOKORO_AVAILABLE
    if _kokoro_pipeline is not None:
        return _kokoro_pipeline          # fast path, no lock needed
    with _KOKORO_INIT_LOCK:
        if _kokoro_pipeline is None:     # re-check inside lock
            try:
                import warnings
                import os
                # Suppress Hugging Face Hub warnings for known model
                os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
                os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
                os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
                # Suppress the "unauthenticated requests" warning from huggingface_hub
                import logging
                logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
                logging.getLogger("huggingface_hub.utils._headers").setLevel(logging.ERROR)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _kokoro_pipeline = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
            except Exception as exc:
                print(f"[Kokoro] Pipeline init failed: {exc}")
                KOKORO_AVAILABLE = False
                return None
    return _kokoro_pipeline


def generate_audio_kokoro(text, wav_path, context: str = "neutral") -> bool:
    """
    Generate audio via Kokoro TTS with context-aware voice and speed selection.
    """
    try:
        pipeline = _get_kokoro_pipeline()
        if pipeline is None:
            return False

        import numpy as np
        import soundfile as sf

        # Select voice and speed based on story context
        voice = _CONTEXT_VOICE_MAP.get(context, _RUN_VOICE)
        speed = _CONTEXT_SPEED_MAP.get(context, 1.05)
        print(f"[Kokoro] Context='{context}' -> voice='{voice}', speed={speed:.2f}")

        chunks = []
        for result in pipeline(text, voice=voice, speed=speed):
            # Kokoro 0.9.4 yields KPipeline.Result objects with a .audio
            # attribute that is a PyTorch Tensor of shape [N].
            # Using item[-1] or np.asarray(item) doesn't work --
            # .audio is the correct attribute.
            audio = getattr(result, 'audio', None)
            if audio is None:
                continue
            # Convert PyTorch Tensor -> numpy float32 array
            if hasattr(audio, 'detach'):
                arr = audio.detach().cpu().numpy().astype(np.float32)
            else:
                arr = np.array(audio, dtype=np.float32).ravel()
            if arr.size > 0:
                chunks.append(arr)

        if not chunks:
            print("[Kokoro] No audio chunks produced")
            return False

        combined = np.concatenate(chunks)

        # PHASE 21: Prepend 120ms of leading silence to prevent first-word clipping.
        # Kokoro generates audio with no leading silence. When MoviePy loads the WAV
        # and composes it with the whoosh SFX, a sub-frame alignment offset can clip
        # the very first phoneme. 120ms of silence gives the mixer a safe runway.
        _SAMPLE_RATE = 24000
        _LEAD_SILENCE_MS = 120
        _lead_samples = int(_SAMPLE_RATE * _LEAD_SILENCE_MS / 1000)
        _lead_silence = np.zeros(_lead_samples, dtype=np.float32)
        combined = np.concatenate([_lead_silence, combined])

        sf.write(wav_path, combined, samplerate=_SAMPLE_RATE)
        ok = os.path.exists(wav_path) and os.path.getsize(wav_path) > 1024
        if ok:
            _dur_s = combined.shape[0] / _SAMPLE_RATE
            print(f"[Kokoro] Audio saved ({_dur_s:.2f}s incl. 120ms lead) -> {wav_path}")
            normalize_audio(wav_path)
            trim_audio_silence(wav_path)   # remove tail silence for tight scene transitions
        return ok
    except Exception as exc:
        print(f"[Kokoro] Audio generation error: {exc}")
        return False


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


def generate_audio(text: str, index: int, context: str = "neutral") -> str | None:
    """
    Use Kokoro/Piper TTS to synthesise *text* and write a WAV file to AUDIO_DIR.

    Piper fallback command issued:
        echo "text" | piper --model <model> --output_file <wav>

    Args:
        text:    narration text
        index:   scene index for filename
        context: story context ("war", "politics", etc.) for voice/speed selection

    Returns the WAV path on success, None on failure.
    """
    safe = hashlib.md5(text.encode()).hexdigest()[:8]
    wav_path = os.path.join(AUDIO_DIR, f"scene_{index:02d}_{safe}.wav")

    # FORCE RE-GENERATION -- no cache reuse
    if os.path.exists(wav_path):
        os.remove(wav_path)

    _active_voice = _CONTEXT_VOICE_MAP.get(context, _RUN_VOICE)
    print(f"[VoiceGen] Generating audio for scene {index}: {text[:60]}…")
    _kokoro_status = "Y" if KOKORO_AVAILABLE else f"N -- {_KOKORO_FAIL_REASON or 'not installed'}"
    print(f"[VoiceGen] Voice: '{_active_voice}' (context='{context}') | Kokoro={_kokoro_status}")
    if KOKORO_AVAILABLE:
        if generate_audio_kokoro(text, wav_path, context=context):
            print(f"[VoiceGen][Kokoro] Audio saved -> {wav_path}")
            return wav_path

    print("[VoiceGen] Kokoro unavailable -- falling back to Piper")

    if not _check_piper():
        return None

    # Split text into sentence fragments
    fragments = re.split(r'(?<=[.!?])\s+', text.strip())
    fragments = [f for f in fragments if f.strip()]

    if len(fragments) <= 1:
        # Single fragment -- use original logic
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
            print(f"[VoiceGen] Cannot execute '{PIPER_EXECUTABLE}' -- not found.")
            return None
        except subprocess.TimeoutExpired:
            print("[VoiceGen] Piper timed out.")
            return None
        except Exception as exc:
            print(f"[VoiceGen] Unexpected error: {exc}")
            return None

        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1024:
            print(f"[VoiceGen] Audio saved -> {wav_path}")
            normalize_audio(wav_path)
            return wav_path

        print(f"[VoiceGen] Output file missing or empty after Piper run.")
        return None

    else:
        # Multiple fragments -- generate each, insert pauses, merge
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
                normalize_audio(wav_path)
                return wav_path

        print(f"[VoiceGen] Failed to generate merged audio.")
        return None


def normalize_audio(wav_path: str) -> bool:
    """
    Apply ffmpeg loudnorm filter to normalize WAV to EBU R128 standard.
    Target: -23 LUFS integrated loudness, -1 dBTP true peak.
    Overwrites the original file in-place.
    Returns True on success, False on failure (original preserved).
    """
    import subprocess
    import os

    if not wav_path or not os.path.isfile(wav_path):
        return False

    try:
        _check = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        if _check.returncode != 0:
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("[NORMALIZE] ffmpeg not found - skipping audio normalization")
        return False

    temp_path = wav_path.replace(".wav", "_normalized.wav")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", wav_path,
        "-af", "loudnorm=I=-23:TP=-1:LRA=11",
        "-ar", "22050",
        "-ac", "1",
        "-acodec", "pcm_s16le",
        temp_path
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30
        )
        if result.returncode == 0 and os.path.isfile(temp_path):
            size_original = os.path.getsize(wav_path)
            size_normalized = os.path.getsize(temp_path)
            if size_normalized > 1024:
                os.replace(temp_path, wav_path)
                print(f"[NORMALIZE] Audio normalized: {os.path.basename(wav_path)} "
                      f"({size_original//1024}KB -> {size_normalized//1024}KB)")
                return True
            os.remove(temp_path)
            print("[NORMALIZE] Output too small - keeping original")
        else:
            print(f"[NORMALIZE] ffmpeg failed (rc={result.returncode})")
            if os.path.isfile(temp_path):
                os.remove(temp_path)
    except subprocess.TimeoutExpired:
        print(f"[NORMALIZE] ffmpeg timed out for {os.path.basename(wav_path)}")
        if os.path.isfile(temp_path):
            os.remove(temp_path)
    except Exception as exc:
        print(f"[NORMALIZE] Error: {exc}")
        if os.path.isfile(temp_path):
            os.remove(temp_path)

    return False


def trim_audio_silence(wav_path: str,
                        silence_db: float = -45.0,
                        keep_tail_s: float = 0.08) -> bool:
    """
    Remove excessive trailing silence from a WAV file.
    Uses ffmpeg silencedetect to find where speech ends, then trims
    keeping only `keep_tail_s` seconds of natural decay.

    keep_tail_s=0.08: 80ms tail -- enough for natural sentence ending,
    not enough to create perceivable pause before next scene.

    Returns True if file was modified, False if skipped.
    """
    import subprocess
    import os
    import re as _re

    if not wav_path or not os.path.isfile(wav_path):
        return False

    try:
        _ver = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        if _ver.returncode != 0:
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    # Step 1: detect silence end times
    _detect = subprocess.run(
        ["ffmpeg", "-i", wav_path,
         "-af", f"silencedetect=noise={silence_db}dB:d=0.05",
         "-f", "null", "-"],
        capture_output=True, timeout=15
    )
    _stderr = _detect.stderr.decode("utf-8", errors="replace")

    # Parse audio duration
    _dur_match = _re.search(r"Duration:\s+(\d+):(\d+):([\d.]+)", _stderr)
    if not _dur_match:
        return False
    _total = int(_dur_match[1]) * 3600 + int(_dur_match[2]) * 60 + float(_dur_match[3])

    # Find last silence_end (= where speech stopped)
    _silence_ends = [float(m) for m in _re.findall(r"silence_end:\s*([\d.]+)", _stderr)]
    if not _silence_ends:
        return False   # no silence detected -- audio is speech throughout

    _speech_end = max(_silence_ends)
    
    # PHASE 20: Silence trimming safety checks
    # Ensure we don't truncate very short audio, and only trim if speech ends at least 0.20s before physical end
    if _total - _speech_end < 0.20 or _total < 1.20:
        return False

    _trim_point = min(_speech_end + keep_tail_s, _total)

    # Only trim if we remove more than 0.15s
    if _total - _trim_point < 0.15:
        return False

    # Step 2: trim the file
    _tmp = wav_path.replace(".wav", "_trim.wav")
    _trim = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-t", f"{_trim_point:.4f}",
         "-acodec", "pcm_s16le", _tmp],
        capture_output=True, timeout=30
    )
    if _trim.returncode == 0 and os.path.isfile(_tmp) and os.path.getsize(_tmp) > 512:
        os.replace(_tmp, wav_path)
        return True
    if os.path.isfile(_tmp):
        os.remove(_tmp)
    return False


if __name__ == "__main__":
    path = generate_audio("Hello, this is a test of the Piper text to speech system.", 0)
    print("Result:", path)
