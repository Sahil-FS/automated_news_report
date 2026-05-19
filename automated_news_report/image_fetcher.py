# image_fetcher.py -- Fetch images via Pexels API (primary) + Wikipedia (fallback)

import sys
import os
import json
import hashlib
import urllib.request
import urllib.parse
import time
import io
import math
import requests
from PIL import Image, ImageDraw
from ddgs import DDGS

_POLLINATIONS_FAILED_THIS_RUN = False
_POLLINATIONS_FAIL_COUNT = 0

def generate_location_map(location: str, output_path: str, coord_override: tuple = None) -> str | None:
    """
    Generate a 1080x1920 dark-mode map card with a red pin.

    Dependencies used (all already available in the project):
      - geopy.geocoders.Nominatim   (free, no API key)
      - CartoDB Dark-Matter tiles   (free, no API key, fetched via requests)
      - PIL / Pillow                (already used in video_builder)
      - requests                    (already used in image_fetcher)

    Does NOT require staticmap. Returns saved path or None on failure.
    """
    import math
    import io
    import requests as _req
    from PIL import Image as _Image, ImageDraw as _ImageDraw
    from PIL import ImageFilter as _ImageFilter

    # -- 1. Geocode the location ---------------------------------------
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderTimedOut
    except ImportError:
        print("[MAP] geopy not installed - run: pip install geopy")
        return None

    try:
        geolocator = Nominatim(user_agent="NewsVideoBot/2.0")
        if coord_override:
            lat, lon = coord_override
            print(f"[MAP] Using override coordinates for '{location}': {lat:.4f}, {lon:.4f}")
        else:
            try:
                geo_result = geolocator.geocode(location, timeout=8)
            except GeocoderTimedOut:
                print(f"[MAP] Geocoding timed out for '{location}'")
                return None

            if not geo_result:
                print(f"[MAP] Location not found: '{location}'")
                return None

            lat = geo_result.latitude
            lon = geo_result.longitude
            print(f"[MAP] Geocoded '{location}' at {lat:.4f}, {lon:.4f}")

        # -- 2. Tile math helpers ------------------------------------------
        # Choose zoom level based on whether this is a country or city name
        # Country names (from COUNTRY_COORDS_OVERRIDE) get lower zoom to show country shape
        _is_country = coord_override is not None  # coord_override means it's a country
        
        # Smarter zoom for small regions / conflict zones (Phase 9)
        SMALL_REGIONS = {
            "gaza", "gaza strip", "west bank", "israel", "taiwan", "ukraine", 
            "hong kong", "singapore", "gibraltar", "monaco", "vatican"
        }
        if location.lower().strip() in SMALL_REGIONS:
            ZOOM = 11
            print(f"[MAP] Small region/conflict zone detected: using higher zoom ({ZOOM})")
        else:
            ZOOM = 5 if _is_country else 10
        TILE_SIZE = 256
        GRID      = 3      # 3x3 grid of tiles
        HALF      = 1      # tiles each side of centre

        def _deg2tile(lat_d, lon_d, z):
            lat_r = math.radians(lat_d)
            n     = 2 ** z
            tx    = int((lon_d + 180.0) / 360.0 * n)
            ty    = int((1.0 - math.log(
                        math.tan(lat_r) + 1.0 / math.cos(lat_r)
                    ) / math.pi) / 2.0 * n)
            return tx, ty

        def _tile2nw(tx, ty, z):
            """Return NW-corner lat/lon of tile (tx, ty, z)."""
            n   = 2 ** z
            lon = tx / n * 360.0 - 180.0
            lat = math.degrees(
                    math.atan(math.sinh(math.pi * (1 - 2 * ty / n)))
                  )
            return lat, lon

        cx_tile, cy_tile = _deg2tile(lat, lon, ZOOM)

        # -- 3. Fetch 3x3 tile grid ----------------------------------------
        TILE_URL  = (
            "https://cartodb-basemaps-a.global.ssl.fastly.net"
            "/dark_all/{z}/{x}/{y}.png"
        )
        HDR       = {"User-Agent": "NewsVideoBot/2.0 (educational)"}
        canvas_px = TILE_SIZE * GRID   # 768 px
        canvas    = _Image.new("RGB", (canvas_px, canvas_px), (20, 20, 30))

        for dy in range(-HALF, HALF + 1):
            for dx in range(-HALF, HALF + 1):
                url = (TILE_URL
                       .replace("{z}", str(ZOOM))
                       .replace("{x}", str(cx_tile + dx))
                       .replace("{y}", str(cy_tile + dy)))
                tile_loaded = False
                for _tile_attempt in range(3):
                    try:
                        r = _req.get(url, headers=HDR, timeout=8)
                        if r.status_code == 200:
                            tile = _Image.open(io.BytesIO(r.content)).convert("RGB")
                            canvas.paste(tile,
                                         ((dx + HALF) * TILE_SIZE,
                                          (dy + HALF) * TILE_SIZE))
                            tile_loaded = True
                            break
                    except Exception as exc:
                        if _tile_attempt == 2:
                            print(f"[MAP] Tile ({dx},{dy}) failed after 3 attempts: {exc}")
                        import time as _tile_time
                        _tile_time.sleep(0.5 * (_tile_attempt + 1))

        # -- 4. Calculate pixel position of the pin ------------------------
        nw_lat, nw_lon = _tile2nw(cx_tile - HALF,        cy_tile - HALF,        ZOOM)
        se_lat, se_lon = _tile2nw(cx_tile - HALF + GRID, cy_tile - HALF + GRID, ZOOM)

        lon_range = (se_lon - nw_lon) or 0.001
        lat_range = (nw_lat - se_lat) or 0.001

        pin_x = int((lon - nw_lon) / lon_range * canvas_px)
        pin_y = int((nw_lat - lat) / lat_range * canvas_px)
        pin_x = max(24, min(canvas_px - 24, pin_x))
        pin_y = max(24, min(canvas_px - 24, pin_y))

        # -- 5. Draw red pin with shadow and white dot ---------------------
        draw = _ImageDraw.Draw(canvas)
        RO, RI, RD = 22, 18, 6   # outer, inner, dot radii

        # Drop shadow
        draw.ellipse(
            [(pin_x - RO + 3, pin_y - RO + 3),
             (pin_x + RO + 3, pin_y + RO + 3)],
            fill=(0, 0, 0)
        )
        # Red circle
        draw.ellipse(
            [(pin_x - RO, pin_y - RO),
             (pin_x + RO, pin_y + RO)],
            fill=(220, 30, 30)
        )
        # White inner ring
        draw.ellipse(
            [(pin_x - RI, pin_y - RI),
             (pin_x + RI, pin_y + RI)],
            fill=(200, 20, 20)
        )
        # White centre dot
        draw.ellipse(
            [(pin_x - RD, pin_y - RD),
             (pin_x + RD, pin_y + RD)],
            fill=(255, 255, 255)
        )

        # -- 6. Scale to 1080 wide, pad to 1080x1920, add vignette --------
        target_w = 1080
        scale_f  = target_w / canvas_px
        new_h    = int(canvas_px * scale_f)   # ~1080 px
        canvas   = canvas.resize((target_w, new_h), _Image.LANCZOS)

        full  = _Image.new("RGB", (1080, 1920), (12, 14, 22))
        y_off = (1920 - new_h) // 2
        full.paste(canvas, (0, y_off))

        # Vignette overlay (gradual dark border)
        vig  = _Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        vd   = _ImageDraw.Draw(vig)
        for i in range(180):
            alpha = int(160 * (i / 180) ** 2)
            vd.rectangle(
                [(i, i), (1080 - i, 1920 - i)],
                outline=(0, 0, 0, alpha)
            )
        full = _Image.alpha_composite(
            full.convert("RGBA"), vig
        ).convert("RGB")

        full.save(output_path, "JPEG", quality=90)
        size_kb = os.path.getsize(output_path) // 1024
        print(f"[MAP] Saved {size_kb} KB -> {output_path}")
        return output_path

    except Exception as exc:
        print(f"[MAP] Error: {exc}")
        return None


