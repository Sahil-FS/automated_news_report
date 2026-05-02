# video_builder.py — 1080×1920 vertical video with styled visuals
# Upgrades: bottom gradient, pill text card, branding bar

import sys
import os
import textwrap

# PHASE 4: Environment lock
if ".venv" not in sys.executable:
    print("❌ ERROR: Not running inside .venv")
    print(f"Current: {sys.executable}")
    print("Run using: .venv\\Scripts\\python.exe main.py")
    exit(1)

import numpy as np


def distribute_word_timings(words, total_duration):
    weights = []

    for w in words:
        if len(w) <= 3:
            weights.append(0.8)
        elif len(w) <= 6:
            weights.append(1.0)
        else:
            weights.append(1.2)

    weights = np.array(weights)
    weights = weights / weights.sum()

    durations = weights * total_duration

    min_duration = 0.20
    durations = np.maximum(durations, min_duration)

    durations = durations * (total_duration / durations.sum())

    timings = []
    current_time = 0

    for d in durations:
        timings.append((current_time, current_time + d))
        current_time += d

    return timings


from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy import (
    ImageClip,
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    CompositeAudioClip,
    concatenate_videoclips,
    vfx
)
from moviepy.video.VideoClip import VideoClip
from moviepy.audio.AudioClip import AudioArrayClip

from config import (
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
    SCENE_DURATION,
    OUTPUT_VIDEO,
)
 
# ── Branding ──────────────────────────────────────────────────────────────────
BRAND_NAME   = "AI NEWS"          # shown in bottom bar — change freely
ACCENT_COLOR = (220, 50, 50)      # red accent  (R, G, B)
BAR_HEIGHT   = 90                 # px — branding bar at very bottom
 
# ── Font loader ───────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = True):
    candidates_bold = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    candidates_reg = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in (candidates_bold if bold else candidates_reg):
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()
 
 
# ── Layer 1 — Background image ────────────────────────────────────────────────
def _make_background(image_path: str | None, W: int, H: int) -> np.ndarray:
    """Return a (H, W, 3) uint8 array — image or dark colour fill."""
    print(f"DEBUG image_path: {image_path}")
 
    if image_path and os.path.isfile(image_path):
        try:
            img = Image.open(image_path).convert("RGB")
            print(f"DEBUG image size (original): {img.width}x{img.height}")
 
            # Scale to FILL the frame — cover mode (no empty bars)
            scale_h = H / img.height
            scale_w = W / img.width
            scale   = max(scale_h, scale_w)          # cover, never letterbox
            new_w   = max(int(img.width  * scale), W)
            new_h   = max(int(img.height * scale), H)
            img = img.resize((new_w, new_h), Image.LANCZOS)
 
            # Centre-crop to exact frame size
            left = (new_w - W) // 2
            top  = (new_h - H) // 2
            img  = img.crop((left, top, left + W, top + H))
 
            # Mild blur so text layer pops
            img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
            print(f"DEBUG image rendered OK: {img.width}x{img.height}")
            return np.array(img)
        except Exception as exc:
            print(f"[VideoBuilder] Image load failed: {exc}")
 
    # Fallback — dark blue-grey solid (more interesting than pure black)
    print("DEBUG image_path: using fallback solid background")
    base = np.zeros((H, W, 3), dtype=np.uint8)
    base[:, :] = [22, 28, 40]
    return base
 
 
# ── Layer 2 — Bottom gradient overlay ────────────────────────────────────────
def _make_gradient_overlay(W: int, H: int) -> np.ndarray:
    """
    RGBA layer: fully transparent at top --> 85 % opaque black at bottom.
    Covers the lower 55 % of the frame so the image breathes at the top.
    """
    overlay = np.zeros((H, W, 4), dtype=np.uint8)
    grad_start = int(H * 0.35)          # gradient begins 35 % from top
    grad_pixels = H - grad_start
 
    for y in range(grad_start, H):
        t = (y - grad_start) / grad_pixels   # 0 --> 1
        # Ease-in curve: slow start, strong finish
        alpha = int(215 * (t ** 1.6))
        overlay[y, :, 3] = min(alpha, 215)
 
    return overlay
 
 
