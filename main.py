#!/usr/bin/env python3
"""
main.py — AI News-to-Video Generator
Orchestrates: News -> Script -> Scenes -> Images + Audio -> Video
"""

import sys
import os
import shutil
import uuid

print("[ENV] Python:", sys.executable)
print("[ENV] Python Version:", sys.version)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from news_fetcher     import fetch_latest_article
from script_generator import summarise
from scene_planner    import plan_scenes
from image_fetcher    import fetch_image
from voice_generator  import generate_audio
from video_builder    import build_video


def main():
    print("=" * 60)
    print("  AI News-to-Video Generator")
    print("=" * 60)

    # ── Step 1: Fetch latest news ─────────────────────────────────────────────
    print("\n[1/5] Fetching latest news...")
    article   = fetch_latest_article()
    full_text = f"""
TITLE: {article['title']}

SUMMARY: {article['summary']}

TASK:
Explain this news clearly with:
- context
- background
- why it matters
"""

    # ── Step 2: Summarise into a script ──────────────────────────────────────
    print("\n[2/5] Generating script...")
    script = summarise(full_text)

    print(f"  Script preview: {script[:200]}...")

    # ── Step 3: Plan scenes ───────────────────────────────────────────────────
    print("\n[3/5] Planning scenes...")
    scenes = plan_scenes(script)

    # Store headline metadata in every scene for video_builder overlays
    news_source = "BBC News"  # matches RSS_FEED_URL in config.py
    for scene in scenes:
        scene["headline"] = article["title"]
        scene["news_source"] = news_source

    if not scenes:
        print("[ERROR] No scenes generated. Aborting.")
        sys.exit(1)

    # ── Step 4: Fetch assets (image + audio) per scene ───────────────────────
    print("\n[4/5] Fetching images and generating audio...")
    for idx, scene in enumerate(scenes):

        # Pass the full scene so image_fetcher can use type-aware queries
        scene["image_path"] = fetch_image(
            scene = scene,
            index = idx,
        )

        scene["audio_path"] = generate_audio(scene["text"], idx)

        if not scene["audio_path"]:
            print(f"[ERROR] Audio failed for scene {idx}")
            continue

    # ── Pre-render validation ─────────────────────────────────────────────
    print("\n[VALIDATE] Running pre-render scene checks...")
    valid_scenes = []
    for idx, scene in enumerate(scenes):
        issues = []

        if not scene.get("text", "").strip():
            issues.append("empty text")

        audio = scene.get("audio_path")
        if not audio or not os.path.isfile(audio):
            issues.append("missing audio")

        image = scene.get("image_path")
        if not image or not os.path.isfile(image):
            issues.append("no image — dark fallback will be used")

        if issues:
            print(f"[VALIDATE] Scene {idx:02d} warnings: {', '.join(issues)}")
        else:
            print(f"[VALIDATE] Scene {idx:02d} OK")

        valid_scenes.append(scene)  # keep all scenes — log only, don't drop

    scenes = valid_scenes
    print(f"[VALIDATE] {len(scenes)} scenes ready for render\n")

    # ── Step 5: Build the video ───────────────────────────────────────────────
    print("\n[5/5] Building video...")
    try:
        output_path = build_video(scenes)
        print("\n" + "=" * 60)
        print(f"[SUCCESS] Video created: {output_path}")
        print("=" * 60)
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"[ERROR] Video build failed: {str(e)}")
        print("=" * 60)
        raise


if __name__ == "__main__":
    # ===== UNIQUE RUN ID — confirms every run is a fresh execution =====
    RUN_ID = str(uuid.uuid4())[:8]
    print(f"[RUN ID]: {RUN_ID}")

    # ===== FORCE CLEAN OUTPUT (NO CACHE) =====
    OUTPUT_DIR = "output"

    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)

    os.makedirs(os.path.join(OUTPUT_DIR, "audio"), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "images"), exist_ok=True)

    print("[SYSTEM] Output directory reset — fresh run enabled")

    main()