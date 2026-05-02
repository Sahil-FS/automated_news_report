#!/usr/bin/env python3
"""
main.py — AI News-to-Video Generator
Orchestrates: News -> Script -> Scenes -> Images + Audio -> Video
"""

import sys
import os
import shutil
import uuid
import warnings

print("[ENV] Python:", sys.executable)
print("[ENV] Python Version:", sys.version)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Suppress warnings for cleaner  output
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

from news_fetcher     import fetch_latest_article
from script_generator import summarise
from scene_planner    import plan_scenes
from image_fetcher    import fetch_image, generate_location_map
from voice_generator  import generate_audio
from video_builder    import build_video, _build_map_stinger


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
    news_source = "BBC News"
    for scene in scenes:
        scene["headline"]    = article["title"]
        scene["news_source"] = news_source

    # ── Location map stinger ─────────────────────────────────────────
    primary_location = ""
    if scenes:
        entities = scenes[0].get("entities", {})
        all_locs = entities.get("all_locations", [])

        # Terms that spaCy misclassifies as GPE but are
        # NOT mappable geographic locations
        GEO_BLACKLIST = {
            "secret service", "police", "hospital",
            "white house", "pentagon", "fbi", "cia",
            "nsa", "nato", "un", "eu", "government",
            "parliament", "congress", "senate",
            "court", "department", "ministry",
            "agency", "bureau", "force", "forces",
            "military", "army", "navy", "air force",
            "intelligence", "committee", "commission",
            "administration", "authority", "service",
            "services", "officials", "office",
            "bbc", "news", "media", "press", "journalist",
            "reporters", "correspondent", "editor", "publisher",
            "broadcast", "channel", "network", "station",
        }

        # Filter to genuine geographic locations only
        geo_locs = [
            loc for loc in all_locs
            if loc.lower() not in GEO_BLACKLIST
            and not any(bad in loc.lower()
                        for bad in GEO_BLACKLIST)
        ]

        if geo_locs:
            # Among genuine locations, prefer city-level
            # over country-level — city names are typically
            # longer and more specific
            # e.g. "Golders Green" > "London" > "UK"
            primary_location = max(geo_locs, key=len)
        elif all_locs:
            # All locations were blacklisted — try falling
            # back to country_context derived from ORG/PERSON
            primary_location = entities.get(
                "country_context", "")
        else:
            primary_location = entities.get(
                "country_context", "")

    # Normalize possessives (e.g. "Iran's" -> "Iran")
    primary_location = primary_location.replace("'s", "").strip()

    print(f"[LOCATION] Primary location resolved: "
          f"'{primary_location}'")

    map_image_path = None
    if primary_location:
        from config import IMAGE_DIR
        import os as _os
        map_out = _os.path.join(IMAGE_DIR, "map_stinger.jpg")
        print(f"\n[MAP] Generating location map for '{primary_location}'...")
        map_image_path = generate_location_map(primary_location, map_out)

    # Generate TTS audio for map stinger
    map_audio_path = None
    if map_image_path and primary_location:
        map_narration = (
            f"Breaking news from {primary_location}."
        )
        print(f"[MAP AUDIO] Generating map stinger "
              f"voiceover...")
        map_audio_path = generate_audio(map_narration,
                                        index=997)

    pipeline_meta = {
        "map_image_path":   map_image_path,
        "map_audio_path":   map_audio_path,
        "primary_location": primary_location,
        "headline":         article["title"],
    }

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
        output_path = build_video(scenes, pipeline_meta=pipeline_meta)
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