# ── Layer 3 — Styled text card ────────────────────────────────────────────────
def _make_text_card(text: str, W: int, H: int, highlight_word: str = "") -> np.ndarray:
    """
    Renders:
      • A semi-transparent rounded-rect pill behind the text
      • Main scene text (2-line max, bold, white)
      • Current word highlighted in yellow for cinematic feel
    Positioned in the lower-third of the frame.
    """
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)
 
    font_size = 48
    font      = _font(font_size, bold=True)
    max_chars = 28
 
    max_width = int(W * 0.8)
    wrapped = " ".join(text.split())
    wrapped = "\n".join(textwrap.wrap(wrapped, width=28))
    line_text = wrapped

    # Measure
    try:
        bbox = draw.multiline_textbbox((0, 0), line_text, font=font, spacing=14)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except AttributeError:
        tw, th = draw.multiline_textsize(line_text, font=font, spacing=14)

    pad_x, pad_y = 48, 28
    card_w = tw + pad_x * 2
    card_h = th + pad_y * 2

    # Horizontal centre; positioned in lower-third
    cx = (W - card_w) // 2
    cy = int(H * 0.68)
 
    # Pill background  (dark, 75 % opacity)
    pill = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(pill)
    radius = 22
    pdraw.rounded_rectangle(
        [(0, 0), (card_w - 1, card_h - 1)],
        radius=radius,
        fill=(10, 10, 10, 190),
    )
    # Accent left border stripe
    pdraw.rounded_rectangle(
        [(0, 0), (8, card_h - 1)],
        radius=radius,
        fill=(*ACCENT_COLOR, 255),
    )
    canvas.alpha_composite(pill, (cx, cy))
 
    # Text drawing
    tx = cx + pad_x
    ty = cy + pad_y

    # NO shadows, NO duplicate layers, NO overlay.
    # Draw word by word with clean highlight logic.
    lines = line_text.split("\n")
    line_y = ty
    
    for line in lines:
        line_words = line.split()
        word_x = tx
        for w in line_words:
            # CLEAN HIGHLIGHT LOGIC
            color = "yellow" if w == highlight_word else "white"
            fill_color = (255, 215, 0, 255) if color == "yellow" else (255, 255, 255, 255)
            
            draw.text((word_x, line_y), w, font=font, fill=fill_color)
            
            try:
                space_bb = draw.textbbox((0, 0), w + " ", font=font)
                word_x += space_bb[2] - space_bb[0]
            except AttributeError:
                word_x += draw.textsize(w + " ", font=font)[0]
        
        try:
            lh = draw.textbbox((0, 0), "A", font=font)
            line_y += (lh[3] - lh[1]) + 20
        except AttributeError:
            line_y += draw.textsize("A", font=font)[1] + 14

    return np.array(canvas)


