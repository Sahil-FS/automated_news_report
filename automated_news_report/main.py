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
import argparse
import json

# Add local path to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

from news_fetcher     import fetch_latest_article
from script_generator import summarise
from scene_planner    import plan_scenes
from image_fetcher    import fetch_image, generate_location_map
from voice_generator  import generate_audio
from video_builder    import build_video
from config import OUTPUT_DIR, AUDIO_DIR, IMAGE_DIR

def save_cache(data, filename):
    with open(os.path.join(OUTPUT_DIR, filename), 'w') as f:
        json.dump(data, f, indent=4)

def load_cache(filename):
    path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return None

def _sanitize_scene_text_for_tts(text: str) -> str:
    """
    Final cleanup of scene text before passing to TTS.
    Ensures the narration sounds grammatically complete.
    """
    if not text:
        return text
    text = text.strip()

    # Fix common capitalization artifacts from splits
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    # Ensure sentence ends with terminal punctuation
    if text and text[-1] not in ".!?":
        text += "."

    # Remove double spaces
    import re as _re
    text = _re.sub(r"\s+", " ", text).strip()

    return text

def generate_youtube_metadata(article: dict, scenes: list, context: str, script: str) -> dict:
    """
    Generate YouTube-ready metadata for the created video.
    Returns a dict with: title, description, hashtags, tags_string.
    Saves to output/youtube_metadata.txt.
    """
    headline = article.get("title", "Breaking News")
    article_url = article.get("link", "")

    TITLE_PREFIXES = {
        "tense":       "🔴 BREAKING:",
        "war":         "⚡ WAR UPDATE:",
        "politics":    "🏛️ POLITICAL ALERT:",
        "serious":     "🚨 URGENT NEWS:",
        "positive":    "✅ MAJOR WIN:",
        "informative": "📡 TECH ALERT:",
        "business":    "💹 MARKET NEWS:",
        "disaster":    "🌪️ DISASTER ALERT:",
        "neutral":     "📰 NEWS UPDATE:",
    }
    prefix = TITLE_PREFIXES.get(context, "📰 NEWS UPDATE:")
    yt_title = f"{prefix} {headline}"
    if len(yt_title) > 100:
        yt_title = yt_title[:97] + "..."

    scene_texts = [s.get("text", "") for s in scenes if s.get("text", "").strip()]
    full_summary = " ".join(scene_texts) or script.strip()

    entities = scenes[0].get("entities", {}) if scenes else {}
    country = entities.get("country_context", "")
    person = entities.get("person", "")
    all_locs = entities.get("all_locations", [])

    CONTEXT_HASHTAGS = {
        "tense":       ["#BreakingNews", "#WorldNews", "#Conflict", "#NewsAlert"],
        "war":         ["#WarNews", "#BreakingNews", "#WorldNews", "#Military"],
        "politics":    ["#Politics", "#WorldNews", "#Government", "#Breaking"],
        "serious":     ["#Breaking", "#EmergencyAlert", "#WorldNews", "#Crisis"],
        "positive":    ["#GoodNews", "#Achievement", "#WorldNews", "#Historic"],
        "informative": ["#TechNews", "#Innovation", "#Science", "#WorldNews"],
        "business":    ["#EconomyNews", "#Markets", "#Business", "#WorldNews"],
        "disaster":    ["#EmergencyAlert", "#DisasterNews", "#Breaking", "#Crisis"],
        "neutral":     ["#News", "#WorldNews", "#Today", "#Breaking"],
    }
    hashtags = list(CONTEXT_HASHTAGS.get(context, ["#News", "#WorldNews"]))

    if country:
        hashtags.append(f"#{country.replace(' ', '')}")
    if person:
        hashtags.append(f"#{person.split()[-1]}")
    for loc in all_locs[:2]:
        clean_loc = loc.replace(" ", "").replace("'", "")
        if len(clean_loc) > 2:
            hashtags.append(f"#{clean_loc}")

    seen_ht = set()
    deduped_hashtags = []
    for h in hashtags:
        hk = h.lower()
        if hk not in seen_ht:
            seen_ht.add(hk)
            deduped_hashtags.append(h)
    hashtags = deduped_hashtags[:15]
    hashtag_string = " ".join(hashtags)

    description = (
        f"{headline}\n\n"
        f"{full_summary}\n\n"
        f"{'─' * 40}\n"
        f"🔔 Subscribe for daily breaking news videos\n"
        f"👍 Like if this was informative\n"
        f"💬 Share your thoughts in the comments\n"
        f"{'─' * 40}\n\n"
    )
    if article_url:
        description += f"📰 Original source: {article_url}\n\n"
    description += f"{hashtag_string}\n\n"
    description += "#AINews #NewsVideo #ShortNews #BreakingNews"

    metadata = {
        "title": yt_title,
        "description": description,
        "hashtags": hashtags,
        "hashtag_string": hashtag_string,
        "context": context,
        "source_url": article_url,
    }

    def _safe_print(message: str) -> None:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(message.encode(encoding, errors="replace").decode(encoding, errors="replace"))

    meta_path = os.path.join(OUTPUT_DIR, "youtube_metadata.txt")
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(f"YOUTUBE TITLE:\n{yt_title}\n\n")
            f.write(f"{'═' * 60}\n\n")
            f.write(f"YOUTUBE DESCRIPTION:\n{description}\n\n")
            f.write(f"{'═' * 60}\n\n")
            f.write(f"HASHTAGS:\n{hashtag_string}\n\n")
            f.write(f"{'═' * 60}\n\n")
            f.write("TAGS FOR YOUTUBE TAG BOX (comma-separated):\n")
            tags = [h.lstrip("#") for h in hashtags]
            tags += ["news", "breaking news", "world news", "short news", "AI news"]
            f.write(", ".join(tags[:30]) + "\n")
    except Exception as e:
        _safe_print(f"[METADATA] Could not save metadata: {e}")
    else:
        _safe_print(f"\n[METADATA] YouTube metadata saved: {meta_path}")
        _safe_print(f"[METADATA] Title: {yt_title}")
        _safe_print(f"[METADATA] Hashtags: {hashtag_string}")

    return metadata

