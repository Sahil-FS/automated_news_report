# image_fetcher.py — Fetch images via Pexels API (primary) + Wikipedia (fallback)

import sys
import os
import json
import hashlib
import urllib.request
import urllib.parse

# PHASE 4: Environment lock
if ".venv" not in sys.executable:
    print("❌ ERROR: Not running inside .venv")
    print(f"Current: {sys.executable}")
    print("Run using: .venv\\Scripts\\python.exe main.py")
    exit(1)

from config import WIKI_API, WIKI_HEADERS, IMAGE_DIR

# ── Pexels config ─────────────────────────────────────────────────────────────
# Get your free key at: https://www.pexels.com/api/
# Paste it here OR set env var:  set PEXELS_API_KEY=your_key_here  (Windows)
#                                export PEXELS_API_KEY=your_key_here (Mac/Linux)
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

if not PEXELS_API_KEY:
    PEXELS_API_KEY = "pTHDTTd3GU14rYzd8KQUMAKZG24EjpxaEwlEzSndOtVvPzAXkr46BIQZ"  # fallback (local use only)

PEXELS_SEARCH  = "https://api.pexels.com/v1/search"

# ── Cache control ─────────────────────────────────────────────────────────────
# Set FORCE_REFRESH=1 to always fetch new images (ignore cache)
FORCE_REFRESH = os.environ.get("FORCE_REFRESH") == "1"

_SEEN_IMAGES = set()
_USED_URLS = set()


# ── Query builder ─────────────────────────────────────────────────────────────
def _build_semantic_query(scene: dict) -> str:
    """
    Build a meaningful search query using full sentence context
    instead of just keyword.
    
    PHASE 4: Improved keyword extraction with stopword removal
    and named entity focus.
    """
    text = scene["text"].lower()

    stopwords = {
        "the","is","a","an","and","or","of","to","in","on","for","with",
        "at","by","from","as","it","this","that","be","are","was","were",
        "has","have","had","do","does","did","will","would","should","could",
        "may","might","must","can","getting","said","saying","says"
    }

    words = []
    for word in text.split():
        clean = word.strip(".,!?-")
        # Keep words > 3 chars and not in stopwords
        if clean not in stopwords and len(clean) > 3:
            words.append(clean)

    # Build query with extracted keywords + news context
    # PHASE 4: Add "news realistic photo" for better relevance
    extracted = " ".join(words[:4])
    
    boost = ""
    text_lower = text.lower()
    if "war" in text_lower:
        boost = " real conflict scene"
    if "tech" in text_lower:
        boost = " modern technology photo"
    if "politics" in text_lower:
        boost = " government meeting"
        
    if extracted:
        return f"{extracted}{boost} news realistic photo"
    else:
        return f"news realistic photo{boost}"


def build_query(scene: dict) -> str | None:
    """
    Keyword-override: maps common space/news topics to tight, high-quality
    Pexels search strings that reliably return relevant images.
    Returns a specific query string, or None to let the ranked list decide.
    
    PHASE 4: All override queries include "news realistic photo" suffix.
    """
    keyword = scene.get("keyword", "")
    scene_type = scene.get("type", "general")
    k = keyword.lower()

    if "astronaut" in k:
        return "astronaut nasa space mission news realistic photo"

    if "earth" in k:
        return "earth from space nasa news realistic photo"

    if "space" in k or "satellite" in k or "orbit" in k:
        return "space satellite nasa news realistic photo"

    # Only match if the keyword is PURELY about updates/news with no other content
    # Do not match keywords that merely contain "news" as a suffix (e.g. appended by scene_planner)
    stripped_k = k.replace("breaking news event realistic", "").replace("news realistic photo", "").strip()
    if stripped_k in ("updates", "news", "latest news", "breaking news", "top news"):
        return "breaking news newsroom studio news realistic photo"

    if scene_type == "politics":
        return f"{keyword} politics government news realistic photo"

    if scene_type == "war":
        return f"{keyword} military conflict news realistic photo"

    if scene_type == "technology":
        return f"{keyword} technology innovation news realistic photo"

    if scene_type == "business":
        return f"{keyword} business market economy news realistic photo"

    if scene_type == "disaster":
        return f"{keyword} disaster emergency news realistic photo"

    # No override — let ranked query list handle it
    return None


