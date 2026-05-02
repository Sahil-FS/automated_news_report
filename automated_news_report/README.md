# AI News-to-Video Generator

Automatically converts the latest BBC news into a short, vertical (1080×1920)
video suitable for TikTok / Instagram Reels / YouTube Shorts — using only free,
local tools.

```
Latest BBC RSS feed
       ↓
  feedparser  →  title + summary
       ↓
   spaCy NLP  →  extractive summary (top-5 sentences)
       ↓
  scene planner →  [text, keyword] × N
       ↓
 Wikimedia API  →  one image per scene
  Piper TTS     →  one WAV per scene
       ↓
   MoviePy     →  1080×1920 MP4
```

---

## Project Structure

```
project/
├── main.py
├── config.py
├── modules/
│   ├── __init__.py
│   ├── news_fetcher.py
│   ├── script_generator.py
│   ├── scene_planner.py
│   ├── image_fetcher.py
│   ├── voice_generator.py
│   └── video_builder.py
├── piper/                    ← you create this (see Step 3)
│   ├── piper                 ← Piper binary
│   ├── en_US-lessac-medium.onnx
│   └── en_US-lessac-medium.onnx.json
├── output/                   ← created automatically
│   ├── audio/
│   ├── images/
│   └── news_video.mp4
└── README.md
```

---

## Installation

### Step 1 — Python dependencies

Install all required Python packages using the provided requirements.txt:

```bash
pip install -r requirements.txt
```

This will install:
- `ddgs` (DuckDuckGo search for image fetching)
- `feedparser` (for RSS feed parsing)
- `spacy` (for natural language processing)
- `numpy` (for numerical operations)
- `Pillow` (for image processing)
- `moviepy` (for video creation)

> **MoviePy ≥ 1.0.3** is required. If `pip install moviepy` installs v2,
> pin it:  `pip install "moviepy==1.0.3"`

### Step 2 — spaCy language model

```bash
python -m spacy download en_core_web_sm
```

### Step 3 — Ollama (for AI script generation)

Ollama is used to generate news scripts using local AI models.

1. Download and install Ollama from https://ollama.com/
2. Pull the required model:
   ```bash
   ollama pull llama3
   ```
3. Ensure Ollama is running in the background.

### Step 4 — Piper TTS (local, offline)

Piper is a fast, local neural TTS engine from Rhasspy.

#### a) Download the Piper binary

Go to https://github.com/rhasspy/piper/releases and download the archive for
your platform, e.g.:

| Platform | File |
|---|---|
| Linux x86-64 | `piper_linux_x86_64.tar.gz` |
| macOS arm64  | `piper_macos_aarch64.tar.gz` |
| Windows      | `piper_windows_amd64.zip` |

Extract into `project/piper/`:

```bash
# Linux example
mkdir -p project/piper
tar -xzf piper_linux_x86_64.tar.gz -C project/piper --strip-components=1
```

#### b) Download a voice model

```bash
cd project/piper

# The ONNX model
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx

# The required JSON config
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

#### c) Make the binary executable (Linux / macOS)

```bash
chmod +x project/piper/piper
```

#### d) Quick test

```bash
echo "Hello world" | ./project/piper/piper \
  --model ./project/piper/en_US-lessac-medium.onnx \
  --output_file /tmp/test.wav
```

> **Tip — Piper not available?**  
> The pipeline still runs without Piper; scenes that have no audio will use a
> fixed 5-second duration instead.  Just install the binary later and re-run.

### Step 4 — ffmpeg (required by MoviePy)

```bash
# Ubuntu / Debian
sudo apt-get install ffmpeg

# macOS (Homebrew)
brew install ffmpeg

# Windows — download from https://ffmpeg.org/download.html
# and add the bin/ folder to your PATH
```

---

## Configuration

All settings live in **`config.py`**:

| Variable | Default | Description |
|---|---|---|
| `RSS_FEED_URL` | BBC News | Any RSS feed URL |
| `NUM_SENTENCES` | `5` | Number of scenes |
| `PIPER_EXECUTABLE` | `./piper/piper` | Path to Piper binary |
| `PIPER_MODEL` | `./piper/en_US-lessac-medium.onnx` | Path to voice model |
| `SCENE_DURATION` | `5` | Fallback scene length (seconds) |
| `OUTPUT_VIDEO` | `output/news_video.mp4` | Final video path |

Override Piper paths with environment variables without editing the file:

```bash
export PIPER_EXECUTABLE=/usr/local/bin/piper
export PIPER_MODEL=/opt/voices/en_US-lessac-medium.onnx
```

---

## Usage

```bash
cd project
python main.py
```

The final video will be written to `output/news_video.mp4`.

### Run individual modules

```bash
# Test the news fetcher
python modules/news_fetcher.py

# Test the summariser
python modules/script_generator.py

# Test image fetching
python modules/image_fetcher.py

# Test Piper TTS
python modules/voice_generator.py