def _make_text_layer(text, W, H):
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    font = _font(48, bold=True)

    wrapped = " ".join(text.split())
    wrapped = "\n".join(textwrap.wrap(wrapped, width=28))

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=12)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    padding_x = 40
    padding_y = 30

    box_w = text_w + padding_x
    box_h = text_h + padding_y

    cx = (W - box_w) // 2
    cy = int(H * 0.68)

    draw.rounded_rectangle(
        [(cx, cy), (cx + box_w, cy + box_h)],
        radius=25,
        fill=(10, 10, 10, 180)
    )

    draw.multiline_text(
        (cx + padding_x // 2, cy + padding_y // 2),
        wrapped,
        font=font,
        fill=(255, 255, 255, 255),
        spacing=12
    )

    return np.array(canvas)


import re

def clean_caption_text(text):
    text = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\b\d+[A-Z]\b', '', text)
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── Progressive caption renderer (frame-based, no TextClip stacking) ─────────
def _make_progressive_caption(text, duration, W, H):
    """
    Frame-based progressive caption (NO stacking).
    Shows words gradually based on time.
    """

    original_text = text
    text = clean_caption_text(original_text)

    print("[RAW TEXT]:", repr(original_text))
    print("[CLEAN TEXT]:", text)

    words = text.split()
    words = [w.strip() for w in words if w.strip()]

    clean_words = []
    prev_word = None

    for w in words:
        if w != prev_word:
            clean_words.append(w)
            prev_word = w

    words = clean_words

    print(f"[WORDS]: {words}")

    total_words = len(words)

    if total_words == 0:
        return None

    print(f"[CLEAN CAPTION TEXT]: {text}")
    print(f"[Caption] words={len(words)}, duration={duration:.2f}")
    
    timings = distribute_word_timings(words, duration)
    assert len(words) == len(timings), "Mismatch: words vs timings"

    def make_frame(t):
        # Step 1 — accurate word index using timings
        word_index = 0
        for i, (start, end) in enumerate(timings):
            if t >= start:
                word_index = i
            else:
                break

        if word_index >= total_words:
            word_index = total_words - 1
                
        visible_words = words[:word_index + 1]

        # Step 2 — limit to last 10 words for readable display
        mobile = True
        max_words = 12 if mobile else 14
        visible_words = visible_words[-max_words:]

        # Cinematic highlight — current (last) word shown in yellow
        highlight_word = visible_words[-1] if visible_words else ""

        caption_text = " ".join(visible_words)

        frame = _make_text_card(caption_text, W, H, highlight_word=highlight_word)
        return frame

    clip = VideoClip()
    clip.frame_function = make_frame
    clip.duration = duration
    clip.size = (W, H)

    return clip


# ── Scene clip assembler ──────────────────────────────────────────────────────
def _make_breaking_news_banner(
    W: int, H: int,
    label: str = "🔴  BREAKING NEWS",
    bar_color: tuple = (220, 30, 30),
) -> np.ndarray:
    """
    Renders a 'BREAKING NEWS' red banner strip at the top of the frame.
    Only used for scene index 0.
    Height: 72px. Style: red background, white bold text, pulsing dot.
    Returns RGBA numpy array.
    """
    BAR_H = 72
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    # Red background strip (full width, top of frame)
    draw.rectangle([(0, 0), (W, BAR_H)], fill=(*bar_color, 240))

    # White divider line at bottom of bar
    draw.rectangle([(0, BAR_H - 2), (W, BAR_H)], fill=(255, 255, 255, 180))

    # "BREAKING NEWS" text
    font_breaking = _font(30, bold=True)
    try:
        tb = draw.textbbox((0, 0), label, font=font_breaking)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]
    except AttributeError:
        tw, th = draw.textsize(label, font=font_breaking)

    text_y = (BAR_H - th) // 2
    # Draw text shadow
    draw.text((22, text_y + 2), label, font=font_breaking, fill=(0, 0, 0, 100))
    # Draw text
    draw.text((20, text_y), label, font=font_breaking, fill=(255, 255, 255, 255))

    # "LIVE" badge on the right
    font_live = _font(22, bold=True)
    live_label = "LIVE"
    try:
        lb = draw.textbbox((0, 0), live_label, font=font_live)
        lw = lb[2] - lb[0]
        lh = lb[3] - lb[1]
    except AttributeError:
        lw, lh = draw.textsize(live_label, font=font_live)

    live_x = W - lw - 30
    live_y = (BAR_H - lh) // 2
    # Badge background
    draw.rounded_rectangle(
        [(live_x - 12, live_y - 6), (live_x + lw + 12, live_y + lh + 6)],
        radius=6,
        fill=(255, 255, 255, 255)
    )
    draw.text((live_x, live_y), live_label, font=font_live, fill=(*bar_color, 255))

    return np.array(canvas)


def _make_lower_third(headline: str, source: str, location: str,
                      W: int, H: int) -> np.ndarray:
    """
    Renders a professional news lower-third overlay.
    Positioned in the bottom 18% of the frame, above the brand bar area.

    Layout:
      ┌──────────────────────────────────────────┐
      │ [RED BAR] HEADLINE TEXT          SOURCE  │
      │           Location / Context             │
      └──────────────────────────────────────────┘

    Returns RGBA numpy array.
    """
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    STRIP_H   = 110
    STRIP_Y   = H - STRIP_H - BAR_HEIGHT - 10   # just above brand bar
    ACCENT_W  = 10

    # Dark semi-transparent background strip
    draw.rectangle(
        [(0, STRIP_Y), (W, STRIP_Y + STRIP_H)],
        fill=(8, 8, 18, 210)
    )

    # Red left accent bar
    draw.rectangle(
        [(0, STRIP_Y), (ACCENT_W, STRIP_Y + STRIP_H)],
        fill=(*ACCENT_COLOR, 255)
    )

    # White top border line
    draw.rectangle(
        [(0, STRIP_Y), (W, STRIP_Y + 2)],
        fill=(255, 255, 255, 60)
    )

    # Headline text — truncate to fit width
    font_headline = _font(34, bold=True)
    max_headline_chars = 38
    display_headline = (headline[:max_headline_chars] + "…"
                        if len(headline) > max_headline_chars else headline)
    display_headline = display_headline.upper()

    text_x  = ACCENT_W + 18
    text_y1 = STRIP_Y + 14

    # Headline
    draw.text((text_x, text_y1), display_headline,
              font=font_headline, fill=(255, 255, 255, 255))

    # Location / context line below headline
    font_location = _font(24, bold=False)
    display_location = location if location else source
    draw.text((text_x, text_y1 + 46), display_location,
              font=font_location, fill=(200, 200, 200, 210))

    # Source label — right-aligned
    font_source = _font(22, bold=False)
    source_label = f"Source: {source}"
    try:
        sb = draw.textbbox((0, 0), source_label, font=font_source)
        sw = sb[2] - sb[0]
    except AttributeError:
        sw, _ = draw.textsize(source_label, font=font_source)

    draw.text((W - sw - 20, text_y1 + 50), source_label,
              font=font_source, fill=(160, 160, 160, 200))

    return np.array(canvas)