def _build_query(scene: dict) -> list[str]:
    """
    Build context-aware search queries based on scene type.
    Returns a ranked list of search queries to try, from most specific to most generic.

    Strategy:
      1. Semantic query from full sentence
      2. Type-specific queries
      3. Fallback keyword-based queries
      
    PHASE 4: All queries include "news realistic photo" suffix for better relevance.
    """
    keyword = scene.get("keyword", "")
    sentence = scene.get("text", "")
    scene_type = scene.get("type", "general")

    # Start with semantic query (already includes "news realistic photo")
    semantic_query = _build_semantic_query(scene)

    queries = [
        semantic_query,
    ]

    # Add type-specific queries
    if scene_type == "politics":
        queries.extend([
            f"{keyword} government politics news realistic photo",
            f"{keyword} political news realistic photo",
            f"{keyword} leader news realistic photo",
            f"{keyword} news realistic photo"
        ])

    elif scene_type == "war":
        queries.extend([
            f"{keyword} military conflict news realistic photo",
            f"{keyword} war news realistic photo",
            f"{keyword} conflict news realistic photo",
            f"{keyword} news realistic photo"
        ])

    elif scene_type == "technology":
        queries.extend([
            f"{keyword} technology innovation news realistic photo",
            f"{keyword} tech news realistic photo",
            f"{keyword} technology news realistic photo",
            f"{keyword} news realistic photo"
        ])

    elif scene_type == "business":
        queries.extend([
            f"{keyword} business market news realistic photo",
            f"{keyword} economy news realistic photo",
            f"{keyword} market news realistic photo",
            f"{keyword} news realistic photo"
        ])

    elif scene_type == "disaster":
        queries.extend([
            f"{keyword} emergency disaster news realistic photo",
            f"{keyword} disaster news realistic photo",
            f"{keyword} emergency news realistic photo",
            f"{keyword} news realistic photo"
        ])

    else:
        # General fallback
        import re
        STOPS = {
            "the","a","an","is","are","was","were","has","have","had",
            "he","she","they","it","his","her","their","its","our","we",
            "and","but","or","so","to","of","in","on","at","by","for",
            "with","from","that","this","these","those","be","been","being",
            "will","would","could","should","may","might","shall","do","did",
            "not","no","nor","yet","both","either","also","just","even",
            "says","say","said","told","tell","over","after","before","about",
        }

        words = re.findall(r"[a-zA-Z]{3,}", sentence)
        meaningful = [w for w in words if w.lower() not in STOPS]

        q1 = " ".join(meaningful[:3]) if meaningful else keyword
        q2 = keyword
        q3 = meaningful[0] if meaningful else "news"

        seen = set()
        for q in [q1, q2, q3]:
            q = q.strip()
            if q and q.lower() not in seen:
                seen.add(q.lower())
                # PHASE 4: Add "news realistic photo" to all fallback queries  
                queries.append(f"{q} news realistic photo")

    # Ensure all queries end with proper suffix
    final_queries = []
    for q in queries:
        if "news realistic photo" not in q.lower():
            q = f"{q} news realistic photo"
        final_queries.append(q)

    return final_queries


# ── Image quality filter ──────────────────────────────────────────────────────
def _should_reject_image(url: str, query: str, scene_type: str) -> bool:
    """
    Reject images that are inappropriate for specific topic types.
    
    For serious topics (politics, war), avoid generic/misleading images.
    Returns True if image should be rejected.
    """
    if scene_type not in ["politics", "war"]:
        return False

    # Stricter filtering for politics/war
    reject_keywords = [
        "money", "cash", "road", "nature", "tree",
        "abstract", "pattern", "background"
    ]

    url_lower = url.lower()
    query_lower = query.lower()

    for word in reject_keywords:
        if word in url_lower:
            return True

    # Keep existing logic for other terms
    reject_terms = ["people", "crowd"]
    for term in reject_terms:
        if term in url_lower or term in query_lower:
            # Check if term is part of relevant keywords (policy vs money)
            # We reject conservatively to avoid broken images
            if term in ["people", "crowd"]:
                return True

    return False