# PHASE 4: Environment lock
import os as _os_env_check
if "VIRTUAL_ENV" not in _os_env_check.environ and ".venv" not in sys.executable and "venv" not in sys.executable:
    print(f"[WARN] Running outside a virtual environment: {sys.executable}")

from config import WIKI_API, WIKI_HEADERS, IMAGE_DIR

# ── Pexels config ─────────────────────────────────────────────────────────────
# Get your free key at: https://www.pexels.com/api/
# Paste it here OR set env var:  set PEXELS_API_KEY=your_key_here  (Windows)
#                                export PEXELS_API_KEY=your_key_here (Mac/Linux)
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

if not PEXELS_API_KEY:
    print(
        "[ImageFetcher] WARNING: PEXELS_API_KEY environment variable is not set.\n"
        "  Pexels image search will be skipped. Set it with:\n"
        "  Windows PowerShell: $env:PEXELS_API_KEY='your_key_here'\n"
        "  Linux/macOS:        export PEXELS_API_KEY='your_key_here'\n"
        "  Get a free key at:  https://www.pexels.com/api/"
    )

PEXELS_SEARCH  = "https://api.pexels.com/v1/search"

# ── Cache control ─────────────────────────────────────────────────────────────
# Set FORCE_REFRESH=1 to always fetch new images (ignore cache)
FORCE_REFRESH = os.environ.get("FORCE_REFRESH") == "1"

_SEEN_IMAGES = set()
_USED_URLS = set()
_USED_IMAGE_PATHS: set[str] = set()   # tracks saved file paths per run
_USED_PEXELS_IDS: set[str] = set()    # tracks Pexels photo IDs per run

import threading
_FETCH_LOCK = threading.Lock()
_PEXELS_ID_LOCK = threading.Lock()

# ── Copyright-protected CDN domain blocklist ──────────────────────────
# Images hosted on these domains are agency/news photos under strict
# copyright. Downloading them for video publication triggers DMCA.
# The pipeline skips any URL matching these patterns.
_BLOCKED_CDN_PATTERNS = [
    "gettyimages.",
    "gettyimages-",
    "istockphoto.",
    "shutterstock.",
    "alamy.com",
    "depositphotos.",
    "123rf.com",
    "dreamstime.",
    # News agency protected CDNs
    "media.gettyimages",
    "nytimes.com/images",
    "washingtonpost.com",
    "economist.com",
    "ft.com",
    "wsj.com",
    "thetimes.co.uk",
    "telegraph.co.uk/content",
    # Wire service raw feeds
    "apimages.com",
    "reuterspictures.",
    "afp.com",
    # Stock aggregators
    "corbisimages.",
    "superstock.",
    "agefotostock.",
]


def _is_blocked_url(url: str) -> bool:
    """Return True if url matches a known protected CDN domain."""
    url_lower = (url or "").lower()
    return any(pattern in url_lower for pattern in _BLOCKED_CDN_PATTERNS)


# ── Adult content and off-topic domain blocklist ──────────────────────────────
# These domains are blocked entirely regardless of what URL path they serve.
# Checked before any other filter. Never download from these domains.
_BLOCKED_DOMAINS = [
    # Adult / manga / hentai hosting
    "momon-ga.com",
    "donmai.us",        # danbooru - adult art
    "gelbooru.com",
    "rule34.xxx",
    "nhentai.net",
    "hentaifox.com",
    "e-hentai.org",
    "fakku.net",
    "pixiv.net",        # often adult content
    "embed.pixiv.net",
    "rule34.paheal.net",
    "tbib.org",
    "xbooru.com",
    "chan.sankakucomplex.com",
    "anime-pictures.net",
    "zerochan.net",
    "konachan.com",
    "yande.re",
    "safebooru.org",    # blocked as precaution even if "safe"
    "danbooru.donmai.us",
    # Japanese shopping / manga sites
    "yimg.jp",
    "item-shopping.c.yimg.jp",
    "kyomo-store",
    "momon-ga",
    # E-commerce / shopping CDNs
    "akinoncloudcdn.com",
    "akinon.com",
    "shopify.com",
    "cdn.shopify.com",
    "static.wixstatic.com",
    "squarespace-cdn.com",
    # Gaming / entertainment (not news)
    "pockettactics.com",
    "gamespot.com",
    "ign.com",
    "kotaku.com",
    "polygon.com",
    # Travel / lifestyle (not news)
    "travelandleisureasia.com",
    "tripadvisor.com",
    "booking.com",
    # Stock art / illustration (supplements existing path filter)
    "freepik.com",
    "flaticon.com",
    "vecteezy.com",
    "vectorstock.com",
    "clipartbest.com",
    # Paywalled news (already in _BLOCKED_CDN_PATTERNS but add here too)
    "alamy.com",
    "gettyimages.com",
    "shutterstock.com",
    "istockphoto.com",
    # General adult content
    "pornhub.com",
    "xvideos.com",
    "xnxx.com",
    "redtube.com",
    "onlyfans.com",
]

def _is_blocked_domain(url: str) -> bool:
    """
    Return True if the URL's domain matches any entry in _BLOCKED_DOMAINS.
    Checks both exact domain match and subdomain match.
    This is the FIRST filter applied -- before any path-level checks.
    """
    if not url:
        return False
    url_lower = url.lower()
    # Strip protocol
    if "://" in url_lower:
        url_lower = url_lower.split("://", 1)[1]
    # Get domain part (before first /)
    domain_part = url_lower.split("/")[0]
    for blocked in _BLOCKED_DOMAINS:
        if blocked in domain_part:
            return True
    return False

def _extract_pexels_id(url: str) -> str:
    """
    Extract the numeric photo ID from a Pexels URL.
    Example: 'https://images.pexels.com/photos/11390951/pexels-photo-11390951.jpeg'
    Returns '11390951' or '' if not a Pexels URL.
    """
    if "pexels.com/photos/" not in url:
        return ""
    try:
        # Pattern: /photos/NUMERIC_ID/
        parts = url.split("/photos/")
        if len(parts) > 1:
            return parts[1].split("/")[0]
    except Exception:
        pass
    return ""

# ── Clipart and toy image rejection patterns ──────────────────────────
# URLs containing these strings are almost always stock illustrations,
# clipart, toy photos, or abstract graphics -- never real news photos.
_CLIPART_URL_PATTERNS = [
    # Art styles that are not photojournalism
    "clipart", "clip-art", "illustration", "vector", "cartoon",
    "drawing", "graphic", "icon", "symbol", "diagram",
    "anime", "manga", "hentai", "doujin", "ecchi",
    "webp",          # .webp files from art sites -- often anime
    # Toy / product photos
    "toys", "toy-", "/toy/", "plastic", "figurine", "miniature",
    # Stock art platforms
    "istockphoto", "clipartbest", "freepik", "flaticon",
    "noun-project", "vecteezy", "vectorstock",
    "shutterstock.com/image-vector",
    "canstockphoto", "dreamstime.com/illustration",
    "123rf.com/photo_.*vector",
    # Shopping / e-commerce path patterns
    "/products/", "/shop/", "/store/", "/item/",
    "shopping", "buy-now", "add-to-cart", "checkout",
    # Food / lifestyle (not news)
    "/food/", "/recipe/", "/cake/", "/menu/",
    # Gaming
    "/game/", "/games/", "/gaming/",
    # Fashion / clothing
    "/fashion/", "/clothing/", "/apparel/", "/outfit/",
    # Travel
    "/travel/", "/hotel/", "/resort/", "/destination/",
]

