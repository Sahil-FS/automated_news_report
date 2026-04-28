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

```bash
pip install feedparser spacy moviepy Pillow numpy
```

> **MoviePy ≥ 1.0.3** is required. If `pip install moviepy` installs v2,
> pin it:  `pip install "moviepy==1.0.3"`

### Step 2 — spaCy language model

```bash
python -m spacy download en_core_web_sm
```

### Step 3 — Piper TTS (local, offline)

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