# ── Pexels fetcher ────────────────────────────────────────────────────────────
def _pexels_image_url(query: str, scene_type: str = "general") -> str | None:
    """
    Search Pexels and return the URL of the best portrait photo.
    Prefers portrait orientation for 9:16 vertical video.
    
    Args:
        query: search term
        scene_type: type of scene (for filtering inappropriate results)
    
    Returns None if API key is missing, quota exceeded, no results, or image rejected.
    """
    if PEXELS_API_KEY == "PASTE_YOUR_KEY_HERE" or not PEXELS_API_KEY:
        print("[ImageFetcher] Pexels API key not set — skipping Pexels.")
        return None

    params = urllib.parse.urlencode({
        "query":       query,
        "per_page":    5,
        "orientation": "portrait",   # 9:16 — perfect for vertical video
    })
    url = f"{PEXELS_SEARCH}?{params}"
    import requests
    try:
        r = requests.get(
            url, 
            headers={
                "Authorization": PEXELS_API_KEY,
                "User-Agent":    "Mozilla/5.0 NewsVideoBot/1.0",
                "Accept":        "application/json",
            },
            timeout=10
        )
        data = r.json()
    except Exception as exc:
        print(f"[ImageFetcher] Pexels request failed: {exc}")
        return None

    photos = data.get("photos", [])
    if not photos:
        print(f"[ImageFetcher] Pexels: no results for '{query}'")
        return None

    # Find first acceptable image (skip rejected ones)
    for photo in photos:
        src = photo.get("src", {})
        img_url = src.get("original") or src.get("large2x") or src.get("large")

        if img_url:
            # Apply quality filter for serious topics
            if _should_reject_image(img_url, query, scene_type):
                print(f"[ImageFetcher] Pexels: rejecting '{query}' (inappropriate)")
                continue

            print(f"[ImageFetcher] Pexels found '{query}' -> {img_url[:70]}...")
            return img_url

    print(f"[ImageFetcher] Pexels: all results rejected for '{query}'")
    return None


# ── Wikipedia fallback ────────────────────────────────────────────────────────
def fetch_wikipedia(query):
    import requests

    if not query or len(query.split()) < 1:
        return None

    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query}"

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None

        data = r.json()
        return data.get("thumbnail", {}).get("source")
    except:
        return None


