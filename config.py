# config.py — Central configuration for AI News-to-Video Generator

import os

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
AUDIO_DIR  = os.path.join(OUTPUT_DIR, "audio")
IMAGE_DIR  = os.path.join(OUTPUT_DIR, "images")

for d in (OUTPUT_DIR, AUDIO_DIR, IMAGE_DIR):
    os.makedirs(d, exist_ok=True)

# ── News source ───────────────────────────────────────────────────────────────
RSS_FEED_URL = "https://feeds.bbci.co.uk/news/rss.xml"

# ── Script generation ─────────────────────────────────────────────────────────
NUM_SENTENCES = 5          # scenes in the final video

# ── Piper TTS ─────────────────────────────────────────────────────────────────
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

# ── Video ──────────────────────────────────────────────────────────────────────
VIDEO_WIDTH   = 1080
VIDEO_HEIGHT  = 1920
VIDEO_FPS     = 24
SCENE_DURATION = 5          # seconds per scene (overridden by audio length)
OUTPUT_VIDEO   = os.path.join(OUTPUT_DIR, "news_video.mp4")

# ── Wikimedia Commons ─────────────────────────────────────────────────────────
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_HEADERS = {"User-Agent": "NewsVideoBot/1.0 (educational project)"}