def _generate_whoosh(duration: float = 0.18) -> np.ndarray | None:
    """
    Generate a 0.18s whoosh sound effect using numpy.
    Technique: exponential frequency sweep from 800Hz down to 120Hz
    with rapid volume fade-out. Sounds like a fast camera cut swoosh.
    Returns stereo float32 array shaped (samples, 2) for MoviePy,
    or None on failure.
    """
    try:
        SAMPLE_RATE = 44100
        n = int(SAMPLE_RATE * duration)
        t = np.linspace(0, duration, n, endpoint=False)

        # Exponential frequency sweep: 800Hz → 120Hz
        f_start, f_end = 800.0, 120.0
        freq = f_start * (f_end / f_start) ** (t / duration)
        phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
        wave = np.sin(phase)

        # Amplitude envelope: fast attack, rapid exponential decay
        envelope = np.exp(-t * 18.0)
        wave = wave * envelope * 0.12   # 12% volume

        # Stereo
        stereo = np.stack([wave, wave], axis=1).astype(np.float32)
        return stereo
    except Exception as exc:
        print(f"[WHOOSH] Generation failed: {exc}")
        return None


def _build_scene_clip(
    image_path: str | None,
    audio_path: str | None,
    text: str,
    scene_idx: int,
    final_duration: float,
    headline: str = "",
    news_source: str = "BBC News",
    location: str = "",
    scene_type: str = "general",
) -> CompositeVideoClip:
    W, H = VIDEO_WIDTH, VIDEO_HEIGHT

    if audio_path and os.path.isfile(audio_path):
        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration + 0.3

        # ── Whoosh SFX prepend ────────────────────────────────────────────
        # Generate a short swoosh and prepend to scene audio for a
        # professional transition feel (12% volume, 0.18s).
        whoosh_data = _generate_whoosh(duration=0.18)
        if whoosh_data is not None:
            try:
                whoosh_clip = AudioArrayClip(whoosh_data, fps=44100)
                whoosh_clip = whoosh_clip.with_volume_scaled(1.0)
                # Mix whoosh with voiceover — whoosh at start, voice throughout
                audio_clip = CompositeAudioClip([
                    whoosh_clip,
                    audio_clip
                ])
            except Exception as exc:
                print(f"[WHOOSH] Mix failed: {exc} — skipping whoosh")
    else:
        audio_clip = None
        duration = final_duration

    duration = final_duration  # keep final_duration as master (already includes buffer via build_video)
 
    def make_frame(_t):
        return _make_background(image_path, W, H)
 
    # ── 2.5s Visual Cut Rule ─────────────────────────────────────────────
    # If scene is longer than 2.8s, split into two visual sub-clips
    # with different images. Audio plays continuously.
    CUT_THRESHOLD = 2.8  # seconds

    if duration > CUT_THRESHOLD and image_path:
        cut_point = duration / 2  # cut at midpoint

        # Primary image — first half
        bg_arr_a = _make_background(image_path, W, H)
        bg_a = (
            ImageClip(bg_arr_a)
            .with_duration(cut_point)
            .resized(lambda t: 1 + 0.04 * t)
        )

        # Try to get a second image for variety
        alt_image_path = _fetch_second_image(
            {"text": text, "type": scene.get("type", "general")
             if hasattr(scene, "get") else "general",
             "keyword": "", "entities": {}},
            scene_idx
        ) if False else None  # disabled inline — uses scene dict below

        # Build background for second half
        bg_arr_b = _make_background(
            alt_image_path if alt_image_path else image_path, W, H
        )
        bg_b = (
            ImageClip(bg_arr_b)
            .with_duration(duration - cut_point)
            .resized(lambda t: 1 + 0.04 * t)
        )

        # Concatenate the two visual halves
        from moviepy import concatenate_videoclips
        bg = concatenate_videoclips([bg_a, bg_b], method="compose")

    else:
        # Short scene — single image with Ken Burns zoom
        bg_arr = _make_background(image_path, W, H)
        bg = (
            ImageClip(bg_arr)
            .with_duration(duration)
            .resized(lambda t: 1 + 0.06 * t)
        )
 
    # Layer 2 — gradient
    grad_arr = _make_gradient_overlay(W, H)
    grad = ImageClip(grad_arr).with_duration(duration)
 
    # Layer 3 — progressive caption (word-by-word)
    caption_clip = _make_progressive_caption(text, duration, W, H)

    # Debug logging
    print(f"[Scene] duration={duration:.2f}s, words={len(text.split())}")
 
    # Layer 4 — Lower third (all scenes)
    lower_arr = _make_lower_third(headline, news_source, location, W, H)
    lower_clip = ImageClip(lower_arr).with_duration(duration)

    # Layer 5 — Category banner (BREAKING on scene 0, type tag on others)
    CATEGORY_STYLES = {
        "war":        ("⚡  WAR",        (180, 30,  30)),
        "politics":   ("🏛  POLITICS",   (20,  60,  160)),
        "technology": ("💡  TECH",       (20,  120, 80)),
        "business":   ("📈  ECONOMY",    (150, 80,  20)),
        "disaster":   ("🚨  DISASTER",   (160, 60,  20)),
        "general":    ("📡  NEWS",       (40,  40,  100)),
        "hook":       ("🔴  BREAKING",   (220, 30,  30)),
        "context":    ("🌍  CONTEXT",    (40,  40,  100)),
        "event":      ("📰  DEVELOPING", (100, 40,  120)),
    }

    layers = [bg.with_position("center"), grad, caption_clip, lower_clip]

    if scene_idx == 0:
        # Always BREAKING NEWS red on first scene
        banner_arr  = _make_breaking_news_banner(W, H)
    else:
        cat_label, cat_color = CATEGORY_STYLES.get(
            scene_type if scene_type else "general",
            ("📡  NEWS", (40, 40, 100))
        )
        banner_arr = _make_breaking_news_banner(
            W, H, label=cat_label, bar_color=cat_color
        )

    banner_clip = ImageClip(banner_arr).with_duration(duration)
    layers.append(banner_clip)

    scene = CompositeVideoClip(layers, size=(W, H)).with_duration(duration)
    if audio_clip:
        scene = scene.with_audio(audio_clip)
 
    print(
        f"[VideoBuilder] Scene {scene_idx:02d} — {duration:.1f}s | "
        f"audio={'Y' if audio_clip else 'N'} | "
        f"image={'Y' if image_path else 'N'}"
    )
    scene = scene.with_effects([
        vfx.FadeIn(0.2),
        vfx.FadeOut(0.3)
    ])
    return scene