def _is_clipart_url(url: str) -> bool:
    """
    Return True if the URL is from a blocked domain OR contains a
    path keyword indicating non-news content (clipart, shopping, anime, etc.)
    Domain check runs first for performance -- most rejections happen there.
    """
    if not url:
        return True  # reject empty URLs
    # Domain-level check (fastest, catches adult sites)
    if _is_blocked_domain(url):
        print(f"[BLOCKED DOMAIN] {url[:70]}")
        return True
    # Path-level keyword check
    url_lower = url.lower()
    return any(pat in url_lower for pat in _CLIPART_URL_PATTERNS)


# ── Query builder ─────────────────────────────────────────────────────────────
def _extract_og_url(article_url: str) -> str | None:
    try:
        if not article_url:
            print("[OG] No article URL provided")
            return None

        import requests
        from bs4 import BeautifulSoup  # pip install beautifulsoup4

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.get(article_url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"[OG] Page fetch failed: HTTP {r.status_code}")
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("meta", property="og:image")
        if not tag:
            tag = soup.find("meta", attrs={"name": "twitter:image"})

        image_url = tag.get("content", "").strip() if tag else ""
        if not image_url:
            print("[OG] No og:image or twitter:image found")
            return None

        if image_url.startswith("//"):
            image_url = f"https:{image_url}"

        # PHASE 10: Suppress redundant print; caller handles logging
        return image_url
    except Exception:
        return None


def _build_semantic_query(scene: dict) -> str:
    """
    Build a meaningful search query anchored to the story's geographic and
    thematic context. The country context and scene type are MANDATORY prefixes
    to prevent off-topic results. Scene-level keywords are secondary.

    PHASE 5: Country-anchored queries prevent abstract keyword drift.
    """
    text = scene.get("text", "").lower()
    scene_type = scene.get("type", "general")
    entities = scene.get("entities", {})
    country = entities.get("country_context", "")
    person = entities.get("person", "")

    stopwords = {
        "the","is","a","an","and","or","of","to","in","on","for","with",
        "at","by","from","as","it","this","that","be","are","was","were",
        "has","have","had","do","does","did","will","would","should","could",
        "may","might","must","can","getting","said","saying","says",
        # Abstract words that produce off-topic images
        "instance","however","according","although","despite","despite",
        "meanwhile","furthermore","additionally","consequently","therefore",
        "who","which","that","whose","both","each","while","amid",
        "significant","revealed","stressed","condemned","insisted","claimed",
    }

    # Only extract CONCRETE NOUNS and PROPER NOUNS -- skip verbs and abstractions
    # A concrete noun describes something that can be photographed
    ABSTRACT_VERBS_AND_ADJ = {
        "according", "however", "despite", "although", "therefore", "furthermore",
        "meanwhile", "consequently", "additionally", "subsequently", "regarding",
        "concerning", "following", "including", "excluding", "regarding",
        "aimed", "designed", "intended", "expected", "stressed", "claimed",
        "stated", "confirmed", "reported", "announced", "revealed", "urged",
        "called", "asked", "told", "said", "noted", "added", "warned",
        "limited", "reduced", "increased", "decreased", "improved", "changed",
        "continued", "remains", "began", "started", "ended", "happened",
        "occurred", "resulted", "caused", "leading", "following", "based",
        "related", "connected", "associated", "linked", "involved",
        "guidelines", "measures", "arrangements", "developments", "situation",
        "circumstances", "conditions", "factors", "aspects", "elements",
        "significant", "important", "major", "critical", "serious", "urgent",
        "immediate", "ongoing", "current", "recent", "latest", "previous",
        "broader", "further", "additional", "overall", "general", "specific",
        "according", "toward", "through", "within", "without", "between",
        "preserve", "minimize", "maximize", "ensure", "maintain", "adopt",
        "remote", "valuable", "foreign", "economic", "political", "social",
    }

    # PHASE 18: Remove words that cause off-topic image results
    # "body" = human anatomy in image search context, not government body
    # "force" = military/police force, or physical force
    # "clear" = glass/transparency, not political clarity
    _IMAGE_AMBIGUOUS_WORDS = {
        "body", "bodies", "force", "forces", "clear", "clearing",
        "ground", "grounds", "base", "bases", "capital", "head",
        "figure", "figures", "front", "back", "state", "states",
        "lead", "leads", "case", "cases", "point", "points",
        "run", "runs", "running", "stand", "stands", "holding",
        "strike", "strikes", "striking", "target", "targets",
        "show", "shows", "showing", "dozens", "hundreds", "thousands",
        "videos", "video", "footage", "live", "latest", "updates",
        "highlights", "told", "said", "added", "years", "year",
        "since", "amid", "following", "news"
    }
    words = []
    for word in text.split():
        clean = word.strip(".,!?-();:'\"").lower()
        if (clean not in stopwords
                and clean not in ABSTRACT_VERBS_AND_ADJ
                and len(clean) > 3
                and not clean.isdigit()
                and clean not in _IMAGE_AMBIGUOUS_WORDS):
            words.append(clean)

    # Build mandatory anchor: country + type
    type_anchor = {
        "war":        "military conflict",
        "politics":   "government official",
        "technology": "technology innovation",
        "business":   "economy finance",
        "disaster":   "emergency crisis",
        "general":    "news",
    }.get(scene_type, "news")

    # Build the query in layers: anchor -> person -> keywords -> suffix
    parts = []
    if country:
        parts.append(country)
    if person and len(person.split()) <= 3:
        parts.append(person)
    parts.append(type_anchor)
    if words:
        # Only add the MOST meaningful 2 words (not generic verbs)
        meaningful = [w for w in words if len(w) > 4][:2]
        parts.extend(meaningful)
    parts.append("news photo")

    query = " ".join(parts)
    return query