def main():
    parser = argparse.ArgumentParser(description="AI News-to-Video Generator")
    parser.add_argument("--stage", choices=["news", "script", "scenes", "assets", "video"], help="Run only a specific stage")
    parser.add_argument("--skip-to", choices=["script", "scenes", "assets", "video"], help="Skip to a specific stage using cached data")
    parser.add_argument("--test-mode", action="store_true", help="Use mock news data")
    parser.add_argument("--no-clean", action="store_true", help="Don't clean output directory")
    parser.add_argument("--reset-memory", action="store_true",
                        help="Clear topic memory so previously used headlines can be reused")
    parser.add_argument("--category", choices=["general", "world", "politics", "tech", "business", "war", "space", "sports", "entertainment"],
                        default="general", help="RSS news category to fetch from")
    args = parser.parse_args()

    _pipeline_context = "neutral"  # story-level context for voice/music

    # Setup environment
    _memory_path = os.path.join(OUTPUT_DIR, "used_topics.json")
    _topic_memory = None
    if not args.reset_memory and os.path.exists(_memory_path):
        try:
            with open(_memory_path, "r", encoding="utf-8") as _mf:
                _topic_memory = _mf.read()
        except Exception:
            _topic_memory = None

    if not args.no_clean and not args.skip_to:
        if os.path.exists(OUTPUT_DIR):
            shutil.rmtree(OUTPUT_DIR)
        os.makedirs(AUDIO_DIR, exist_ok=True)
        os.makedirs(IMAGE_DIR, exist_ok=True)
        if _topic_memory:
            with open(_memory_path, "w", encoding="utf-8") as _mf:
                _mf.write(_topic_memory)
        print("[SYSTEM] Output directory reset — fresh run enabled")
    else:
        os.makedirs(AUDIO_DIR, exist_ok=True)
        os.makedirs(IMAGE_DIR, exist_ok=True)
        print("[SYSTEM] Using existing output directory")

    if args.reset_memory:
        _mem = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "used_topics.json")
        if os.path.exists(_mem):
            os.remove(_mem)
            print("[SYSTEM] Topic memory cleared - all headlines eligible")

    RUN_ID = str(uuid.uuid4())[:8]
    print(f"[RUN ID]: {RUN_ID}")
    print("=" * 60)
    print("  AI News-to-Video Generator")
    print("=" * 60)
    print(f"[PIPELINE] Initializing run {RUN_ID}")
    print(f"[CONTEXT] Category: {args.category}")

    # ── Stage 1: News ────────────────────────────────────────────────────────
    article = None
    if args.skip_to in ["script", "scenes", "assets", "video"]:
        article = load_cache("article.json")
        if article: print("\n[SKIP] Loading cached article...")
    
    if not article:
        if args.test_mode:
            print("\n[1/5] Using mock news data...")
            mock_path = os.path.join(os.path.dirname(__file__), "tests", "mock_article.json")
            with open(mock_path, "r") as f:
                article = json.load(f)
        else:
            print("\n[1/5] Fetching latest news...")
            article = fetch_latest_article(category=getattr(args, 'category', 'general'))
        save_cache(article, "article.json")

        # Extract clean source name from article RSS feed URL
        _feed_url = article.get("source_feed", "") or article.get("link", "")
        _SOURCE_NAME_MAP = {
            "aljazeera.com":       "Al Jazeera",
            "bbci.co.uk":          "BBC News",
            "bbc.co.uk":           "BBC News",
            "skynews.com":         "Sky News",
            "dw.com":              "Deutsche Welle",
            "timesofindia":        "Times of India",
            "ndtv":                "NDTV News",
            "feedburner.com/ndtv": "NDTV News",
            "reuters.com":         "Reuters",
            "apnews.com":          "AP News",
            "theguardian.com":     "The Guardian",
            "independent.co.uk":   "The Independent",
            "france24.com":        "France 24",
            "aljazeera":           "Al Jazeera",   # catch both domain formats
        }
        _article_source = "World News"
        for _domain, _name in _SOURCE_NAME_MAP.items():
            if _domain in _feed_url.lower():
                _article_source = _name
                break
        article["_source_name"] = _article_source
        print(f"[NewsFetcher] Source identified as: '{_article_source}'")

    if args.stage == "news": return

    # ── Stage 2: Script ──────────────────────────────────────────────────────
    script = None
    if args.skip_to in ["scenes", "assets", "video"]:
        script_path = os.path.join(OUTPUT_DIR, "script.txt")
        if os.path.exists(script_path):
            with open(script_path, 'r') as f:
                script = f.read()
            print("\n[SKIP] Loading cached script...")

    if not script:
        print("\n[2/5] Generating script...")
        # PHASE 14: Use full scraped article when available, else fall back to RSS snippet
        full_text = (
            article.get("full_article_text")
            or article.get("description", "")
            or article.get("summary", "")
            or article.get("title", "")
        )
        if not full_text.strip():
            full_text = article.get("title", "Breaking news")
        print(f"[Stage 2] Script input: {len(full_text)} chars "
              f"({'full article' if 'FULL ARTICLE' in full_text else 'RSS snippet'})")
        script = summarise(full_text)
        with open(os.path.join(OUTPUT_DIR, "script.txt"), 'w') as f:
            f.write(script)
    
        # Derive story context from the full script text
        # This is used for voice, music, and metadata — must be accurate
        try:
            from script_generator import detect_context as _detect_ctx, nlp as _sg_nlp
            _ctx_doc = _sg_nlp(script)
            _detected = _detect_ctx(_ctx_doc)
            # Only accept meaningful contexts — never fall back to generic
            if _detected and _detected not in ("", None):
                _pipeline_context = _detected
            print(f"[PIPELINE] Story context: '{_pipeline_context}' "
                  f"(used for voice/music/metadata)")
        except Exception as _ctx_err:
            # If detection fails, try to infer from headline keywords
            _headline_lower = article.get("title", "").lower()
            if any(w in _headline_lower for w in ["kill", "dead", "attack", "strike",
                                                   "war", "bomb", "shot", "conflict"]):
                _pipeline_context = "tense"
            elif any(w in _headline_lower for w in ["crash", "disaster", "earthquake",
                                                      "flood", "fire", "crisis"]):
                _pipeline_context = "serious"
            print(f"[PIPELINE] Context fallback from headline: '{_pipeline_context}'")
    
    if args.stage == "script": return

    # ── Stage 3: Scenes ──────────────────────────────────────────────────────
    scenes = None
    pipeline_meta = None
    if args.skip_to in ["assets", "video"]:
        scenes = load_cache("scenes.json")
        pipeline_meta = load_cache("pipeline_meta.json")
        if scenes:
            print("\n[SKIP] Loading cached scenes...")
            invalid_count = 0
            for scene in scenes:
                for path_key in ("image_path", "audio_path", "alt_image_path"):
                    p = scene.get(path_key)
                    if p and not os.path.isfile(p):
                        print(f"[SKIP WARN] {path_key} missing, clearing: {p}")
                        scene[path_key] = None
                        invalid_count += 1
            if invalid_count:
                print(f"[SKIP WARN] {invalid_count} cached path(s) were invalid and cleared.")

    if not scenes:
        print("\n[3/5] Planning scenes...")
        scenes = plan_scenes(script)
        
        # Metadata and Location resolution
        news_source = "BBC News"
        primary_location = ""
        if scenes:
            entities = scenes[0].get("entities", {})
            all_locs = entities.get("all_locations", [])
            geo_locs = [loc for loc in all_locs if loc.lower() not in ["bbc", "news"]] 
            if geo_locs:
                primary_location = max(geo_locs, key=len)
            else:
                primary_location = entities.get("country_context", "")

        # Clean common NER artifacts from location string
        primary_location = primary_location.replace("'s", "").strip()
        # Remove leading articles that confuse geocoders
        for _article in ("the ", "The ", "a ", "A ", "an ", "An "):
            if primary_location.startswith(_article):
                primary_location = primary_location[len(_article):]
                break
        # Prefer country_context over raw NER location for geocoding accuracy
        _country_ctx = scenes[0].get("entities", {}).get("country_context", "") if scenes else ""
        if _country_ctx and _country_ctx != primary_location:
            print(f"[LOCATION] Using country_context '{_country_ctx}' over raw location '{primary_location}'")
            primary_location = _country_ctx
        print(f"[LOCATION] Primary location: '{primary_location}'")

        # PHASE 14: Headline fallback if NER failed to find a location
        if not primary_location:
            _HEADLINE_LOCATIONS = {
                # Countries
                "iran": "Iran", "israel": "Israel", "gaza": "Gaza",
                "ukraine": "Ukraine", "russia": "Russia", "china": "China",
                "india": "India", "pakistan": "Pakistan", "lebanon": "Lebanon",
                "syria": "Syria", "iraq": "Iraq", "yemen": "Yemen",
                "afghanistan": "Afghanistan", "turkey": "Turkey",
                "taiwan": "Taiwan", "north korea": "North Korea",
                "united states": "United States", "uk": "United Kingdom",
                "france": "France", "germany": "Germany", "japan": "Japan",
                "brazil": "Brazil", "australia": "Australia", "canada": "Canada",
                # Indian states (common in NDTV/Times of India feeds)
                "uttar pradesh": "Lucknow",
                "maharashtra": "Mumbai",
                "delhi": "New Delhi",
                "rajasthan": "Jaipur",
                "madhya pradesh": "Bhopal",
                "west bengal": "Kolkata",
                "tamil nadu": "Chennai",
                "karnataka": "Bangalore",
                "gujarat": "Ahmedabad",
                "bihar": "Patna",
                "odisha": "Bhubaneswar",
                "assam": "Guwahati",
                "punjab": "Chandigarh",
                "haryana": "Chandigarh",
                "kerala": "Thiruvananthapuram",
                # Major Indian cities
                "prayagraj": "Prayagraj",
                "lucknow": "Lucknow",
                "varanasi": "Varanasi",
                "agra": "Agra",
                "bareilly": "Bareilly",
                "mumbai": "Mumbai",
                "bangalore": "Bangalore",
                "hyderabad": "Hyderabad",
                "chennai": "Chennai",
                "kolkata": "Kolkata",
                "pune": "Pune",
                "ahmedabad": "Ahmedabad",
            }
            _headline_lower = article.get("title", "").lower()
            for _loc_key, _loc_name in _HEADLINE_LOCATIONS.items():
                if _loc_key in _headline_lower:
                    primary_location = _loc_name
                    print(f"[LOCATION] Headline fallback location: '{primary_location}'")
                    break

        # Known country/capital coordinate overrides to prevent Nominatim drift
        COUNTRY_COORDS_OVERRIDE = {
            "united states": (38.8954, -77.0369),    # Washington DC
            "united kingdom": (51.5074, -0.1278),     # London
            "india": (28.6139, 77.2090),               # New Delhi
            "china": (39.9042, 116.4074),              # Beijing
            "russia": (55.7558, 37.6173),              # Moscow
            "ukraine": (50.4501, 30.5234),             # Kyiv
            "france": (48.8566, 2.3522),               # Paris
            "germany": (52.5200, 13.4050),             # Berlin
            "iran": (35.6892, 51.3890),                # Tehran
            "israel": (31.7683, 35.2137),              # Jerusalem
            "pakistan": (33.6844, 73.0479),            # Islamabad
            "saudi arabia": (24.7136, 46.6753),        # Riyadh
            "turkey": (39.9334, 32.8597),              # Ankara
            "japan": (35.6762, 139.6503),              # Tokyo
            "south korea": (37.5665, 126.9780),        # Seoul
            "north korea": (39.0194, 125.7381),        # Pyongyang
            "brazil": (-15.7939, -47.8828),            # Brasilia
            "australia": (-35.2809, 149.1300),         # Canberra
            "canada": (45.4215, -75.6919),             # Ottawa
            "mexico": (19.4326, -99.1332),             # Mexico City
            "egypt": (30.0444, 31.2357),               # Cairo
            "ethiopia": (9.0320, 38.7469),             # Addis Ababa
            "nigeria": (9.0579, 7.4951),               # Abuja
            "south africa": (-25.7479, 28.2293),       # Pretoria
            "afghanistan": (34.5553, 69.2075),         # Kabul
            "myanmar": (19.7633, 96.0785),             # Naypyidaw
            "venezuela": (10.4806, -66.9036),          # Caracas
            # ── India Regions & Cities (Phase 10) ──────────────────────────
            "mumbai": (19.0760, 72.8777),
            "delhi": (28.6139, 77.2090),
            "bengaluru": (12.9716, 77.5946),
            "bangalore": (12.9716, 77.5946),
            "hyderabad": (17.3850, 78.4867),
            "chennai": (13.0827, 80.2707),
            "kolkata": (22.5726, 88.3639),
            "pune": (18.5204, 73.8567),
            "ahmedabad": (23.0225, 72.5714),
            "jaipur": (26.9124, 75.7873),
            "lucknow": (26.8467, 80.9462),
            "kerala": (10.8505, 76.2711),
            "tamil nadu": (11.1271, 78.6569),
            "maharashtra": (19.7515, 75.7139),
            "uttar pradesh": (26.8467, 80.9462),
            "west bengal": (22.9868, 87.8550),
            "karnataka": (15.3173, 75.7139),
            "gujarat": (22.2587, 71.1924),
            "punjab": (31.1471, 75.3412),
            "haryana": (29.0588, 76.0856),
            "rajasthan": (27.3913, 73.4326),
            "telangana": (18.1124, 79.0193),
            "andhra pradesh": (15.9129, 79.7400),
            "odisha": (20.9517, 85.0985),
            "bihar": (25.0961, 85.3131),
            "assam": (26.2006, 92.9376),
            "jammu": (32.7266, 74.8570),
            "kashmir": (34.0837, 74.7973),
            "ladakh": (34.1526, 77.5770),
            # ── Active conflict zones ──────────────────────────────────────
            "gaza": (31.3547, 34.3088),                # Gaza City
            "gaza strip": (31.3547, 34.3088),          # Gaza Strip
            "west bank": (31.9466, 35.3027),           # Ramallah
            "palestine": (31.9035, 35.2034),           # Ramallah / Palestinian territories
            "lebanon": (33.8938, 35.5018),             # Beirut
            "beirut": (33.8938, 35.5018),
            "south lebanon": (33.2704, 35.2037),
            "tripoli, lebanon": (34.4367, 35.8497),
            "syria": (33.5102, 36.2913),               # Damascus
            "iraq": (33.3152, 44.3661),                # Baghdad
            "libya": (32.9020, 13.1806),               # Tripoli
            "sudan": (15.5007, 32.5599),               # Khartoum
            "south sudan": (4.8594, 31.5713),          # Juba
            "somalia": (2.0469, 45.3182),              # Mogadishu
            "yemen": (15.3520, 44.2064),               # Sanaa
            "ethiopia conflict": (9.0320, 38.7469),    # Addis Ababa
            "myanmar conflict": (16.8661, 96.1951),    # Yangon operational area
            "kashmir": (34.0837, 74.7973),             # Srinagar
            "taiwan": (25.0330, 121.5654),             # Taipei
            "crimea": (45.3456, 34.0006),              # Simferopol
            "donbas": (48.0229, 37.8078),              # Donetsk
            "kosovo": (42.6629, 21.1655),              # Pristina
            "haiti": (18.5944, -72.3074),              # Port-au-Prince
            "mali": (12.6392, -8.0029),                # Bamako
            "burkina faso": (12.3647, -1.5333),        # Ouagadougou
            "niger": (13.5137, 2.1098),                # Niamey
            "middle east": (31.0000, 35.0000),         # Region center
            # Indian cities/states for map stinger
            "lucknow":          (26.8467, 80.9462),   # UP capital
            "prayagraj":        (25.4358, 81.8463),   # Prayagraj
            "varanasi":         (25.3176, 82.9739),
            "bareilly":         (28.3670, 79.4304),
            "kanpur":           (26.4499, 80.3319),
            "patna":            (25.5941, 85.1376),
            "bhopal":           (23.2599, 77.4126),
            "jaipur":           (26.9124, 75.7873),
            "guwahati":         (26.1445, 91.7362),
            "bhubaneswar":      (20.2961, 85.8245),
            "chandigarh":       (30.7333, 76.7794),
            "thiruvananthapuram": (8.5241, 76.9366),
        }

        _loc_key = primary_location.lower().strip()
        _coord_override = COUNTRY_COORDS_OVERRIDE.get(_loc_key)
        if _coord_override:
            print(f"[LOCATION] Using known coordinates for '{primary_location}': {_coord_override}")

        map_image_path = None
        if primary_location:
            map_out = os.path.join(IMAGE_DIR, "map_stinger.jpg")
            map_image_path = generate_location_map(
                primary_location,
                map_out,
                coord_override=_coord_override
            )

        map_audio_path = None

        # Determine dominant scene type for story-level context (music + TTS voice)
        _scene_type_counts: dict[str, int] = {}
        for _s in scenes:
            _t = _s.get("type", "general")
            _scene_type_counts[_t] = _scene_type_counts.get(_t, 0) + 1
        _dominant_type = max(_scene_type_counts, key=_scene_type_counts.get) if _scene_type_counts else "neutral"
        print(f"[CONTEXT] Dominant story type: '{_dominant_type}' -> voice/music context")

        if map_image_path and primary_location:
            map_narration = f"Breaking news from {primary_location}."
            map_audio_path = generate_audio(
            map_narration,
            index=997,
            context=_pipeline_context
        )

        for scene in scenes:
            scene["headline"]     = article["title"]
            scene["news_source"]  = article.get("_source_name", "World News")
            scene["article_url"]  = article.get("link", "")
            scene["og_image_url"] = article.get("og_image_url", "")

        pipeline_meta = {
            "map_image_path": map_image_path,
            "map_audio_path": map_audio_path,
            "primary_location": primary_location,
            "headline": article.get("title", "Breaking News") or "Breaking News",
            "story_context": _pipeline_context,  # script-level context for music/TTS
            "article_url": article.get("link", ""),
        }
        save_cache(scenes, "scenes.json")
        save_cache(pipeline_meta, "pipeline_meta.json")

    if args.stage == "scenes": return

    # ── Stage 4: Assets ──────────────────────────────────────────────────────
    if args.stage == "assets" or not args.skip_to == "video":
        print("\n[4/5] Fetching images and generating audio...")
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_image_task(args):
            scene, idx = args
            scene["image_path"] = fetch_image(scene=scene, index=idx)
            return scene

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(
                _fetch_image_task,
                [(scene, idx) for idx, scene in enumerate(scenes)]
            ))
        scenes = results

        # Parallelize audio when Kokoro is available; fall back to sequential for Piper
        from voice_generator import KOKORO_AVAILABLE as _kokoro_ok
        if _kokoro_ok:
            def _audio_task(args):
                scene, idx = args
                _tts_text = _sanitize_scene_text_for_tts(scene["text"])
                if _tts_text.strip() != scene["text"].strip():
                    print(f"[TTS SANITIZE] Scene {idx}: '{scene['text'][:50].strip()}' -> '{_tts_text[:50].strip()}'")
                # Use dominant story context for consistent voice across all scenes
                _ctx = pipeline_meta.get("story_context", "neutral") if pipeline_meta else "neutral"
                scene["audio_path"] = generate_audio(_tts_text, idx, context=_ctx)
                return scene

            with ThreadPoolExecutor(max_workers=3) as audio_executor:
                audio_results = list(audio_executor.map(
                    _audio_task,
                    [(scene, idx) for idx, scene in enumerate(scenes)]
                ))
            scenes = audio_results
        else:
            # Piper uses subprocess — keep sequential to avoid race conditions
            for idx, scene in enumerate(scenes):
                _tts_text = _sanitize_scene_text_for_tts(scene["text"])
                if _tts_text.strip() != scene["text"].strip():
                    print(f"[TTS SANITIZE] Scene {idx}: '{scene['text'][:50].strip()}' -> '{_tts_text[:50].strip()}'")
                _ctx = pipeline_meta.get("story_context", "neutral") if pipeline_meta else "neutral"
                scene["audio_path"] = generate_audio(_tts_text, idx, context=_ctx)
        
        # Pre-fetch visual cut alt-images for scenes longer than 2.8s
        print("[4/5] Pre-fetching visual cut alt-images...")
        from image_fetcher import _build_query, fetch_with_retry, clean_query, IMAGE_DIR as _IMG_DIR, PEXELS_API_KEY as _PEXELS_KEY
        if not _PEXELS_KEY:
            print("[4/5] Pexels key not set — skipping alt-image pre-fetch (primary images only).")
        else:
            import hashlib as _hs
            for idx, scene in enumerate(scenes):
                audio_path = scene.get("audio_path")
                scene["alt_image_path"] = None
                if audio_path and os.path.isfile(audio_path):
                    from moviepy.audio.io.AudioFileClip import AudioFileClip as _AFC
                    _ac = _AFC(audio_path)
                    _scene_dur = _ac.duration
                    _ac.close()
                    if _scene_dur > 2.8 and scene.get("image_path"):
                        try:
                            _alt_queries = _build_query(scene)
                            for _aq in _alt_queries[1:4]:
                                _aq = clean_query(_aq)
                                _alt_url = fetch_with_retry(_aq, idx + 1000)
                                if _alt_url:
                                    _alt_dest = os.path.join(
                                        _IMG_DIR,
                                        f"scene_{idx:02d}_b_{_hs.md5(_aq.encode()).hexdigest()[:8]}.jpg"
                                    )
                                    import requests as _req
                                    _r = _req.get(_alt_url, timeout=10)
                                    if _r.status_code == 200:
                                        with open(_alt_dest, "wb") as _f:
                                            _f.write(_r.content)
                                        if os.path.getsize(_alt_dest) > 2048:
                                            scene["alt_image_path"] = _alt_dest
                                            break
                        except Exception as _alt_exc:
                            print(f"[ALT IMAGE] Scene {idx} failed: {_alt_exc}")

        save_cache(scenes, "scenes.json") # Update with paths

    if args.stage == "assets": return

    # ── Stage 5: Video ───────────────────────────────────────────────────────
    print("\n[5/5] Building video...")
    try:
        output_path = build_video(scenes, pipeline_meta=pipeline_meta)
        print("\n" + "=" * 60)
        print(f"[SUCCESS] Video created: {output_path}")
        _context_for_meta = scenes[0].get("type", "neutral") if scenes else "neutral"
        generate_youtube_metadata(article, scenes, _context_for_meta, script)
        print("=" * 60)
    except Exception as e:
        print(f"\n[ERROR] Video build failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