# ── Downloader ────────────────────────────────────────────────────────────────
def _download(img_url: str, dest_path: str) -> bool:
    """Download img_url to dest_path. Returns True on success."""
    if "pexels" in img_url:
        headers = {
            "Authorization": PEXELS_API_KEY,
            "User-Agent":    "Mozilla/5.0 NewsVideoBot/1.0",
        }
    else:
        # Wikipedia CDN requires Referer + User-Agent or it returns empty body
        headers = {
            "User-Agent": "Mozilla/5.0 NewsVideoBot/1.0 (educational project)",
            "Referer":    "https://en.wikipedia.org/",
            "Accept":     "image/jpeg,image/png,image/*",
        }

    req = urllib.request.Request(img_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp, \
             open(dest_path, "wb") as f:
            f.write(resp.read())
    except Exception as exc:
        print(f"[ImageFetcher] Download failed: {exc}")
        # Clean up empty/partial file
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False

    size_kb = os.path.getsize(dest_path) // 1024
    if size_kb < 2:
        # File too small — server returned error body, not image
        print(f"[ImageFetcher] Download returned {size_kb} KB (too small) — discarding.")
        os.remove(dest_path)
        return False

    print(f"[ImageFetcher] Saved {size_kb} KB -> {dest_path}")
    return True


def clean_query(q: str) -> str:
    import re

    q = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", q)  # remove terminal codes
    q = re.sub(r"[^a-zA-Z0-9 ]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()

    return q

def fetch_unsplash(query, index):
    import requests, os

    ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
    if not ACCESS_KEY:
        return None

    url = f"https://api.unsplash.com/photos/random?query={query}&orientation=portrait"

    headers = {"Authorization": f"Client-ID {ACCESS_KEY}"}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None

        data = r.json()
        return data.get("urls", {}).get("regular")
    except:
        return None

import time

def fetch_with_retry(query, index, retries=3):
    for attempt in range(retries):
        try:
            return fetch_pexels(query, index)
        except Exception as e:
            print(f"[RETRY] attempt {attempt+1} failed: {e}")
            time.sleep(1.5 * (attempt + 1))
    return None

def fetch_pexels(query, index):
    return _pexels_image_url(query, "general")

def fetch_fallback(query):
    return f"https://source.unsplash.com/1080x1920/?{query.replace(' ', ',')}"

def build_smart_query(scene, index):
    text = scene.get("text", "").lower()

    if "shot" in text or "gun" in text:
        return "police emergency scene crowd"

    elif "trump" in text or "president" in text:
        return "US president security escort"

    elif "meeting" in text:
        return "government meeting conference"

    elif "security" in text:
        return "security response emergency team"

    else:
        return "breaking news crowd scene"

def clean_keyword(keyword):
    words = keyword.split()
    words = [w for w in words if len(w) > 3]

    if len(words) < 2:
        return "breaking news event"

    return " ".join(words[:4])

# ── Public entry-point ────────────────────────────────────────────────────────
def fetch_image(scene: dict, index: int) -> str | None:
    keyword  = scene.get("keyword", "")
    keyword  = clean_keyword(keyword)
    sentence = scene.get("text", "")
    scene_type = scene.get("type", "general")

    # ── Entity-anchored query builder ─────────────────────────────────────
    # Use NER entities extracted by scene_planner for country/person context.
    entities = scene.get("entities", {})
    person          = entities.get("person", "")
    location        = entities.get("location", "")
    org             = entities.get("org", "")
    country_context = entities.get("country_context", "")
    all_persons     = entities.get("all_persons", [])
    all_orgs        = entities.get("all_orgs", [])

    # Map known organisation types to visually searchable equivalents
    ORG_VISUAL_MAP = {
        "fbi":           "FBI agents investigation",
        "cia":           "CIA intelligence officers",
        "white house":   "White House Washington DC exterior",
        "secret service":"US Secret Service agents escort",
        "nato":          "NATO military alliance meeting",
        "un":            "United Nations assembly",
        "pentagon":      "Pentagon building Washington",
        "nsa":           "NSA cybersecurity surveillance",
        "police":        "police officers scene",
        "congress":      "US Congress Capitol building",
        "senate":        "US Senate chamber",
        "bbc":           "BBC news studio newsroom",
    }

    # ── Scene-aware entity query builder ─────────────────────────────────
    # Scenes 0 and 1 = establishing shots → use global Person + Location anchor.
    # Scenes 2+      = detail shots       → use hybrid scene-level query.
    entity_query = ""

    # Extract scene-level semantic keywords (top 3 meaningful words from text)
    import re as _re
    SEMANTIC_STOPS = {
        "the","a","an","is","are","was","were","be","been","being",
        "have","has","had","do","did","does","will","would","should",
        "could","may","might","shall","must","can","this","that",
        "these","those","and","but","or","so","yet","nor","as","if",
        "of","to","in","on","at","by","for","with","from","into",
        "over","under","than","about","through","before","after",
        "also","just","even","not","no","its","his","her","their",
        "our","your","my","some","any","such","both","each","few",
    }
    scene_words = _re.findall(r"[a-zA-Z]{4,}", sentence.lower())
    scene_keywords = [w for w in scene_words if w not in SEMANTIC_STOPS][:3]
    scene_kw_str   = " ".join(scene_keywords)

    if index <= 1:
        # Establishing scenes — global Person + Location anchor
        if person and location:
            entity_query = f"{person} {location} news photo"
        elif person and country_context:
            entity_query = f"{person} {country_context} news photo"
        elif location:
            entity_query = f"{location} {scene_type} news photo"

    else:
        # Detail scenes — hybrid: country + scene content + type
        if country_context and scene_kw_str:
            # Build type-specific hybrid
            if scene_type == "war":
                entity_query = f"{country_context} {scene_kw_str} shooting security news photo"
            elif scene_type == "politics":
                entity_query = f"{country_context} {scene_kw_str} government official news photo"
            elif scene_type == "business":
                entity_query = f"{country_context} {scene_kw_str} economy news photo"
            elif scene_type == "technology":
                entity_query = f"{country_context} {scene_kw_str} technology news photo"
            elif scene_type == "disaster":
                entity_query = f"{country_context} {scene_kw_str} emergency news photo"
            else:
                entity_query = f"{country_context} {scene_kw_str} news photo"

        elif org:
            # ORG visual map (FBI, Secret Service, etc.)
            org_lower = org.lower()
            mapped = next((v for k, v in ORG_VISUAL_MAP.items()
                           if k in org_lower), "")
            if mapped:
                country_prefix = country_context if country_context else ""
                entity_query = f"{country_prefix} {mapped} news photo".strip()
            else:
                entity_query = f"{org} {scene_kw_str} news photo"

        elif location and scene_kw_str:
            entity_query = f"{location} {scene_kw_str} news photo"

        elif scene_kw_str:
            entity_query = f"{scene_kw_str} news photo"

    # Clean and validate
    if entity_query:
        entity_query = clean_query(entity_query)
        print(f"[ENTITY QUERY] scene {index}: {entity_query}")

    # ── Final query resolution ────────────────────────────────────────────
    # Try entity query first, fall back to existing smart/ranked system
    override = build_query(scene)
    if entity_query:
        query = entity_query          # entity always wins if available
    elif override:
        query = clean_query(override)
    else:
        ranked_queries = _build_query(scene)
        query = clean_query(ranked_queries[0]) if ranked_queries else "breaking news event"

    if not query:
        query = "breaking news event"
        
    cache_key = hashlib.md5(f"{query}|{sentence}".encode()).hexdigest()[:10]
    dest = os.path.join(IMAGE_DIR, f"scene_{index:02d}_{cache_key}.jpg")

    if os.path.exists(dest):
        os.remove(dest)
        print(f"[CACHE] Deleted stale cache to force refresh: {dest}")

    print(f"\n[ImageFetcher] Scene {index:02d} | type='{scene_type}' | keyword='{keyword}'")
    print(f"[SMART QUERY]: {query}")
    print(f"[CONTEXT]: {scene.get('context')}")

    # ── Priority 1: Wikipedia person thumbnail ───────────────────────────
    # For scenes with a named PERSON entity, try Wikipedia portrait first.
    # Wikipedia REST API returns actual photos of public figures.
    image_url = None
    source = "none"

    person = entities.get("person", "")
    if person and index <= 3:
        # Only use Wikipedia person photo for first 3 scenes (establishing)
        wiki_person_url = fetch_wikipedia(person)
        if wiki_person_url:
            image_url = wiki_person_url
            source = "wikipedia_person"
            print(f"[PRIORITY 1] Wikipedia person photo: '{person}'")

    # ── Priority 2: Unsplash ─────────────────────────────────────────────
    if not image_url:
        image_url = fetch_unsplash(query, index)
        source = "unsplash" if image_url else source

    if not image_url:
        # Try ranked queries from the intelligent system before falling back
        ranked_queries = _build_query(scene)
        for ranked_q in ranked_queries[:3]:
            ranked_q = clean_query(ranked_q)
            if ranked_q and ranked_q != query:
                image_url = fetch_with_retry(ranked_q, index)
                if image_url:
                    source = "pexels"
                    break
        if not image_url:
            image_url = fetch_with_retry(query, index)
            source = "pexels" if image_url else source

    if not image_url:
        image_url = fetch_wikipedia(query)
        source = "wiki" if image_url else source

    if not image_url:
        print("[ERROR] No image found — using fallback")
        image_url = fetch_fallback(query)
        source = "fallback"

    if image_url in _USED_URLS:
        print("[ImageFetcher] Duplicate image detected — trying Wikipedia fallback")
        # Try Wikipedia first — more likely to have topic-relevant thumbnail
        wiki_url = fetch_wikipedia(keyword)
        if wiki_url and wiki_url not in _USED_URLS:
            image_url = wiki_url
            print(f"[ImageFetcher] Wikipedia fallback found: {wiki_url[:60]}...")
        else:
            # Try a diversified Pexels query with scene index as variation seed
            varied_queries = _build_query(scene)
            fallback_found = False
            for vq in varied_queries[1:4]:  # skip first (already tried), try next 3
                vq = clean_query(vq)
                alt_url = fetch_with_retry(vq, index)
                if alt_url and alt_url not in _USED_URLS:
                    image_url = alt_url
                    fallback_found = True
                    print(f"[ImageFetcher] Alt query fallback found: {vq}")
                    break
            if not fallback_found:
                # Last resort — generic Unsplash fallback
                image_url = fetch_fallback(query + " " + str(index))
                print(f"[ImageFetcher] Using generic fallback for scene {index}")

    _USED_URLS.add(image_url)

    print(f"[IMAGE SOURCE]: {source}")

    if not image_url:
        return None

    success = download_with_retry(image_url, dest)

    if not success:
        print("[RETRY FAILED] Using fallback image")
        image_url = fetch_fallback(query)
        success = download_with_retry(image_url, dest)

    return dest if success else None

def download_with_retry(url, path, retries=2):
    import requests
    for i in range(retries):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
                return True
        except:
            continue
    return False


# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = fetch_image(
        keyword  = "Iran",
        index    = 0,
        sentence = "Trump issues threat to Iran over Strait of Hormuz.",
    )
    print("\nResult:", result)