def _build_topic_prefix(scene: dict) -> str:
    """
    Build a 2-4 word contextual anchor that is prepended to every
    image search query for this scene.

    Priority:
      1. scene["topic_prefix"] if explicitly set by scene_planner
      2. keyword first 2 meaningful words + scene type
      3. country_context + scene type
      4. scene type alone

    The prefix ensures DuckDuckGo and Pexels stay on-topic even when
    scene-level keywords are generic (e.g. "shocking revelations").
    """
    STOPWORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at",
        "to", "for", "of", "with", "from", "by", "as", "is", "are",
        "was", "were", "been", "be", "has", "have", "had", "will",
        "would", "could", "should", "may", "might", "must", "can",
        "do", "did", "does", "not", "no", "nor", "so", "yet",
        "putting", "citing", "according", "shocking", "revealing",
        "amid", "despite", "following", "regarding", "concerning",
    }

    # Option 1 - explicit topic_prefix from scene_planner
    explicit = scene.get("topic_prefix", "").strip()
    if explicit and len(explicit.split()) >= 2:
        return explicit

    # Option 2 - extract 2 meaningful words from keyword
    keyword   = scene.get("keyword", "")
    kw_words  = [w for w in keyword.lower().split()
                 if w not in STOPWORDS and len(w) > 3][:2]

    scene_type = scene.get("type", "general")
    type_token = {
        "war":        "military conflict",
        "politics":   "government politics",
        "technology": "technology innovation",
        "business":   "economy business",
        "disaster":   "emergency disaster",
        "general":    "news",
    }.get(scene_type, "news")

    if len(kw_words) >= 2:
        return f"{' '.join(kw_words)} {type_token}"

    # Option 3 - country_context + type
    country = scene.get("entities", {}).get("country_context", "")
    if country:
        return f"{country} {type_token}"

    # Option 4 - type alone
    return type_token


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

    # No override -- let ranked query list handle it
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
        print("[ImageFetcher] Pexels API key not set -- skipping Pexels.")
        return None

    params = urllib.parse.urlencode({
        "query":       query,
        "per_page":    5,
        "orientation": "portrait",   # 9:16 -- perfect for vertical video
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

        if not img_url:
            continue

        # PHASE 22: Lifestyle photo rejection for conflict/war queries.
        # Pexels often returns lifestyle/people photos for geopolitical queries
        # ("Ukraine", "Russia"). Check the photo's alt text against lifestyle indicators.
        if scene_type == "war":
            _LIFESTYLE_INDICATORS = {
                "child", "children", "kids", "baby", "babies", "smile", "smiling",
                "family", "mother", "father", "playing", "school", "student",
                "cooking", "food", "restaurant", "shopping", "market", "flower",
                "selfie", "wedding", "birthday", "party", "beach", "sunset",
                "yoga", "fitness", "workout", "sport", "soccer", "football",
                "dog", "cat", "pet", "animal", "nature", "garden", "park",
                "office", "meeting", "business", "computer", "laptop",
            }
            _photo_alt = (
                photo.get("alt", "") or
                photo.get("photographer", "") or ""
            ).lower()
            _photo_desc = "".join((
                photo.get("alt", "") or "",
                photo.get("url", "") or "",
            )).lower()
            if any(ind in _photo_alt for ind in _LIFESTYLE_INDICATORS):
                print(f"[PEXELS LIFESTYLE REJECT] War query but lifestyle image: '{_photo_alt[:60]}'")
                continue

        # Check Pexels photo ID deduplication
        pexels_id = _extract_pexels_id(img_url)
        if pexels_id:
            with _PEXELS_ID_LOCK:
                if pexels_id in _USED_PEXELS_IDS:
                    print(f"[PEXELS DEDUP] Photo ID {pexels_id} already used -- skipping")
                    continue
                _USED_PEXELS_IDS.add(pexels_id)

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
        headers = {
            "User-Agent": "NewsVideoBot/1.0 (educational project; contact: darshan@example.com)",
            "Accept": "application/json"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None

        data = r.json()
        return data.get("thumbnail", {}).get("source")
    except:
        return None


def fetch_og_image(og_url: str, dest_path: str) -> bool:
    """
    Download a pre-captured og:image URL directly to dest_path.
    Returns True if the file was saved and is larger than 2 KB.
    Returns False on any failure -- never raises.

    Used exclusively for Scene 00 so the video always opens with
    the journalist's chosen editorial photo for the story.
    """
    if not og_url or not og_url.startswith("http"):
        return False
    try:
        success = download_with_retry(og_url, dest_path)
        if success and os.path.exists(dest_path):
            size_kb = os.path.getsize(dest_path) // 1024
            if size_kb >= 2:
                print(f"[OG IMAGE] Downloaded {size_kb}KB -> {dest_path}")
                return True
            os.remove(dest_path)
    except Exception as exc:
        print(f"[OG IMAGE] Download failed: {exc}")
    return False


def _extract_article_images(article_url: str, max_images: int = 5) -> list[str]:
    """
    Phase 9: Multi-image article scraper.
    Visit the article page and extract up to `max_images` editorial image URLs.

    Priority order (highest editorial quality first):
      1. og:image / twitter:image  (journalist-chosen hero shot)
      2. <figure> > <img> elements (inline article editorial images)
      3. Large <img> tags (width/height >= 400px in HTML attrs)
      4. src-set candidates (largest available resolution)

    Filters applied:
      - Minimum URL length check (rejects 1x1 trackers)
      - Portrait/avatar pattern rejection (headshot, avatar, profile, etc.)
      - Minimum image size: 30KB (rejects icons, thumbnails, ads)
      - Deduplicated: same URL appears at most once in result list

    Returns empty list on any failure -- never raises.
    Never blocks the pipeline.
    """
    if not article_url or not article_url.startswith("http"):
        return []

    try:
        from bs4 import BeautifulSoup
        import requests as _req
        import re as _re
    except ImportError:
        return []

    # Patterns that indicate portrait/avatar/icon -- reject these
    _PORTRAIT_PATTERNS = [
        "avatar", "headshot", "portrait", "profile", "author",
        "byline", "icon", "logo", "badge", "thumbnail", "thumb",
        "advert", "promo", "sponsor", "banner", "placeholder",
        "1x1", "pixel", "tracking", "spacer",
    ]

    def _is_editorial_url(url: str) -> bool:
        """Return True if the URL looks like a real editorial photo."""
        if not url or len(url) < 20:
            return False
        url_lower = url.lower()
        for pat in _PORTRAIT_PATTERNS:
            if pat in url_lower:
                return False
        return True

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        r = _req.get(article_url, timeout=10, headers=headers)
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []

        # Priority 1: og:image / twitter:image
        for attr in ("og:image", "twitter:image"):
            tag = (
                soup.find("meta", property=attr)
                or soup.find("meta", attrs={"name": attr})
            )
            if tag and tag.get("content"):
                url = tag["content"].strip()
                if url.startswith("//"):
                    url = "https:" + url
                if _is_editorial_url(url):
                    candidates.append(url)

        # Priority 2: <figure> img elements (inline editorial images)
        for fig in soup.find_all("figure"):
            img = fig.find("img")
            if not img:
                continue
            src = img.get("src") or img.get("data-src") or ""
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                from urllib.parse import urlparse as _urlparse
                _base = _urlparse(article_url)
                src = f"{_base.scheme}://{_base.netloc}{src}"
            if src.startswith("http") and _is_editorial_url(src):
                candidates.append(src)

        # Priority 3: Large <img> tags with explicit size attrs
        for img in soup.find_all("img"):
            try:
                w = int(img.get("width", 0))
                h = int(img.get("height", 0))
            except (ValueError, TypeError):
                w, h = 0, 0

            if w >= 400 or h >= 300:
                src = img.get("src") or img.get("data-src") or ""
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    from urllib.parse import urlparse as _urlparse2
                    _base2 = _urlparse2(article_url)
                    src = f"{_base2.scheme}://{_base2.netloc}{src}"
                if src.startswith("http") and _is_editorial_url(src):
                    candidates.append(src)

            # Also try srcset -- pick the largest resolution
            srcset = img.get("srcset", "")
            if srcset:
                srcs_w = []
                for part in srcset.split(","):
                    parts = part.strip().split()
                    if len(parts) >= 1:
                        _src = parts[0]
                        _w = 0
                        if len(parts) >= 2 and parts[1].endswith("w"):
                            try:
                                _w = int(parts[1][:-1])
                            except ValueError:
                                pass
                        srcs_w.append((_w, _src))
                if srcs_w:
                    best_src = max(srcs_w, key=lambda x: x[0])[1]
                    if best_src.startswith("//"):
                        best_src = "https:" + best_src
                    if best_src.startswith("http") and _is_editorial_url(best_src):
                        candidates.append(best_src)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for url in candidates:
            if url not in seen:
                seen.add(url)
                unique.append(url)

        print(f"[ARTICLE IMAGES] Found {len(unique)} candidates from article")
        return unique[:max_images]

    except Exception as exc:
        print(f"[ARTICLE IMAGES] Extraction failed: {exc}")
        return []



def fetch_duckduckgo(query: str) -> str | None:
    """
    Search DuckDuckGo Images with Creative Commons / public-domain filter.

    ddgs.images() supports a license_image parameter:
      "any"     -> all images (default, risky for commercial use)
      "Public"  -> public domain only
      "Share"   -> CC ShareAlike
      "Modify"  -> CC that allow modification (best for video use)

    We try "Modify" first (most permissive CC), then fall back to
    "Share", then "any" as a last resort so we never return nothing
    purely due to license filtering.
    """
    LICENSE_PREFERENCE = ["Modify", "Share", "any"]

    for license_type in LICENSE_PREFERENCE:
        try:
            with DDGS() as ddgs:
                if license_type != "any":
                    results = ddgs.images(
                        query, max_results=8, license_image=license_type
                    )
                else:
                    results = ddgs.images(query, max_results=8)
                for r in results:
                    img_url = r.get("image")
                    if not img_url or img_url in _USED_URLS:
                        continue
                    # Domain block check BEFORE printing or returning
                    if _is_blocked_domain(img_url):
                        print(f"[DDG BLOCKED DOMAIN] Rejected: {img_url[:70]}")
                        continue
                    if _is_clipart_url(img_url):
                        print(f"[DDG CLIPART] Rejected: {img_url[:70]}")
                        continue
                    # Reject .webp files from DuckDuckGo -- these are almost exclusively
                    # from art hosting sites, not news photography
                    if img_url.lower().endswith(".webp"):
                        print(f"[DDG WEBP REJECT] Skipping .webp result: {img_url[:70]}")
                        continue
                    print(
                        f"[DuckDuckGo] Found (license={license_type}): "
                        f"{img_url[:70]}"
                    )
                    return img_url

            print(
                f"[DuckDuckGo] No results with license={license_type} "
                f"for '{query}' - trying next tier"
            )

        except Exception as exc:
            print(f"[DuckDuckGo] Error (license={license_type}): {exc}")

    print(f"[DuckDuckGo] All license tiers exhausted for '{query}'")
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
        print(f"[ImageFetcher] Download returned {size_kb} KB (too small) -- discarding.")
        os.remove(dest_path)
        return False

    # Pixel-level validation: check image dimensions and color profile
    # Rejects: tiny images, pure white/black screens, non-photo files
    try:
        from PIL import Image as _PILCheck
        with _PILCheck.open(dest_path) as _img:
            _w, _h = _img.size
            # Reject images smaller than 100x100 (icon/thumbnail/error page)
            if _w < 100 or _h < 100:
                print(f"[ImageFetcher] Image too small ({_w}x{_h}) -- discarding.")
                os.remove(dest_path)
                return False
            # Convert to RGB for color analysis
            _rgb = _img.convert("RGB")
            # Sample 9 pixels in a grid to detect solid-color images (error pages)
            _pixels = [
                _rgb.getpixel((_w // 4, _h // 4)),
                _rgb.getpixel((_w // 2, _h // 2)),
                _rgb.getpixel((_w * 3 // 4, _h * 3 // 4)),
                _rgb.getpixel((_w // 4, _h * 3 // 4)),
                _rgb.getpixel((_w * 3 // 4, _h // 4)),
            ]
            # If all sampled pixels are nearly identical = solid color = reject
            _r_vals = [p[0] for p in _pixels]
            _g_vals = [p[1] for p in _pixels]
            _b_vals = [p[2] for p in _pixels]
            if (max(_r_vals) - min(_r_vals) < 10 and
                    max(_g_vals) - min(_g_vals) < 10 and
                    max(_b_vals) - min(_b_vals) < 10):
                print(f"[ImageFetcher] Solid-color image detected -- discarding.")
                os.remove(dest_path)
                return False

            _sample_pixels = []
            for _si in range(5):
                for _sj in range(5):
                    _sx = int(_w * (_si + 1) / 6)
                    _sy = int(_h * (_sj + 1) / 6)
                    _sample_pixels.append(_rgb.getpixel((_sx, _sy)))
            _avg_brightness = sum(
                (p[0] + p[1] + p[2]) / 3
                for p in _sample_pixels
            ) / len(_sample_pixels)
            if _avg_brightness > 210:
                print(f"[ImageFetcher] Very bright background ({_avg_brightness:.0f}/255) -- discarding.")
                os.remove(dest_path)
                return False
            if _avg_brightness < 15:
                print(f"[ImageFetcher] Too dark ({_avg_brightness:.0f}/255) -- discarding.")
                os.remove(dest_path)
                return False
    except Exception as _val_exc:
        print(f"[ImageFetcher] Image validation error: {_val_exc} -- discarding.")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False

    print(f"[ImageFetcher] Saved {size_kb} KB -> {dest_path}")
    return True


def fetch_ai_generated(scene: dict, dest_path: str) -> bool:
    """Thin wrapper around the consolidated _ai_generate_contextual_image."""
    return _ai_generate_contextual_image(scene, dest_path)


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
def _ai_generate_contextual_image(scene: dict, dest_path: str) -> bool:
    """
    Generate a contextual news image via Pollinations.ai (free, no API key).
    Returns True if a valid image was saved to dest_path.
    """
    import urllib.parse
    import requests as _req
    import os
    import time

    # Build a descriptive photojournalistic prompt
    _entities = scene.get("entities", {}) or {}
    _country  = _entities.get("country_context", "") or ""
    _person   = _entities.get("person", "") or ""
    _type     = scene.get("type", "general")
    _text     = scene.get("text", "") or ""
    _keyword  = scene.get("keyword", "") or ""

    # Extract concrete nouns from scene text
    _SKIP = {
        "the","a","an","is","are","was","were","and","or","but","in","on",
        "at","to","for","of","with","by","from","as","it","this","that",
        "has","have","had","will","would","could","should","been","being",
        "said","told","noted","added","confirmed","reported","announced",
    }
    _topic_words = []
    for _w in (_keyword + " " + _text).split():
        _c = _w.strip(".,!?;:()").lower()
        if len(_c) > 4 and _c not in _SKIP and not _c.isdigit():
            _topic_words.append(_c)
        if len(_topic_words) >= 4:
            break

    # Style by scene type
    _STYLE = {
        "war":        "military conflict zone AP photojournalism dramatic lighting 35mm",
        "politics":   "government press conference Reuters news photo professional",
        "disaster":   "emergency response crisis scene documentary natural lighting",
        "business":   "financial district Bloomberg editorial corporate documentary",
        "technology": "modern technology laboratory science journalism editorial",
        "general":    "news event editorial photojournalism professional realistic",
    }
    _style = _STYLE.get(_type, _STYLE["general"])

    _parts = []
    if _country:
        _parts.append(_country)
    if _person and len(_person.split()) <= 3:
        _parts.append(_person)
    _parts.extend(_topic_words[:3])
    _parts.append(_style)
    _parts.append("4K no watermark no text portrait 9:16")

    _prompt = " ".join(_parts)
    _encoded = urllib.parse.quote(_prompt[:300])
    import hashlib
    _seed = int(hashlib.md5(_text.encode("utf-8")).hexdigest(), 16) % 99999
    _url = (
        f"https://image.pollinations.ai/prompt/{_encoded}"
        f"?width=1080&height=1920&nologo=true&seed={_seed}"
    )

    global _POLLINATIONS_FAILED_THIS_RUN, _POLLINATIONS_FAIL_COUNT
    with _FETCH_LOCK:
        if _POLLINATIONS_FAILED_THIS_RUN:
            print("[AI-IMG] Pollinations is globally disabled this run (previous failures).")
            return False

    _backoff_timeouts = [15, 25]  # PHASE 19: reduced timeouts with exponential backoff
    for _attempt, _timeout in enumerate(_backoff_timeouts):
        try:
            _r = _req.get(_url, timeout=_timeout, stream=False)
            if _r.status_code == 200:
                _content = _r.content
                # Validate: must be at least 10KB and be a real image
                if len(_content) < 10_000:
                    time.sleep(2)
                    continue
                _magic = _content[:4]
                if _magic[:2] != b'\xff\xd8' and _magic[:4] != b'\x89PNG':
                    time.sleep(2)
                    continue
                with open(dest_path, "wb") as _f:
                    _f.write(_content)
                _kb = os.path.getsize(dest_path) // 1024
                print(f"[AI-IMG] Generated {_kb}KB: '{_prompt[:60]}'")
                with _FETCH_LOCK:
                    _POLLINATIONS_FAIL_COUNT = 0
                return True
        except Exception as _exc:
            print(f"[AI-IMG] Attempt {_attempt+1} failed (timeout={_timeout}s): {_exc}")
            time.sleep(2 * (_attempt + 1))

    with _FETCH_LOCK:
        _POLLINATIONS_FAIL_COUNT += 1
        if _POLLINATIONS_FAIL_COUNT >= 2:
            print("[AI-IMG] CRITICAL: Pollinations failed twice. Disabling for the rest of the run.")
            _POLLINATIONS_FAILED_THIS_RUN = True

    return False


def fetch_image(scene: dict, index: int) -> str | None:
    global _USED_IMAGE_PATHS, _USED_PEXELS_IDS, _POLLINATIONS_FAILED_THIS_RUN, _POLLINATIONS_FAIL_COUNT
    # Reset registry only on scene 0 (start of a new video run)
    if index == 0:
        with _FETCH_LOCK:
            _USED_IMAGE_PATHS.clear()
            _USED_PEXELS_IDS.clear()
            _POLLINATIONS_FAILED_THIS_RUN = False
            _POLLINATIONS_FAIL_COUNT = 0
            if hasattr(fetch_image, "_article_img_cache"):
                fetch_image._article_img_cache = {}
            if hasattr(fetch_image, "_article_img_index"):
                fetch_image._article_img_index = {}
            print("[DEDUP] Asset registry cleared for new run")

    # PHASE 20: Map Image Local Cache Lock
    # If this is the map stinger context, verify if we already have the map image
    # generated in this run to avoid geopy/CartoDB API abuse.
    _map_path = scene.get("map_image_path")
    if _map_path and os.path.exists(_map_path) and os.path.getsize(_map_path) > 10240:
        print(f"[MAP CACHE HIT] Map image exists: {_map_path}")
        _USED_IMAGE_PATHS.add(_map_path)
        return _map_path

    keyword  = scene.get("keyword", "")
    # PHASE 18: Strip ambiguous nouns from image queries
    _AMBIGUOUS = {
        "body", "bodies", "force", "forces", "clear", "clearing",
        "ground", "grounds", "base", "bases", "capital", "head",
        "figure", "figures", "front", "back", "state", "states",
        "lead", "leads", "case", "cases", "point", "points",
        "run", "runs", "running", "stand", "stands", "holding",
        "strike", "strikes", "striking", "target", "targets",
        "show", "shows", "showing", "dozens", "hundreds", "thousands",
        "videos", "video", "footage", "live", "latest", "updates",
        "highlights", "told", "said", "added", "years", "year",
        "since", "amid", "following", "news"
    }
    keyword = " ".join(w for w in keyword.split() if w.lower() not in _AMBIGUOUS)
    keyword  = clean_keyword(keyword)
    sentence = scene.get("text", "")
    scene_type = scene.get("type", "general")
    article_url = scene.get("article_url", "")
    og_image_url = None
    og_context_words = ""

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
    # Scenes 0 and 1 = establishing shots -> use global Person + Location anchor.
    # Scenes 2+      = detail shots       -> use hybrid scene-level query.
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
        # Establishing scenes -- global Person + Location anchor
        if person and location:
            entity_query = f"{person} {location} news photo"
        elif person and country_context:
            entity_query = f"{person} {country_context} news photo"
        elif location:
            type_suffix  = f" {scene_type}" if scene_type != "general" else ""
            entity_query = f"{location}{type_suffix} {scene_kw_str} news photo".strip()
        elif country_context and scene_kw_str:
            entity_query = f"{country_context} {scene_kw_str} news photo"

    else:
        # Detail scenes -- hybrid: country + scene content + type
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
                type_suffix  = f" {scene_type}" if scene_type != "general" else ""
                entity_query = f"{country_context} {scene_kw_str}{type_suffix} news photo"

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

    # -- Contextual prefix injection ------------------------------------
    # Prepend a topic anchor to prevent off-topic image results.
    # e.g. "infant safety news" + "shocking revelations secret news photo"
    _topic_prefix = _build_topic_prefix(scene)
    if _topic_prefix and _topic_prefix.lower() not in query.lower():
        query = f"{_topic_prefix} {query}"
        query = clean_query(query)
        print(f"[TOPIC PREFIX] Applied: '{_topic_prefix}' -> {query[:80]}")

    # PHASE 22: Conflict-zone image override.
    # When the story is a conflict/war story AND the country is in a known conflict zone,
    # force the Pexels query to use conflict-specific anchors instead of generic terms.
    # This prevents lifestyle/people photos from being returned for war stories.
    _CONFLICT_ZONES = {
        "ukraine", "russia", "israel", "gaza", "palestine", "iran", "syria",
        "yemen", "sudan", "ethiopia", "myanmar", "afghanistan", "iraq",
        "lebanon", "west bank", "taiwan",
    }
    _CONFLICT_HEADLINE_WORDS = {
        "attack", "killed", "drone", "strike", "war", "troops", "missile",
        "bomb", "military", "airstrike", "ceasefire", "invasion", "conflict",
        "offensive", "shelling", "artillery", "casualties", "explosion",
    }
    _headline_lower = (scene.get("headline", "") or "").lower()
    _country_lower = country_context.lower() if country_context else ""
    _text_lower = sentence.lower()
    _is_conflict_story = (
        any(cz in _country_lower for cz in _CONFLICT_ZONES) or
        any(cz in _text_lower for cz in _CONFLICT_ZONES)
    ) and (
        any(cw in _headline_lower for cw in _CONFLICT_HEADLINE_WORDS) or
        any(cw in _text_lower for cw in _CONFLICT_HEADLINE_WORDS)
    )
    if _is_conflict_story and scene_type != "war":
        print(f"[IMAGE OVERRIDE] Conflict zone '{country_context}' + conflict headline -> scene_type='war'")
        scene_type = "war"
    if _is_conflict_story:
        _conflict_kws = " ".join(scene_keywords[:2]) if scene_keywords else ""
        if country_context and _conflict_kws and _conflict_kws.lower() not in country_context.lower():
            entity_query = f"{country_context} {_conflict_kws} war conflict news photo"
        else:
            _country_anchor = country_context if country_context else "military"
            _QUERY_VARIATIONS = [
                f"{_country_anchor} military airstrike news photo",
                f"{_country_anchor} conflict zone destruction news photo",
                f"{_country_anchor} soldiers troops military news photo",
                f"{_country_anchor} war aftermath damage news photo",
                f"{_country_anchor} missile drone attack news photo",
                f"{_country_anchor} military operation forces news photo",
                f"{_country_anchor} conflict casualties emergency news photo",
                f"{_country_anchor} war crisis military news photo",
                f"{_country_anchor} armed forces operation news photo",
                f"{_country_anchor} battlefield conflict scene news photo",
            ]
            entity_query = _QUERY_VARIATIONS[index % len(_QUERY_VARIATIONS)]

        print(f"[IMAGE OVERRIDE] War override (scene {index}): '{entity_query[:60]}'")
        query = clean_query(entity_query)

    if article_url and index >= 2:
        # PHASE 10: Standardized cache key length to 50 chars
        _og_cache_key = f"_og_result_{article_url[:50]}"
        _og_fail_key = f"_og_failed_{article_url[:50]}"

        if _og_fail_key in _USED_URLS:
            _og_context_url = None
        elif _og_cache_key in _USED_URLS:
            _og_context_url = getattr(fetch_image, "_og_url_cache", {}).get(article_url)
        else:
            _og_context_url = _extract_og_url(article_url)
            if _og_context_url:
                if not hasattr(fetch_image, "_og_url_cache"):
                    fetch_image._og_url_cache = {}
                fetch_image._og_url_cache[article_url] = _og_context_url
                _USED_URLS.add(_og_cache_key)
            else:
                _USED_URLS.add(_og_fail_key)
        if _og_context_url:
            _url_lower = _og_context_url.lower()
            _is_news_domain = any(dom in _url_lower for dom in [
                "bbc", "reuters", "cnn", "nytimes", "apnews", "bloomberg",
                "theguardian", "independent", "ndtv", "aljazeera", "cnbc"
            ])
            _url_filename = _og_context_url.rstrip("/").split("/")[-1].split(".")[0]
            _has_uuid = bool(__import__("re").match(r"^[0-9a-f\-]{20,}$", _url_filename))
            if _has_uuid or not _is_news_domain:
                og_context_words = ""
            else:
                import os as _os
                import re as _re
                import urllib.parse as _urlparse

                parsed_path = _urlparse.urlparse(_og_context_url).path
                filename = _os.path.splitext(_os.path.basename(parsed_path))[0]
                words = _re.findall(r"[a-zA-Z]{4,}", filename.lower())
                stops = {
                    "image","photo","photos","picture","pictures","resize",
                    "thumbnail","thumb","large","small","medium","width","height",
                    "jpg","jpeg","png","webp","bbc","news","ichef","standard",
                }
                clean_words = []
                for word in words:
                    if word not in stops and word not in clean_words:
                        clean_words.append(word)
                valid_words = [
                    w for w in clean_words
                    if not _re.match(r"^[0-9a-f]{4,}$", w)
                    and len(w) >= 4
                    and w.isalpha()
                ]
                og_context_words = " ".join(valid_words[:3])
                if og_context_words:
                    print(f"[OG] Pexels context words: {og_context_words}")
                else:
                    og_context_words = ""
        
    cache_key = hashlib.md5(f"{query}|{sentence}".encode()).hexdigest()[:10]
    dest = os.path.join(IMAGE_DIR, f"scene_{index:02d}_{cache_key}.jpg")

    # PHASE 20: If cache exists and is valid, reuse it rather than calling APIs again
    # unless FORCE_REFRESH is explicitly set to True
    if os.path.exists(dest) and os.path.getsize(dest) > 10240 and not FORCE_REFRESH:
        print(f"[CACHE HIT] Reusing existing image for scene {index}: {dest}")
        _USED_IMAGE_PATHS.add(dest)
        return dest

    if os.path.exists(dest):
        os.remove(dest)
        print(f"[CACHE] Deleted stale cache to force refresh: {dest}")

    print(f"\n[ImageFetcher] Scene {index:02d} | type='{scene_type}' | keyword='{keyword}'")
    print(f"[SMART QUERY]: {query}")
    print(f"[CONTEXT]: {scene.get('context')}")

    image_url = None
    source = "none"

    # ── Priority 0: Editorial og:image -- Scene 00 ONLY ───────────────
    # fetch_og_image() returns bool (True/False), not a URL string.
    # The pre-captured URL is in scene["og_image_url"].
    # We use _og_url (the string) for logging, not the bool return.
    if index == 0:
        _og_url = scene.get("og_image_url", "")
        if _og_url and isinstance(_og_url, str) and _og_url.startswith("http"):
            _og_dest = os.path.join(
                IMAGE_DIR, f"scene_{index:02d}_og_editorial.jpg"
            )
            # Change 11: Check cache before network call
            if os.path.exists(_og_dest):
                print(f"[P0] Using cached editorial image: {_og_dest}")
                _USED_URLS.add(_og_url)
                _USED_IMAGE_PATHS.add(_og_dest)
                return _og_dest

            _success = fetch_og_image(_og_url, _og_dest)   # returns bool
            if _success:
                _USED_URLS.add(_og_url)
                _USED_IMAGE_PATHS.add(_og_dest)
                # Log the URL string, NOT the bool
                print(f"[P0] Editorial og:image: {_og_url[:70]}")
                return _og_dest
            else:
                print("[P0] og:image download failed -- falling through to P1")
        else:
            print("[P0] No og_image_url in scene -- falling through to P1")

    # ── Priority 0.5: Article editorial images (scenes 1–4) ──────────────────
    # Uses images scraped from the article page -- highest editorial relevance.
    # Runs ONLY if we haven't already found an image above.
    if not image_url and article_url and 1 <= index <= 4:
        # Thread-safe: use a module-level dict keyed by article_url
        if not hasattr(fetch_image, "_article_img_cache"):
            fetch_image._article_img_cache = {}
        if not hasattr(fetch_image, "_article_img_index"):
            fetch_image._article_img_index = {}

        _cached_key = article_url[:80]

        # Populate cache on first call for this article
        if _cached_key not in fetch_image._article_img_cache:
            _art_imgs = _extract_article_images(article_url, max_images=8)
            fetch_image._article_img_cache[_cached_key] = _art_imgs
            fetch_image._article_img_index[_cached_key] = 0
            if _art_imgs:
                print(f"[P0.5] Scraped {len(_art_imgs)} article images")
        else:
            _art_imgs = fetch_image._article_img_cache[_cached_key]

        # Get the NEXT unused image using a per-article counter
        # This prevents scene 1, 2, 3, 4 from all getting image[0]
        _start_idx = fetch_image._article_img_index.get(_cached_key, 0)

        for _i in range(_start_idx, len(_art_imgs)):
            _art_url = _art_imgs[_i]
            if _art_url in _USED_URLS:
                continue  # already used by a previous scene
            if _is_blocked_domain(_art_url) or _is_blocked_url(_art_url):
                continue

            _art_dest = os.path.join(IMAGE_DIR, f"scene_{index:02d}_article_{_i}.jpg")
            if download_with_retry(_art_url, _art_dest):
                size_kb = os.path.getsize(_art_dest) // 1024 if os.path.exists(_art_dest) else 0
                if size_kb >= 10:
                    # Mark URL as used AND advance the counter
                    _USED_URLS.add(_art_url)
                    _USED_IMAGE_PATHS.add(_art_dest)
                    fetch_image._article_img_index[_cached_key] = _i + 1
                    print(f"[P0.5] Article image #{_i}: {_art_url[:60]} ({size_kb}KB)")
                    return _art_dest
                if os.path.exists(_art_dest):
                    os.remove(_art_dest)

        # All article images exhausted - fall through to AI image generation
        print(f"[P0.5] No unique article images left - falling through to AI generation")






    # ── Priority 1: Wikipedia person photo (establishing scenes 0–3) ──────────
    person = entities.get("person", "")
    if not image_url and person and index <= 3:
        # Only use Wikipedia person photo for first 3 scenes (establishing)
        wiki_person_url = fetch_wikipedia(person)
        if wiki_person_url:
            image_url = wiki_person_url
            source = "wikipedia_person"
            print(f"[P1] Wikipedia person: '{person}'")

    # ── Priority 1.5: Pexels (promoted to primary when key is available) ──────
    if not image_url and PEXELS_API_KEY:
        # Try semantic query first, then entity query
        _pexels_queries = [query] + _build_query(scene)[:2]
        for _pq in _pexels_queries[:3]:
            _pq = clean_query(_pq)
            _pexels_url = fetch_with_retry(_pq, index)
            if _pexels_url:
                image_url = _pexels_url
                source = "pexels_primary"
                print(f"[P1.5] Pexels primary: found for '{_pq[:50]}'")
                break

    # ── Priority 2: Wikipedia topic fallback (Promoted in Phase 9) ─────────────
    if not image_url:
        wiki_url = fetch_wikipedia(query)
        if wiki_url and wiki_url not in _USED_URLS:
            image_url = wiki_url
            source = "wikipedia_topic"
            print(f"[P2] Wikipedia topic: found for '{query}'")
    if not image_url:
        _ddg_url = fetch_duckduckgo(query)
        if _ddg_url and _is_clipart_url(_ddg_url):
            print(f"[P2] DuckDuckGo: clipart rejected -- {_ddg_url[:60]}")
            _ddg_url = None
        if _ddg_url:
            image_url = _ddg_url
            source    = "duckduckgo"
            print(f"[P2] DuckDuckGo: found for '{query}'")

    # ── Priority 3: Unsplash ─────────────────────────────────────────────
    if not image_url:
        image_url = fetch_unsplash(query, index)
        source = "unsplash" if image_url else source

    # ── Priority 4: Pexels (fallback when no key, or primary already tried) ──
    if not image_url and not PEXELS_API_KEY:  # Only if key not set (already ran above)
        ranked_queries = _build_query(scene)
        for ranked_q in ranked_queries[:3]:
            ranked_q = clean_query(ranked_q)
            if og_context_words:
                ranked_q = clean_query(f"{ranked_q} {og_context_words}")
            if ranked_q and ranked_q != query:
                image_url = fetch_with_retry(ranked_q, index)
                if image_url:
                    source = "pexels"
                    break
        if not image_url:
            pexels_query = clean_query(f"{query} {og_context_words}") if og_context_words else query
            image_url = fetch_with_retry(pexels_query, index)
            source = "pexels" if image_url else source

    # ── Priority 5: Wikipedia topic fallback ─────────────────────────────
    if not image_url:
        image_url = fetch_wikipedia(query)
        source = "wiki" if image_url else source

    # -- Priority 5.5: AI-generated contextual image (Pollinations.ai) ----------
    # PHASE 16/20: Generates a custom image from scene context, more relevant than generic stock.
    # Scene 0 always uses the OG editorial image, so AI gen skips scene 0.
    # Flipped priority: runs after web/Pexels and Wikipedia search before generic AI fallback.
    if not image_url and index >= 1:
        _ai_dest = os.path.join(IMAGE_DIR, f"scene_{index:02d}_ai_generated.jpg")
        if _ai_generate_contextual_image(scene, _ai_dest):
            _USED_IMAGE_PATHS.add(_ai_dest)
            print(f"[P5.5] AI-generated contextual image for scene {index}")
            return _ai_dest

    # ── Priority 6: AI Image Generation (Pollinations.ai, free) ─────────────
    if not image_url:
        ai_dest = os.path.join(IMAGE_DIR, f"scene_{index:02d}_ai.jpg")
        if fetch_ai_generated(scene, ai_dest):
            _USED_IMAGE_PATHS.add(ai_dest)
            image_url = ai_dest
            source = "ai_generated"

    # ── Priority 7: Last resort ──────────────────────────────────────────
    if not image_url:
        print("[ERROR] No image found -- using fallback")
        image_url = fetch_fallback(query)
        source = "fallback"

    if image_url in _USED_URLS:
        print("[ImageFetcher] Duplicate image detected -- trying Wikipedia fallback")
        # Try Wikipedia first -- more likely to have topic-relevant thumbnail
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
                    if _is_clipart_url(alt_url):
                        print(f"[DEDUP FALLBACK] Clipart rejected: {alt_url[:60]}")
                        continue
                    image_url = alt_url
                    fallback_found = True
                    print(f"[ImageFetcher] Alt query fallback found: {vq}")
                    break
            if not fallback_found:
                # Last resort -- generic Unsplash fallback
                image_url = fetch_fallback(query + " " + str(index))
                print(f"[ImageFetcher] Using generic fallback for scene {index}")

    # ── Protected CDN filter ──────────────────────────────────────────
    if _is_blocked_url(image_url):
        print(
            f"[BLOCKED] Protected CDN detected - skipping: "
            f"{image_url[:80]}"
        )
        # Try DuckDuckGo with an alternate query as immediate replacement
        _alt_q = clean_query(f"{query} site:commons.wikimedia.org OR pexels.com")
        _alt   = fetch_duckduckgo(_alt_q)
        if _alt and not _is_blocked_url(_alt):
            image_url = _alt
            source    = "duckduckgo_cc_retry"
            print(f"[BLOCKED] CC replacement found: {image_url[:70]}")
        else:
            # Accept Pexels fallback or blur-safety-net
            image_url = None

    if not image_url:
        return None

    _USED_URLS.add(image_url)

    print(f"[IMAGE SOURCE]: {source}")

    if not image_url:
        return None

    success = download_with_retry(image_url, dest)

    if not success:
        print("[RETRY FAILED] Primary source download failed -- trying AI generation")
        ai_dest = os.path.join(IMAGE_DIR, f"scene_{index:02d}_ai_fallback.jpg")
        if fetch_ai_generated(scene, ai_dest):
            print(f"[AI GEN] Fallback image generated for scene {index}")
            _USED_IMAGE_PATHS.add(ai_dest)
            return ai_dest

        # Last resort -- generic fallback URL
        print("[AI GEN] Failed -- using generic fallback URL")
        image_url = fetch_fallback(query)
        if image_url and _is_blocked_url(image_url):
            print(f"[BLOCKED] Protected CDN detected - skipping")
            image_url = None
        success = download_with_retry(image_url, dest) if image_url else False

    if not success:
        # Final AI attempt before giving up
        ai_dest = os.path.join(IMAGE_DIR, f"scene_{index:02d}_ai_last.jpg")
        if fetch_ai_generated(scene, ai_dest):
            _USED_IMAGE_PATHS.add(ai_dest)
            return ai_dest
        return None

    # ── Asset deduplication check ─────────────────────────────────────
    # If this exact file path was already used in this run,
    # the visual cut system would repeat the same image multiple times.
    # Trigger the blur-safety-net instead by returning None so
    # video_builder uses the high-contrast blurred previous frame.
    with _FETCH_LOCK:
        if dest in _USED_IMAGE_PATHS:
            print(
                f"[DEDUP] Path '{os.path.basename(dest)}' already used "
                f"this run - skipping to prevent asset loop"
            )
            return None

        _USED_IMAGE_PATHS.add(dest)
    return dest

def download_with_retry(url, path, retries=2):
    if not url:
        return False
    # Reroute to call _download() which implements user-agent, Referer, size, and dimensions validation
    for i in range(retries):
        try:
            if _download(url, path):
                return True
        except Exception as exc:
            print(f"[ImageFetcher] download_with_retry attempt {i+1} failed: {exc}")
            continue
    return False


# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = fetch_image(
        scene = {
            "keyword":  "Iran",
            "text":     "Trump issues threat to Iran over Strait of Hormuz.",
            "type":     "politics"
        },
        index = 0
    )
    print("\nResult:", result)