def _fetch_second_image(scene: dict, index: int) -> str | None:
    """
    Fetch an alternative image for a scene's second visual sub-clip.
    Uses the second-ranked query from _build_query() to ensure
    visual variety — different query, different image.
    Returns image path or None.
    """
    try:
        from image_fetcher import _build_query, fetch_with_retry, \
                                  clean_query, _download, IMAGE_DIR
        import hashlib, os

        ranked = _build_query(scene)
        # Skip the first query (already used for primary image)
        # Try queries 2, 3, 4 for a fresh image
        for alt_query in ranked[1:4]:
            alt_query = clean_query(alt_query)
            if not alt_query:
                continue
            alt_url = fetch_with_retry(alt_query, index + 1000)
            if alt_url:
                cache_key = hashlib.md5(alt_query.encode()).hexdigest()[:10]
                dest = os.path.join(
                    IMAGE_DIR,
                    f"scene_{index:02d}_b_{cache_key}.jpg"
                )
                import requests
                try:
                    r = requests.get(alt_url, timeout=10)
                    if r.status_code == 200:
                        with open(dest, "wb") as f:
                            f.write(r.content)
                        if os.path.getsize(dest) > 2048:
                            print(f"[VISUAL CUT] Alt image fetched for scene {index}")
                            return dest
                except Exception:
                    continue
    except Exception as exc:
        print(f"[VISUAL CUT] Alt image fetch failed: {exc}")
    return None


