#!/usr/bin/env python3
"""
main.py -- AI News-to-Video Generator
Orchestrates: News -> Script -> Scenes -> Images + Audio -> Video
"""

import sys
import os
import re

# PHASE 16: Force UTF-8 stdout on Windows to prevent UnicodeEncodeError
# from special characters in log messages.
if sys.platform == "win32":
    try:
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True
        )
    except Exception:
        pass  # Never crash the pipeline over a logging fix

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

def _normalize_numbers_for_tts(text: str) -> str:
    """
    Convert numeric expressions in scene text to spoken-word equivalents
    so Kokoro/Piper reads them correctly.
    """
    if not text:
        return text

    import re as _re

    def _int_to_words(n: int) -> str:
        if n == 0:
            return "zero"

        ones = ["", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve", "thirteen",
                "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
        tens = ["", "", "twenty", "thirty", "forty", "fifty",
                "sixty", "seventy", "eighty", "ninety"]

        def _below_1000(num: int) -> str:
            if num == 0:
                return ""
            if num < 20:
                return ones[num]
            if num < 100:
                return tens[num // 10] + ("-" + ones[num % 10] if num % 10 else "")
            remainder = num % 100
            return ones[num // 100] + " hundred" + (" and " + _below_1000(remainder) if remainder else "")

        if n < 0:
            return "minus " + _int_to_words(-n)
        if n < 1000:
            return _below_1000(n)
        if n < 1_000_000:
            thousands = n // 1000
            remainder = n % 1000
            result = _below_1000(thousands) + " thousand"
            if remainder > 0:
                result += " " + _below_1000(remainder)
            return result
        if n < 1_000_000_000:
            millions = n // 1_000_000
            remainder = n % 1_000_000
            result = _below_1000(millions) + " million"
            if remainder >= 1000:
                result += " " + _int_to_words(remainder)
            elif remainder > 0:
                result += " and " + _below_1000(remainder)
            return result

        billions = n // 1_000_000_000
        remainder = n % 1_000_000_000
        result = _below_1000(billions) + " billion"
        if remainder > 0:
            result += " " + _int_to_words(remainder)
        return result

    def _replace_currency(m):
        symbol = m.group(1)
        amount = m.group(2)
        suffix = m.group(3) or ""
        currency_name = {"$": "dollars", "£": "pounds", "€": "euros"}.get(symbol, "")
        if "." in amount:
            whole, frac = amount.replace(",", "").split(".", 1)
            spoken = _int_to_words(int(whole)) + " point " + " ".join(_int_to_words(int(d)) for d in frac if d.isdigit())
        else:
            spoken = _int_to_words(int(amount.replace(",", "")))
        result = spoken
        if suffix.strip():
            result += " " + suffix.strip()
        if currency_name:
            result += " " + currency_name
        return result

    text = _re.sub(
        r'([$£€])(\d[\d,]*(?:\.\d+)?)(\s*(?:billion|million|trillion|thousand))?',
        _replace_currency,
        text,
        flags=_re.IGNORECASE,
    )

    def _replace_percent(m):
        num_str = m.group(1).replace(",", "")
        if "." in num_str:
            whole, frac = num_str.split(".", 1)
            return _int_to_words(int(whole)) + " point " + " ".join(_int_to_words(int(d)) for d in frac if d.isdigit()) + " percent"
        return _int_to_words(int(num_str)) + " percent"

    text = _re.sub(r'(\d[\d,]*(?:\.\d+)?)\s*%', _replace_percent, text)

    def _replace_large_number(m):
        try:
            n = int(m.group(0).replace(",", ""))
            if n >= 1000:
                return _int_to_words(n)
        except ValueError:
            pass
        return m.group(0)

    text = _re.sub(r'\b\d{1,3}(?:,\d{3})+\b', _replace_large_number, text)

    def _replace_year(m):
        year = int(m.group(0))
        if 1900 <= year <= 2099:
            century = year // 100
            decade = year % 100
            if decade == 0:
                return _int_to_words(century * 100)
            century_word = _int_to_words(century)
            decade_word = _int_to_words(decade) if decade >= 10 else "oh " + _int_to_words(decade)
            return century_word + " " + decade_word
        return m.group(0)

    text = _re.sub(r'\b(19|20)\d{2}\b(?!\s*,\d)', _replace_year, text)

    _ORDINALS = {
        "1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth",
        "5th": "fifth", "6th": "sixth", "7th": "seventh", "8th": "eighth",
        "9th": "ninth", "10th": "tenth",
    }
    for num, word in _ORDINALS.items():
        text = _re.sub(r'\b' + _re.escape(num) + r'\b', word, text, flags=_re.IGNORECASE)

    return text


def _should_show_map(article: dict, primary_location: str, context: str, scenes: list) -> bool:
    """Return True when a map stinger adds clear geographic context."""
    headline = (article.get("title", "") or "").lower()

    if not primary_location:
        print("[MAP DECISION] SKIP - no primary location")
        return False

    _COMPANY_LOCATIONS = {
        "bbc", "cnn", "reuters", "ap", "fedex", "amazon", "google", "microsoft",
        "apple", "meta", "twitter", "facebook", "instagram", "tiktok", "youtube",
        "tesla", "spacex", "nasa", "who", "un", "eu", "nato", "imf",
    }
    if primary_location.lower() in _COMPANY_LOCATIONS:
        print(f"[MAP DECISION] SKIP - location is company/org name: '{primary_location}'")
        return False

    _NO_MAP_CONTEXTS = {"informative", "positive"}
    _NO_MAP_TOPIC_WORDS = {
        "scam", "fraud", "cybercrime", "hack", "comedian", "actor", "celebrity",
        "award", "film", "movie", "song", "music", "album", "sports", "cricket",
        "football", "technology", "ai", "artificial intelligence", "startup",
        "science", "research", "discovery", "space", "nasa", "satellite",
        "stock", "market", "earnings", "quarterly", "ipo", "company",
    }
    _COUNTRIES = [
        "ukraine", "russia", "iran", "israel", "india", "china", "pakistan",
        "north korea", "south korea", "gaza", "taiwan",
    ]

    if context in _NO_MAP_CONTEXTS and not any(country in headline for country in _COUNTRIES):
        print(f"[MAP DECISION] SKIP - context '{context}' with no country-specific event")
        return False

    if any(word in headline for word in _NO_MAP_TOPIC_WORDS):
        _geo_events = {
            "killed", "dead", "attack", "war", "conflict", "strike", "bomb",
            "earthquake", "flood", "disaster", "election", "coup", "arrest",
            "sanctions", "protest", "explosion", "missile", "drone",
        }
        if not any(w in headline for w in _geo_events):
            print("[MAP DECISION] SKIP - feature/profile story with no geo-event")
            return False

    _SHOW_MAP_TRIGGERS = {
        "killed", "dead", "attack", "war", "conflict", "strike", "bomb",
        "airstrike", "missile", "drone", "ceasefire", "invasion", "troops",
        "earthquake", "flood", "disaster", "hurricane", "tsunami",
        "election", "coup", "arrested", "sanctions", "protest", "rally",
        "nuclear", "hostage", "siege", "offensive",
    }
    _has_geo_event = any(w in headline for w in _SHOW_MAP_TRIGGERS)
    _has_real_location = len(primary_location) > 2

    if _has_geo_event and _has_real_location:
        print(f"[MAP DECISION] SHOW - geo-event story in '{primary_location}'")
        return True
    if context in {"tense", "war", "serious", "disaster", "politics"} and _has_real_location:
        print(f"[MAP DECISION] SHOW - context '{context}' with location '{primary_location}'")
        return True

    print("[MAP DECISION] SKIP - no clear geographic event trigger found")
    return False


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
    text = _normalize_numbers_for_tts(text)

    # Fix common capitalization artifacts from splits
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    # Ensure sentence ends with terminal punctuation
    if text and text[-1] not in ".!?":
        text += "."

    # Remove double spaces
    import re as _re
    # PHASE 18: Remove quotation marks from TTS text (sounds unnatural when spoken)
    import re as _tts_re
    text = _tts_re.sub(r'["\u201c\u201d\u2018\u2019]+', '', text)
    text = _tts_re.sub(r'\s{2,}', ' ', text).strip()

    # SAFE possessive correction — explicit allowlist only, no regex over proper nouns
    _POSSESSIVE_FIXES = {
        " Trumps ":   " Trump's ",
        " Bidens ":   " Biden's ",
        " Putins ":   " Putin's ",
        " Irans ":    " Iran's ",
        " Israels ":  " Israel's ",
        " Russias ":  " Russia's ",
        " Chinas ":   " China's ",
        " Indias ":   " India's ",
        "Zelenskyys": "Zelenskyy's",
    }
    for _bad, _good in _POSSESSIVE_FIXES.items():
        text = text.replace(_bad, _good)
    
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
        print("[SYSTEM] Output directory reset -- fresh run enabled")
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
            "bbc.com":             "BBC News",
            "bbcnews":             "BBC News",
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
        # Since labels are no longer used, check length instead
        _is_full_article = len(full_text) > 500  # >500 chars = likely full article
        print(f"[Stage 2] Script input: {len(full_text)} chars "
              f"({'full article' if _is_full_article else 'RSS snippet'})")
        script = summarise(full_text)
        with open(os.path.join(OUTPUT_DIR, "script.txt"), 'w') as f:
            f.write(script)
    
        # Derive story context from the full script text
        # This is used for voice, music, and metadata -- must be accurate
        try:
            from script_generator import detect_context as _detect_ctx, nlp as _sg_nlp

            # PHASE 17: Run context detection on BOTH article text and script.
            # Article text is more reliable for context when script is too short.
            _ctx_text = full_text if len(script.split()) < 40 else script
            _ctx_doc = _sg_nlp(_ctx_text)
            _detected = _detect_ctx(_ctx_doc)
            if _detected and _detected not in ("", None):
                _pipeline_context = _detected
            print(f"[PIPELINE] Story context: '{_pipeline_context}' "
                  f"({'article text' if _ctx_text == full_text else 'script'}) "
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
            
        # PHASE 17: Pipeline health checkpoint
        _script_word_count = len(script.split()) if script else 0
        print(f"[HEALTH] Script: {_script_word_count} words | "
              f"Context: '{_pipeline_context}' | "
              f"Article: {len(full_text)} chars")
        _verify_score = article.get("verification_score", 50)
        print(f"[VERIFY] Article confidence: {_verify_score}/100 "
              f"| Sources: {article.get('cross_sources', [])}")
        if _verify_score < 25:
            print("[VERIFY] CAUTION: Very low cross-source confidence - script may contain unverified claims")
        if _script_word_count < 20:
            print(f"[HEALTH] WARNING: Script is very short ({_script_word_count} words). "
                  f"Video quality will be low. Check Ollama connection.")
        elif _script_word_count < 60:
            print(f"[HEALTH] CAUTION: Script is short ({_script_word_count} words). "
                  f"May produce fewer than 8 scenes.")
        else:
            print(f"[HEALTH] Script length OK ({_script_word_count} words).")
    
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
        
        # PHASE 17: Hard guard -- if scenes is empty, the entire pipeline will crash
        if not scenes:
            print("[PIPELINE] CRITICAL: plan_scenes() returned 0 scenes.")
            print("[PIPELINE] The script may be too short or all sentences were rejected.")
            print("[PIPELINE] Attempting emergency fallback: using raw article title.")
            _emergency_text = article.get("title", "Breaking news from around the world.")
            if not _emergency_text.endswith((".", "!", "?")):
                _emergency_text += "."
            from scene_planner import extract_keywords
            scenes = [{
                "text": _emergency_text,
                "keyword": extract_keywords(_emergency_text) if _emergency_text else "news",
                "type": "general",
                "context": "general",
                "entities": {"location": "", "person": "", "org": "",
                             "all_persons": [], "all_locations": [], "all_orgs": [],
                             "all_events": [], "country_context": ""},
                "headline": article.get("title", ""),
                "news_source": article.get("_source_name", "World News"),
                "article_url": article.get("link", ""),
                "og_image_url": article.get("og_image_url", ""),
            }]
            print(f"[PIPELINE] Emergency fallback: {len(scenes)} scene(s) created from headline.")

        # Metadata and Location resolution
        news_source = "BBC News"
        primary_location = ""

        _CONFLICT_OVERRIDE = {
            "ukraine": "Ukraine", "russia": "Russia",
            "gaza": "Gaza", "iran": "Iran", "israel": "Israel",
            "taiwan": "Taiwan", "myanmar": "Myanmar",
        }

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
        headline_loc = ""

        # 1. Check conflict overrides
        for _conflict_key, _conflict_loc in _CONFLICT_OVERRIDE.items():
            if re.search(r'\b' + re.escape(_conflict_key) + r'\b', _headline_lower):
                headline_loc = _conflict_loc
                print(f"[LOCATION] Conflict override matched: '{headline_loc}'")
                break

        # 2. Check general headline locations
        if not headline_loc:
            for _loc_key in sorted(_HEADLINE_LOCATIONS.keys(), key=len, reverse=True):
                if re.search(r'\b' + re.escape(_loc_key) + r'\b', _headline_lower):
                    headline_loc = _HEADLINE_LOCATIONS[_loc_key]
                    print(f"[LOCATION] Headline location matched: '{headline_loc}'")
                    break

        # 3. Extract scene-based GPEs
        scene_gpe = ""
        scene_country_ctx = ""
        if scenes:
            entities = scenes[0].get("entities", {})
            all_locs = entities.get("all_locations", [])
            geo_locs = [loc for loc in all_locs if loc.lower() not in ["bbc", "news", "reuters", "ap", "fedex", "press"]]
            if geo_locs:
                scene_gpe = max(geo_locs, key=len)
            scene_country_ctx = entities.get("country_context", "")

        # 4. Consolidate and resolve
        if headline_loc:
            primary_location = headline_loc
        elif scene_gpe:
            primary_location = scene_gpe
        else:
            primary_location = scene_country_ctx

        # Clean common NER artifacts from location string
        primary_location = primary_location.replace("'s", "").strip()
        # Remove leading articles that confuse geocoders
        for _article in ("the ", "The ", "a ", "A ", "an ", "An "):
            if primary_location.startswith(_article):
                primary_location = primary_location[len(_article):]
                break

        # Prefer country_context over raw GPE if country_context is set and different
        # but NOT if we matched headline_loc (headline_loc takes absolute priority)
        if not headline_loc and scene_country_ctx and scene_country_ctx != primary_location:
            _hq_countries = {"United Kingdom", "Germany", "United States"}
            if scene_gpe and scene_country_ctx in _hq_countries and scene_gpe not in _hq_countries:
                print(f"[LOCATION] Rejecting HQ country_context '{scene_country_ctx}' in favor of script location '{scene_gpe}'")
                primary_location = scene_gpe
            else:
                print(f"[LOCATION] Using country_context '{scene_country_ctx}' over raw location '{primary_location}'")
                primary_location = scene_country_ctx

        print(f"[LOCATION] Resolved primary location: '{primary_location}'")

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

        # Determine dominant scene type for story-level context (music + TTS voice)
        _scene_type_counts: dict[str, int] = {}
        for _s in scenes:
            _t = _s.get("type", "general")
            _scene_type_counts[_t] = _scene_type_counts.get(_t, 0) + 1
        _dominant_type = max(_scene_type_counts, key=_scene_type_counts.get) if _scene_type_counts else "neutral"
        print(f"[CONTEXT] Dominant story type: '{_dominant_type}' -> voice/music context")

        map_image_path = None
        _show_map = _should_show_map(article, primary_location, _pipeline_context, scenes)
        if primary_location and _show_map:
            map_out = os.path.join(IMAGE_DIR, "map_stinger.jpg")
            map_image_path = generate_location_map(
                primary_location,
                map_out,
                coord_override=_coord_override
            )
        elif not _show_map:
            print("[MAP STINGER] Skipped - story type does not require geographic context")

        map_audio_path = None
        map_narration = ""

        if map_image_path and primary_location:
            # PHASE 21: Dynamic story-specific hook instead of "Breaking news from X."
            def _generate_map_hook(article: dict, primary_location: str, pipeline_context: str) -> str:
                """
                Generate a strong, story-specific hook sentence for the map stinger.
                The sentence must name the location AND deliver the core news fact.

                Priority:
                1. Best-scoring sentence from full article text (number + impact verb)
                2. Truncated headline (≤ 14 words)
                3. Context-matched generic fallback
                """
                import re as _hook_re

                full_text = (
                    article.get("full_article_text", "")
                    or article.get("description", "")
                    or article.get("summary", "")
                    or article.get("title", "")
                )
                headline = article.get("title", "").strip()

                # Strategy 1: Find the most impactful sentence in the article.
                # A high-impact sentence contains a specific number AND an action verb.
                _IMPACT_VERBS = {
                    "killed", "dead", "died", "wounded", "injured", "arrested", "detained",
                    "struck", "attacked", "bombed", "shelled", "collapsed", "destroyed",
                    "protested", "marched", "rallied", "sentenced", "fired", "launched",
                    "announced", "signed", "passed", "blocked", "suspended", "imposed",
                }
                _sentences = [s.strip() for s in _hook_re.split(r'(?<=[.!?])\s+', full_text)
                              if len(s.strip().split()) >= 6]

                best_hook = ""
                best_score = 0

                # PHASE 22: Pre-filter to reject web metadata artifacts from DuckDuckGo snippets.
                # These patterns appear in hook candidates when the article text comes from DDG search.
                _META_PATTERNS = [
                    r'^\d{1,2}[:/]\d{2}',            # timestamp opener "22:56" or "22/04"
                    r'\d+\s*(?:hour|min|day)s?\s+ago', # "2 hours ago"
                    r'(?i)^(?:published|updated|modified)\b',  # "Published at ..."
                    r'(?i)^(?:story highlights|live updates?|breaking news|read more|click here)',
                    r'(?i)^\d+[.)\-]\s',             # numbered list items "1. ", "2) "
                    r'[|]{1}[^.]{5,}$',              # pipe-separated nav text "Title | Site"
                    r'(?i)^(?:by|from|via|source)\s+[A-Z]',  # byline "By Staff Reporter"
                    r'\.{3,}$',                       # trailing ellipsis (truncated snippet)
                ]
                import re as _meta_re

                def _is_metadata_sentence(sent: str) -> bool:
                    s = sent.strip()
                    if len(s.split()) < 6:
                        return True   # too short to be a hook
                    for pat in _META_PATTERNS:
                        if _meta_re.search(pat, s):
                            return True
                    return False

                _HOOK_BAD_ENDINGS = {
                    "a", "an", "the",
                    "to", "of", "in", "on", "at", "for", "by", "with", "from",
                    "into", "onto", "upon", "over", "under", "through", "about",
                    "after", "before", "during", "between", "among", "against",
                    "and", "or", "but", "nor", "so", "yet", "as", "that", "which",
                    "is", "are", "was", "were", "has", "have", "had",
                    "will", "would", "should", "could", "may", "might", "must",
                    "it", "its", "this", "these", "those",
                }

                def _hook_ends_cleanly(sent: str) -> bool:
                    """Return True if hook sentence ends on a complete grammatical word."""
                    if not sent:
                        return False
                    if not sent.rstrip().endswith((".", "!", "?")):
                        return False
                    last_word = sent.rstrip(".!?, ").split()[-1].lower() if sent.split() else ""
                    return last_word not in _HOOK_BAD_ENDINGS

                scored_sentences = []
                for _sent in _sentences[:15]:
                    if _is_metadata_sentence(_sent):
                        print(f"[HOOK FILTER] Skipping metadata sentence: '{_sent[:60]}'")
                        continue
                    _s_lower = _sent.lower()
                    _score = 0
                    if _hook_re.search(r'\b\d+\b', _sent):
                        _score += 3
                    if any(v in _s_lower for v in _IMPACT_VERBS):
                        _score += 3
                    if primary_location and primary_location.lower() in _s_lower:
                        _score += 2
                    _wc = len(_sent.split())
                    if _wc <= 18:
                        _score += 2
                    elif _wc <= 25:
                        _score += 1
                    # Penalty: orphan pronoun opener needs prior context
                    if _sent.split()[0].lower() in ("he", "she", "they", "it", "this", "that"):
                        _score -= 3
                    scored_sentences.append((_score, _sent))

                scored_sentences.sort(key=lambda x: x[0], reverse=True)

                for _score, _sent in scored_sentences:
                    if _score < 3:
                        break
                    best_hook = _sent
                    best_score = _score
                    _words = best_hook.split()
                    if len(_words) > 18:
                        for _ci in range(15, min(len(_words), 20)):
                            if _words[_ci - 1].rstrip().endswith((",", ".", ";")) or \
                               _words[_ci].lower() in ("and", "but", "as", "while", "after"):
                                best_hook = " ".join(_words[:_ci])
                                if not best_hook.rstrip().endswith((".", "!", "?")):
                                    best_hook += "."
                                break
                        else:
                            best_hook = " ".join(_words[:16]) + "."
                    if not best_hook.rstrip().endswith((".", "!", "?")):
                        best_hook += "."
                    if best_hook:
                        best_hook = _hook_re.sub(r'[,;:]+\.?\s*$', '.', best_hook.strip())
                        best_hook = _hook_re.sub(r'\.{2,}$', '.', best_hook)
                        best_hook = _hook_re.sub(r',\s*$', '.', best_hook)
                        if not best_hook.endswith(('.', '!', '?')):
                            best_hook += '.'
                    if _hook_ends_cleanly(best_hook):
                        print(f"[HOOK] Story-specific map hook (score={best_score}): '{best_hook}'")
                        return best_hook

                best_hook = ""
                best_score = 0

                # Strategy 2: Trim headline to 14 words
                if headline:
                    _hl_words = headline.split()[:14]
                    _hl = " ".join(_hl_words)
                    if not _hl.rstrip().endswith((".", "!", "?")):
                        _hl += "."
                    print(f"[HOOK] Headline-based map hook: '{_hl}'")
                    return _hl

                # Strategy 3: Context-matched generic fallback (last resort)
                _CONTEXT_FALLBACKS = {
                    "tense":       f"Breaking developments are unfolding right now in {primary_location}.",
                    "war":         f"A major military incident is developing in {primary_location}.",
                    "politics":    f"A significant political event is happening in {primary_location}.",
                    "serious":     f"An urgent situation is developing in {primary_location}.",
                    "disaster":    f"Emergency response underway in {primary_location} right now.",
                    "business":    f"Major economic news is breaking from {primary_location}.",
                    "informative": f"An important development is emerging from {primary_location}.",
                }
                fallback = _CONTEXT_FALLBACKS.get(
                    pipeline_context,
                    f"Breaking news is developing in {primary_location}."
                )
                print(f"[HOOK] Generic fallback map hook: '{fallback}'")
                return fallback

            map_narration = _generate_map_hook(article, primary_location, _pipeline_context)
            import re as _ph_re
            map_narration = _ph_re.sub(r'[,;:]+\.?\s*$', '.', map_narration.strip())
            map_narration = _ph_re.sub(r'\.{2,}$', '.', map_narration)
            map_narration = _ph_re.sub(r',\s*$', '.', map_narration)
            if not map_narration.endswith(('.', '!', '?')):
                map_narration += '.'
            map_narration = _normalize_numbers_for_tts(map_narration)
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
            "map_hook_text": map_narration,          # PHASE 22: hook text for progressive captions on map stinger
            "primary_location": primary_location,
            "headline": article.get("title", "Breaking News") or "Breaking News",
            "story_context": _pipeline_context,  # script-level context for music/TTS
            "article_url": article.get("link", ""),
            "show_map": _show_map,
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
            # Piper uses subprocess -- keep sequential to avoid race conditions
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
            print("[4/5] Pexels key not set -- skipping alt-image pre-fetch (primary images only).")
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
        # PHASE 17: Use pipeline-level context for metadata title prefix
        generate_youtube_metadata(article, scenes, _pipeline_context, script)
        print("=" * 60)
    except Exception as e:
        print(f"\n[ERROR] Video build failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