# Smoke-test the video builder (blank clips, no Piper needed)
python modules/video_builder.py
```

---

## How It Works

### 1 · News Fetcher (`feedparser`)
Parses the BBC RSS feed and extracts the `title`, `link`, and `summary` of
the first entry.

### 2 · Script Generator (`spaCy` + `heapq`)
Extractive summarisation in three steps:
1. Tokenise with spaCy; count non-stopword tokens → normalised frequency table.
2. Score each sentence by summing the frequencies of its tokens.
3. Use `heapq.nlargest` to select the top-N sentences; return them in original
   document order.

### 3 · Scene Planner (`spaCy`)
Splits the summary into one sentence = one scene.  For each sentence, extracts
a keyword by priority: named entity → proper noun → common noun → any token.

### 4 · Image Fetcher (Wikimedia Commons API)
Queries `https://en.wikipedia.org/w/api.php?action=query&prop=pageimages…` for
each keyword and downloads the `original` image.  Falls back to a blank dark
clip if the API returns nothing.

### 5 · Voice Generator (`subprocess` + Piper)
Pipes the scene text to the Piper binary:
```
echo "text" | piper --model <onnx> --output_file scene.wav
```
Returns the WAV path, or `None` if Piper is not installed.

### 6 · Video Builder (`MoviePy`)
For each scene:
* Load background image → `resize(height=1920)` → `crop(x_center, 1080×1920)`.
* Apply 60 % dark overlay (`ColorClip + set_opacity`).
* Render wrapped text (≤ 2 lines) centred with drop-shadow using Pillow.
* Attach WAV audio; duration = audio length (or 5 s fallback).

All scenes are concatenated and exported as `libx264` / `aac` MP4.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `OSError: en_core_web_sm not found` | `python -m spacy download en_core_web_sm` |
| `Piper executable not found` | Set `PIPER_EXECUTABLE` or edit `config.py` |
| `ffmpeg not found` | Install ffmpeg and ensure it is on `PATH` |
| Black video, no images | Wikimedia API may rate-limit; the blank fallback is used automatically |
| `moviepy` import error | Pin version: `pip install "moviepy==1.0.3"` |

---

## License

MIT — use freely, no attribution required.

---

## Additional Project Context

This repository builds a full local AI news-to-video generator that converts the latest BBC News RSS story into a vertical social video.

### What we are making

- A complete pipeline that turns news content into a short, vertical video suitable for TikTok / Instagram Reels / YouTube Shorts.
- The pipeline fetches the latest article, generates a concise script, plans story scenes, fetches supporting visuals, renders local speech, and composes the final MP4.
- The goal is to keep the process local where possible and use free / accessible tools with offline models.

### Core files and their relationships

- `main.py` — orchestrates the pipeline end to end and resets `output/` for a fresh run.
- `config.py` — central settings for directories, feed URL, Piper model path, video resolution, and API endpoints.
- `news_fetcher.py` — uses `feedparser` to parse BBC RSS and return the latest article title, link, and summary.
- `script_generator.py` — uses spaCy NLP plus custom heuristics to clean text, score content, detect context, and build a short news narration.
- `scene_planner.py` — splits the generated script into scenes, extracts named entities and keywords, and creates metadata used by image/audio generation.
- `image_fetcher.py` — builds semantic image queries, fetches images from Pexels (primary), falls back to Wikimedia/Wikipedia when needed, and can generate a location map stinger.
- `voice_generator.py` — runs the Piper TTS binary with a local ONNX model to produce scene WAV files.
- `video_builder.py` — uses MoviePy + Pillow to render each vertical scene with images, overlays, text cards, branding, and audio.
- `video_review.py` — helper script that runs `main.py` inside `.venv`, validates the video output, and reports quality metrics.
- `PROJECT_DOCUMENTATION.md` and `VIDEO_QUALITY_ANALYSIS_REPORT.md` — supporting docs for design and review, outside the runtime pipeline.

### Technology used

- Python 3
- feedparser for RSS parsing
- spaCy (`en_core_web_sm`) for NLP, summarisation, sentence splitting, and named entity recognition
- MoviePy for video composition
- Pillow for text rendering and graphic overlays
- NumPy for timing and image array handling
- Piper TTS with local ONNX voice model for audio generation
- Pexels API for realistic photo sourcing
- Wikipedia/Wikimedia API fallback for missing images
- staticmap + geopy for generating location map stingers
- ffmpeg (required by MoviePy)

### Data sources and AI models

- News source: BBC News RSS feed at `https://feeds.bbci.co.uk/news/rss.xml`
- Image source: Pexels API (`https://www.pexels.com/api/`) with fallback to Wikipedia Commons
- TTS model: `rhasspy/piper-voices` `en_US-lessac-medium.onnx`
- NLP model: spaCy `en_core_web_sm`

### What changed in this repo

- Added stronger script quality rules and validation in `script_generator.py`.
- Added richer scene planning with entity extraction in `scene_planner.py`.
- Added semantic and type-aware image query generation in `image_fetcher.py`.
- Added optional location map stinger generation for stories with geographic entities.
- Added strict `.venv` checks in core modules to ensure the right environment is used.
- Added `video_review.py` to validate end-to-end pipeline execution and video duration.
- Improved `video_builder.py` styling with a gradient overlay, text card, branding bar, and fallback image handling.

### Output structure

- `output/audio/` — generated WAV files for each scene
- `output/images/` — downloaded scene images and map stinger
- `output/news_video.mp4` — final vertical news video