# ── Public entry-point ────────────────────────────────────────────────────────
def _build_outro_scene(duration: float = 5.0) -> CompositeVideoClip:
    """
    Premium news-style outro scene — no image needed.

    Layout (top to bottom):
      ┌──────────────────────────────────┐
      │  [RED BAR]  AI NEWS       LIVE  │  ← top branding (72px)
      │                                  │
      │         [WORLD MAP ICON]         │  ← decorative circle
      │                                  │
      │   Stay Updated on World Affairs  │  ← main headline
      │   Follow for daily news reels    │  ← subtitle
      │                                  │
      │   [ 🔔  SUBSCRIBE ]             │  ← fades in at t=1.5s
      │                                  │
      │   AI NEWS  ·  Source: BBC News   │  ← bottom credit
      └──────────────────────────────────┘
    """
    W, H = VIDEO_WIDTH, VIDEO_HEIGHT

    # ── Static background frame ───────────────────────────────────────────
    def _make_outro_bg() -> np.ndarray:
        canvas = Image.new("RGB", (W, H), (6, 8, 18))
        draw   = ImageDraw.Draw(canvas)

        # Vertical gradient — dark navy to near-black
        for y in range(H):
            t = y / H
            r = int(6  + (14 - 6)  * (1 - t))
            g = int(8  + (20 - 8)  * (1 - t))
            b = int(18 + (40 - 18) * (1 - t))
            draw.line([(0, y), (W, y)], fill=(r, g, b))

        # Decorative red accent circle (world icon feel)
        cx, cy, radius = W // 2, int(H * 0.32), 110
        draw.ellipse(
            [(cx - radius, cy - radius), (cx + radius, cy + radius)],
            outline=(*ACCENT_COLOR, 180), width=4
        )
        # Cross lines inside circle (globe lines)
        draw.line([(cx, cy - radius), (cx, cy + radius)],
                  fill=(*ACCENT_COLOR, 80), width=2)
        draw.line([(cx - radius, cy), (cx + radius, cy)],
                  fill=(*ACCENT_COLOR, 80), width=2)
        draw.arc(
            [(cx - radius, cy - radius // 2),
             (cx + radius, cy + radius // 2)],
            start=0, end=360,
            fill=(*ACCENT_COLOR, 50), width=1
        )

        # Horizontal divider line
        div_y = int(H * 0.52)
        draw.rectangle([(60, div_y), (W - 60, div_y + 2)],
                        fill=(220, 30, 30, 160))

        return np.array(canvas)

    # ── Subscribe button frame (PIL — no external assets) ─────────────────
    def _make_subscribe_btn(alpha: float) -> np.ndarray:
        """
        Renders the subscribe button as RGBA with given opacity (0.0–1.0).
        alpha is used to animate fade-in.
        """
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw   = ImageDraw.Draw(canvas)

        btn_w, btn_h = 420, 90
        btn_x = (W - btn_w) // 2
        btn_y = int(H * 0.67)

        opacity = int(255 * min(max(alpha, 0.0), 1.0))

        # Button background — red pill
        draw.rounded_rectangle(
            [(btn_x, btn_y), (btn_x + btn_w, btn_y + btn_h)],
            radius=45,
            fill=(220, 30, 30, opacity)
        )

        # Bell icon drawn with PIL shapes (left side of button)
        bell_cx = btn_x + 60
        bell_cy = btn_y + btn_h // 2
        bell_r  = 18
        # Bell dome
        draw.ellipse(
            [(bell_cx - bell_r, bell_cy - bell_r),
             (bell_cx + bell_r, bell_cy + bell_r // 2)],
            fill=(255, 255, 255, opacity)
        )
        # Bell handle
        draw.rectangle(
            [(bell_cx - 5, bell_cy + bell_r // 2),
             (bell_cx + 5, bell_cy + bell_r)],
            fill=(255, 255, 255, opacity)
        )
        # Bell clapper (small dot below)
        draw.ellipse(
            [(bell_cx - 5, bell_cy + bell_r),
             (bell_cx + 5, bell_cy + bell_r + 8)],
            fill=(255, 255, 255, opacity)
        )

        # SUBSCRIBE text
        font_btn = _font(36, bold=True)
        btn_label = "SUBSCRIBE"
        try:
            tb = draw.textbbox((0, 0), btn_label, font_btn)
            tw = tb[2] - tb[0]
            th = tb[3] - tb[1]
        except AttributeError:
            tw, th = draw.textsize(btn_label, font=font_btn)

        text_x = btn_x + 95
        text_y = btn_y + (btn_h - th) // 2
        draw.text((text_x, text_y), btn_label,
                  font=font_btn, fill=(255, 255, 255, opacity))

        return np.array(canvas)

    # ── Static text layer ─────────────────────────────────────────────────
    def _make_outro_text() -> np.ndarray:
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw   = ImageDraw.Draw(canvas)

        # Main headline
        font_main = _font(52, bold=True)
        main_text = "Stay Updated"
        try:
            mb = draw.textbbox((0, 0), main_text, font_main)
            mw = mb[2] - mb[0]
        except AttributeError:
            mw, _ = draw.textsize(main_text, font=font_main)
        draw.text(((W - mw) // 2, int(H * 0.54)),
                  main_text, font=font_main,
                  fill=(255, 255, 255, 255))

        # Sub headline
        font_sub = _font(38, bold=False)
        sub_text = "on World Affairs"
        try:
            sb = draw.textbbox((0, 0), sub_text, font_sub)
            sw = sb[2] - sb[0]
        except AttributeError:
            sw, _ = draw.textsize(sub_text, font=font_sub)
        draw.text(((W - sw) // 2, int(H * 0.54) + 62),
                  sub_text, font=font_sub,
                  fill=(200, 200, 200, 220))

        # Subtitle
        font_caption = _font(28, bold=False)
        cap_text = "Daily news reels  ·  Real stories  ·  No noise"
        try:
            cb = draw.textbbox((0, 0), cap_text, font_caption)
            cw = cb[2] - cb[0]
        except AttributeError:
            cw, _ = draw.textsize(cap_text, font=font_caption)
        draw.text(((W - cw) // 2, int(H * 0.62)),
                  cap_text, font=font_caption,
                  fill=(140, 140, 160, 200))

        # Bottom credit line
        font_credit = _font(24, bold=False)
        credit_text = f"{BRAND_NAME}  ·  Powered by AI"
        try:
            crb = draw.textbbox((0, 0), credit_text, font_credit)
            crw = crb[2] - crb[0]
        except AttributeError:
            crw, _ = draw.textsize(credit_text, font=font_credit)
        draw.text(((W - crw) // 2, int(H * 0.88)),
                  credit_text, font=font_credit,
                  fill=(100, 100, 120, 180))

        return np.array(canvas)

    # ── Breaking banner for outro ─────────────────────────────────────────
    outro_banner_arr = _make_breaking_news_banner(W, H)

    # ── Assemble layers ───────────────────────────────────────────────────
    bg_arr   = _make_outro_bg()
    text_arr = _make_outro_text()

    bg_clip   = ImageClip(bg_arr).with_duration(duration)
    text_clip = ImageClip(text_arr).with_duration(duration)
    banner_clip = ImageClip(outro_banner_arr).with_duration(duration)

    # Animated subscribe button — fades in from t=1.5s over 0.8s
    FADE_START = 1.5
    FADE_DUR   = 0.8

    def make_subscribe_frame(t):
        if t < FADE_START:
            alpha = 0.0
        elif t < FADE_START + FADE_DUR:
            alpha = (t - FADE_START) / FADE_DUR
        else:
            alpha = 1.0
        return _make_subscribe_btn(alpha)

    subscribe_clip = VideoClip()
    subscribe_clip.frame_function = make_subscribe_frame
    subscribe_clip.duration = duration
    subscribe_clip.size = (W, H)

    outro = CompositeVideoClip(
        [bg_clip, text_clip, subscribe_clip, banner_clip],
        size=(W, H)
    ).with_duration(duration)

    outro = outro.with_effects([vfx.FadeIn(0.5), vfx.FadeOut(0.4)])

    print(f"[OUTRO] Built outro scene — {duration}s with subscribe CTA")
    return outro


def _generate_ambient_tone(duration: float, output_path: str) -> str | None:
    """
    Generate a subtle ambient atmosphere tone using numpy — no external
    audio files needed.

    Technique:
      - Low sub-bass rumble at 45Hz (tension/seriousness feel)
      - Very faint high-frequency air at 8000Hz (room presence)
      - Both mixed at extremely low volume (0.04 = 4%)
      - Output: WAV file at 44100 Hz mono

    The result is a barely-audible background texture that adds depth
    without distracting from the voiceover.
    """
    try:
        import wave
        import struct

        SAMPLE_RATE  = 44100
        VOLUME_BASS  = 0.03   # 3% — sub-bass rumble
        VOLUME_AIR   = 0.015  # 1.5% — high-frequency air presence
        FREQ_BASS    = 45     # Hz — low tension rumble
        FREQ_AIR     = 6000   # Hz — room presence / air

        num_samples = int(SAMPLE_RATE * duration)
        t_arr = np.linspace(0, duration, num_samples, endpoint=False)

        # Bass rumble — pure sine at very low volume
        bass = VOLUME_BASS * np.sin(2 * np.pi * FREQ_BASS * t_arr)

        # Air presence — sine at high frequency, even quieter
        air  = VOLUME_AIR * np.sin(2 * np.pi * FREQ_AIR * t_arr)

        # Add very subtle noise floor (room ambience)
        noise = 0.008 * np.random.normal(0, 1, num_samples)

        # Mix all layers
        mixed = bass + air + noise

        # Soft fade-in and fade-out (0.5s each) to avoid clicks
        fade_samples = int(SAMPLE_RATE * 0.5)
        fade_in  = np.linspace(0, 1, fade_samples)
        fade_out = np.linspace(1, 0, fade_samples)
        mixed[:fade_samples]  *= fade_in
        mixed[-fade_samples:] *= fade_out

        # Clamp to [-1, 1] and convert to 16-bit PCM
        mixed = np.clip(mixed, -1.0, 1.0)
        pcm   = (mixed * 32767).astype(np.int16)

        # Write WAV file
        with wave.open(output_path, 'w') as wf:
            wf.setnchannels(1)       # mono
            wf.setsampwidth(2)       # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())

        print(f"[AMBIENT] Generated tone — {duration:.1f}s → {output_path}")
        return output_path

    except Exception as exc:
        print(f"[AMBIENT] Generation failed: {exc} — skipping ambient audio")
        return None


def build_video(scenes: list[dict]) -> str:
    if not scenes:
        raise ValueError("[VideoBuilder] No scenes provided.")
 
    scene_durations = []
    WORDS_PER_SECOND = 2.0
    for idx, scene in enumerate(scenes):
        audio_path = scene.get("audio_path")
        words = scene["text"].split()
        if audio_path and os.path.isfile(audio_path):
            audio_clip = AudioFileClip(audio_path)
            duration = audio_clip.duration
            audio_clip.close()
        else:
            duration = len(words) * 0.6
        duration = max(4.0, min(duration, 7.5))
        # Removed: first-2-scene cap of 2.8s was cutting audio mid-word
        scene_durations.append(duration)

    total_duration = sum(scene_durations)

    if total_duration < 45:
        scale = 45 / total_duration
        scene_durations = [d * scale for d in scene_durations]
    elif total_duration > 55:
        scale = 55 / total_duration
        scene_durations = [d * scale for d in scene_durations]

    clips = []
    for idx, scene in enumerate(scenes):
        clip = _build_scene_clip(
            image_path=scene.get("image_path"),
            audio_path=scene.get("audio_path"),
            text=scene["text"],
            scene_idx=idx,
            final_duration=scene_durations[idx],
            headline=scene.get("headline", ""),
            news_source=scene.get("news_source", "BBC News"),
            location=scene.get("entities", {}).get("location", ""),
            scene_type=scene.get("type", "general"),
        )
        clips.append(clip)
 
    # ── Append outro scene ────────────────────────────────────────────────
    print("[VideoBuilder] Building outro scene...")
    outro_clip = _build_outro_scene(duration=5.0)
    clips.append(outro_clip)

    print(f"[VideoBuilder] Concatenating {len(clips)} scene(s) + outro…")
    final = concatenate_videoclips(clips, method="compose", padding=0.3)
 
    # ── Ambient audio generation and mixing ───────────────────────────────
    total_duration = final.duration
    ambient_path   = OUTPUT_VIDEO.replace(".mp4", "_ambient.wav")

    ambient_file = _generate_ambient_tone(total_duration, ambient_path)

    if ambient_file and os.path.isfile(ambient_file):
        try:
            ambient_audio = AudioFileClip(ambient_file).with_volume_scaled(0.045)

            if final.audio is not None:
                # Mix ambient under existing voiceover audio
                mixed_audio = CompositeAudioClip([final.audio, ambient_audio])
                final = final.with_audio(mixed_audio)
            else:
                # No voiceover — use ambient only
                final = final.with_audio(ambient_audio)

            print(f"[AMBIENT] Mixed into final video at 4.5% volume")
        except Exception as exc:
            print(f"[AMBIENT] Mix failed: {exc} — rendering without ambient")
    else:
        print("[AMBIENT] Skipping ambient mix — generation failed")

    print(f"[VIDEO] Rendering started...")
    print(f"[VIDEO] Output path: {OUTPUT_VIDEO}")
    print(f"[VideoBuilder] Rendering -> {OUTPUT_VIDEO}")

    final.write_videofile(
        OUTPUT_VIDEO,
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        temp_audiofile=OUTPUT_VIDEO.replace(".mp4", "_tmp_audio.m4a"),
        remove_temp=True,
        logger="bar",
    )
 
    print(f"[VIDEO] Rendering completed")
    print(f"[VideoBuilder] [DONE] -> {OUTPUT_VIDEO}")
    return OUTPUT_VIDEO
 
 
# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_scenes = [
        {"text": "Global leaders gather for emergency climate summit.",
         "keyword": "climate", "image_path": None, "audio_path": None},
        {"text": "Scientists discover new treatment for rare disease.",
         "keyword": "science", "image_path": None, "audio_path": None},
    ]
    build_video(test_scenes)