# config.py -- Central configuration for AI News-to-Video Generator

import os

# Load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed -- use OS environment variables directly

DEBUG_MODE = os.environ.get("DEBUG_MODE", "true").lower() == "true"

# â”€â”€ Paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
AUDIO_DIR  = os.path.join(OUTPUT_DIR, "audio")
IMAGE_DIR  = os.path.join(OUTPUT_DIR, "images")

for d in (OUTPUT_DIR, AUDIO_DIR, IMAGE_DIR):
    os.makedirs(d, exist_ok=True)

# â”€â”€ News source â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
RSS_FEED_URL = "https://feeds.bbci.co.uk/news/rss.xml"

# â”€â”€ Script generation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
NUM_SENTENCES = 5          # scenes in the final video

# â”€â”€ Piper TTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Download a model from: https://github.com/rhasspy/piper/releases
# Example (Linux x86-64):
#   wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_x86_64.tar.gz
#   tar -xzf piper_linux_x86_64.tar.gz
# Point PIPER_EXECUTABLE at the extracted `piper` binary.
PIPER_EXECUTABLE = os.environ.get("PIPER_EXECUTABLE", "piper/piper.exe")
PIPER_MODEL      = os.environ.get(
    "PIPER_MODEL",
    "./piper/en_US-lessac-medium.onnx"
)

# â”€â”€ Video â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
VIDEO_WIDTH   = 1080
VIDEO_HEIGHT  = 1920
VIDEO_FPS     = 24
SCENE_DURATION = 5          # seconds per scene (overridden by audio length)
OUTPUT_VIDEO   = os.path.join(OUTPUT_DIR, "news_video.mp4")


def _ensure_placeholder():
    """Create a default placeholder image if it doesn't exist."""
    from PIL import Image as _PI, ImageDraw as _PID

    _path = os.path.join(BASE_DIR, "assets", "placeholder.jpg")
    os.makedirs(os.path.dirname(_path), exist_ok=True)
    if os.path.isfile(_path):
        return

    _img = _PI.new("RGB", (1080, 1920), (13, 17, 35))
    _draw = _PID.Draw(_img)
    try:
        from PIL import ImageFont
        _font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 80)
    except Exception:
        from PIL import ImageFont
        _font = ImageFont.load_default()
    _draw.text((540, 960), "AI NEWS", font=_font, fill=(220, 30, 30), anchor="mm")
    _img.save(_path, "JPEG", quality=85)
    print(f"[ASSETS] Created placeholder image: {_path}")


_ensure_placeholder()

# â”€â”€ Wikimedia Commons â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_HEADERS = {"User-Agent": "NewsVideoBot/1.0 (educational project)"}

# â”€â”€ Scene Planning â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SCENE_MIN_WORDS       = 8     # minimum words per scene caption
SCENE_MAX_WORDS       = 18    # PHASE 21: raised from 14 â€” allows complete news sentences
SCENE_TARGET_MIN      = 8     # minimum number of scenes
SCENE_TARGET_MAX      = 9     # maximum number of scenes

# â”€â”€ Video Timeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
MAX_SCENE_TOTAL_SECS  = 85.0  # maximum total content duration before compression
MIN_SCENE_TOTAL_SECS  = 35.0  # minimum total duration before expansion
MAX_SCENE_DURATION    = 9.0   # per-scene audio cap
MIN_SCENE_DURATION    = 2.0   # per-scene audio floor
VISUAL_CUT_THRESHOLD  = 2.8   # scenes longer than this get a B-roll mid-cut
CAPTION_LEAD_SECS     = 0.10  # caption appears this many seconds before spoken word

# â”€â”€ Audio Post-Processing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
PAUSE_AFTER_SENTENCE  = 0.40  # silence gap between Piper TTS sentence fragments
AUDIO_TAIL_BUFFER     = 0.30  # silence buffer added to final scene
SILENCE_KEEP_TAIL     = 0.08  # natural decay tail preserved after trimming
MAP_STINGER_DURATION  = 5.0   # default; overridden by hook audio in practice
MAP_STINGER_MAX       = 12.0  # hard ceiling for extreme hook lengths
MAP_STINGER_MIN       = 3.5   # minimum duration

# â”€â”€ Script Generation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
OLLAMA_URL            = "http://localhost:11434/api/generate"
OLLAMA_MODEL          = "llama3"
OOV_THRESHOLD         = 20    # PHASE 21: raised from 18 â€” prevents false relevance rejections
SCRIPT_MIN_WORDS      = 80    # minimum acceptable script word count



