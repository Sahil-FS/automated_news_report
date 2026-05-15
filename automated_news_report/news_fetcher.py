# news_fetcher.py — Fetch the latest BBC news article via RSS
#
# FIX (2026-05-05):
#   feedparser.parse(url) does its own HTTP internally with no timeout
#   and no retry — a partial TCP response causes IncompleteRead and crashes
#   the pipeline.  Solution: fetch RSS bytes ourselves with requests
#   (timeout + retry + browser User-Agent), then pass raw bytes to
#   feedparser.parse() which only does XML parsing when given bytes/string.

import re
import time
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import RSS_FEED_URL


# ── Feeds scored together for high-impact headline selection ────────────────
_ALL_FEEDS = [
    # BBC
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/world/asia/india/rss.xml",
    # Reuters
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://rss.dw.com/rdf/rss-en-world",
    # Times of India
    "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
    # NDTV
    "https://feeds.feedburner.com/ndtvnews-top-stories",
    # Al Jazeera
    "https://www.aljazeera.com/xml/rss/all.xml",
]

# Phase 9 ─ Category-specific feed pools
_CATEGORY_FEEDS = {
    "general": _ALL_FEEDS,

    "sports": [
        "https://feeds.bbci.co.uk/sport/rss.xml",
        "https://www.espn.com/espn/rss/news",
        "https://timesofindia.indiatimes.com/rssfeeds/4719161.cms",  # TOI Sports
        "https://rss.dw.com/rdf/rss-en-sport",
        "https://feeds.skynews.com/feeds/rss/sports.xml",
    ],

    "entertainment": [
        "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
        "https://www.hollywoodreporter.com/feed",
        "https://variety.com/feed/",
        "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms",  # TOI Entertainment
        "https://feeds.skynews.com/feeds/rss/entertainment.xml",
    ],

    "business": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://rss.dw.com/rdf/rss-en-business",
        "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms",   # TOI Business
        "https://feeds.skynews.com/feeds/rss/business.xml",
        "https://feeds.feedburner.com/ndtvnews-business",
    ],

    "tech": [
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "https://rss.dw.com/rdf/rss-en-technology",
        "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms",  # TOI Tech
        "https://feeds.skynews.com/feeds/rss/technology.xml",
        "https://feeds.feedburner.com/ndtvnews-tech-media-gadgets",
    ],

    "world": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.dw.com/rdf/rss-en-world",
        "https://feeds.skynews.com/feeds/rss/world.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],

    "politics": [
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://feeds.skynews.com/feeds/rss/politics.xml",
    ],

    "war": [
        "https://www.aljazeera.com/xml/rss/all.xml", # Focuses on conflict
        "https://rss.dw.com/rdf/rss-en-world",
    ],

    "space": [
        "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    ],
}


# ── HTTP session with automatic retry ────────────────────────────────────────
def _make_session() -> requests.Session:
    """
    Return a requests Session with:
      - 3 retries on connection/read errors (exponential back-off)
      - Browser-like User-Agent so BBC does not throttle Python clients
    """
    session = requests.Session()

    retry = Retry(
        total=3,
        backoff_factor=1.0,           # waits 1s, 2s, 4s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control":   "no-cache",
    })
    return session


def _fetch_feed_bytes(url: str, timeout: int = 12) -> bytes | None:
    """
    Download RSS bytes via requests.
    Returns raw bytes on success, None on any failure.

    Using requests instead of feedparser's built-in HTTP means:
      - We control the timeout (feedparser has none by default)
      - Retries happen automatically via HTTPAdapter
      - IncompleteRead from http.client is caught and handled
      - We get a clear error message instead of a silent hang
    """
    session = _make_session()
    try:
        print(f"[NewsFetcher] GET {url}  (timeout={timeout}s)")
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()

        content = resp.content
        if len(content) < 100:
            print(f"[NewsFetcher] Response too short ({len(content)} bytes) — skipping")
            return None

        print(f"[NewsFetcher] Feed fetched OK: {len(content):,} bytes")
        return content

    except requests.exceptions.Timeout:
        print(f"[NewsFetcher] ✗ Timeout after {timeout}s → {url}")
        return None
    except requests.exceptions.ConnectionError as exc:
        print(f"[NewsFetcher] ✗ Connection error → {url}: {exc}")
        return None
    except requests.exceptions.HTTPError as exc:
        print(f"[NewsFetcher] ✗ HTTP error → {url}: {exc}")
        return None
    except Exception as exc:
        # Catches http.client.IncompleteRead and anything else
        print(f"[NewsFetcher] ✗ Unexpected error → {url}: {type(exc).__name__}: {exc}")
        return None
    finally:
        session.close()


def _parse_entry(entry) -> dict:
    """Extract title, link, summary from a feedparser entry."""
    title   = getattr(entry, "title",   "No title")
    link    = getattr(entry, "link",    "")
    summary = getattr(entry, "summary", "")

    # Strip HTML tags from summary
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()

    # Fall back to title when summary is missing
    if not summary:
        summary = title

    return {"title": title, "link": link, "summary": summary}


def _score_headline(title: str) -> int:
    """
    Score a headline by counting high-impact keyword matches.
    Higher score = more likely to produce engaging short-form content.
    Returns integer score 0-100.
    """
    HIGH_IMPACT_TOKENS = {
        # Conflict / Crisis - weight 3
        "war": 3, "strike": 3, "attack": 3, "clash": 3, "invasion": 3,
        "explosion": 3, "airstrike": 3, "troops": 3, "ceasefire": 3,
        "missile": 3, "nuclear": 3, "hostage": 3, "siege": 3,
        # Death / Disaster - weight 3
        "dead": 3, "killed": 3, "deadly": 3, "death": 3, "disaster": 3,
        "earthquake": 3, "flood": 3, "crash": 3, "collapse": 3,
        # Politics / Power - weight 2
        "warns": 2, "threatens": 2, "sanctions": 2, "ban": 2,
        "resign": 2, "arrest": 2, "crisis": 2, "shutdown": 2,
        "impeach": 2, "election": 2, "coup": 2,
        # Economy / Scandal - weight 2
        "scam": 2, "fraud": 2, "billion": 2, "record": 2, "crash": 2,
        "inflation": 2, "bankrupt": 2, "scandal": 2,
        # India/Global priority - weight 2
        "india": 2, "modi": 2, "pakistan": 2, "china": 2, "iran": 2,
        "russia": 2, "ukraine": 2, "israel": 2, "gaza": 2,
        # Soft positive - weight 1
        "historic": 1, "landmark": 1, "breakthrough": 1, "first": 1,
    }
    title_lower = (title or "").lower()
    score = 0
    for token, weight in HIGH_IMPACT_TOKENS.items():
        if token in title_lower:
            score += weight
    return min(score, 100)


def _fetch_og_image_url(article_url: str) -> str:
    """
    Visit the article page and extract the og:image meta tag URL.
    Returns the image URL string, or "" on any failure.
    Never raises — pipeline must not crash here.
    """
    if not article_url or not article_url.startswith("http"):
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""   # bs4 not installed — skip silently

    try:
        r = requests.get(
            article_url,
            timeout=8,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        tag  = (
            soup.find("meta", property="og:image")
            or soup.find("meta", attrs={"name": "twitter:image"})
        )
        if tag and tag.get("content"):
            url = tag["content"].strip()
            if url.startswith("//"):
                url = "https:" + url
            print(f"[NewsFetcher] og:image: {url[:80]}")
            return url
    except Exception as exc:
        print(f"[NewsFetcher] og:image fetch failed: {exc}")
    return ""


def _scrape_article_body(url: str, max_chars: int = 3000) -> str:
    """
    Scrape the full text body from a news article URL.
    Returns the article text (up to max_chars characters) or empty string on failure.

    Extraction strategy (ordered by quality):
      1. <article> tag content — most news sites use semantic HTML
      2. Largest <div> with article-like class names
      3. All <p> tags inside main content area
      4. All <p> tags on page (fallback)

    Filters:
      - Skip navigation, footer, sidebar, ad paragraphs
      - Skip paragraphs under 40 characters (headlines, labels, bylines)
      - Stop at "Related" / "Also Read" / "Advertisement" sections
    """
    if not url or not url.startswith("http"):
        return ""

    try:
        from bs4 import BeautifulSoup
        import requests as _req
    except ImportError:
        return ""

    try:
        r = _req.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=12,
        )
        if r.status_code != 200:
            return ""

        soup = BeautifulSoup(r.text, "html.parser")

        # Remove noise: scripts, styles, nav, ads, footers, sidebars
        for _tag in soup.find_all(["script", "style", "nav", "footer",
                                    "aside", "header", "form", "button",
                                    "noscript", "iframe", "figure"]):
            _tag.decompose()

        # Strategy 1: semantic <article> tag
        _article_tag = soup.find("article")
        if _article_tag:
            _paragraphs = _article_tag.find_all("p")
        else:
            # Strategy 2: divs with article-like class names
            _ARTICLE_CLASSES = [
                "article-body", "story-body", "article__body",
                "article-content", "story-content", "post-content",
                "entry-content", "content-body", "article-text",
                "newstext", "news-body", "storyBody", "body-content",
                "articleBody", "article_content",
            ]
            _content_div = None
            for _cls in _ARTICLE_CLASSES:
                _content_div = (
                    soup.find("div", class_=lambda c: c and _cls in " ".join(c))
                    or soup.find("div", id=lambda i: i and _cls in i)
                )
                if _content_div:
                    break

            if _content_div:
                _paragraphs = _content_div.find_all("p")
            else:
                # Strategy 3: all <p> tags on the page (last resort)
                _paragraphs = soup.find_all("p")

        # Filter and collect text
        _STOP_PHRASES = [
            "related news", "also read", "read also", "advertisement",
            "follow us on", "subscribe to", "get the latest",
            "watch live", "live updates", "breaking news",
            "click here", "read more", "continue reading",
            "sponsored", "partner content",
        ]

        lines = []
        total_chars = 0

        for p in _paragraphs:
            text = p.get_text(separator=" ").strip()

            # Skip short fragments (bylines, labels, captions)
            if len(text) < 40:
                continue

            # Stop at "related/also read" sections
            if any(stop in text.lower() for stop in _STOP_PHRASES):
                break

            lines.append(text)
            total_chars += len(text)

            if total_chars >= max_chars:
                break

        result = " ".join(lines).strip()
        if result:
            print(f"[ArticleScraper] Extracted {len(result)} chars from {url[:60]}")
        return result[:max_chars]

    except Exception as exc:
        print(f"[ArticleScraper] Failed for {url[:60]}: {exc}")
        return ""


def fetch_latest_article(feed_url: str = RSS_FEED_URL, category: str = "general") -> dict:
    """
    Scrape all feeds in the selected category pool simultaneously, score every
    headline using _score_headline(), and return the single highest-scoring article.
    Falls back to the primary feed if all others fail.

    Args:
        feed_url: primary RSS URL (used as first candidate in every category)
        category: one of "general", "sports", "entertainment", "business", "technology"
    """
    import concurrent.futures

    def _fetch_single(url):
        raw = _fetch_feed_bytes(url, timeout=10)
        if not raw:
            return []
        try:
            d = feedparser.parse(raw)
        except Exception:
            return []
        results = []
        for entry in d.entries[:3]:   # top 3 per feed
            article = _parse_entry(entry)
            article["_score"] = _score_headline(article["title"])
            article["_feed"]  = url
            results.append(article)
        return results

    all_articles = []
    # Select category-specific feed pool
    _cat_feeds = _CATEGORY_FEEDS.get(category, _ALL_FEEDS)
    feeds = list(dict.fromkeys([feed_url] + _cat_feeds))
    print(f"[NewsFetcher] Category: '{category}' — scoring from {len(feeds)} feeds...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_single, url): url for url in feeds}
        for future in concurrent.futures.as_completed(futures, timeout=15):
            try:
                all_articles.extend(future.result())
            except Exception:
                pass

    if not all_articles:
        raise RuntimeError(
            "[NewsFetcher] All feeds failed. Check internet connection."
        )

    # Sort by score descending, then pick the best unused headline.
    all_articles.sort(key=lambda a: a["_score"], reverse=True)
    best = all_articles[0]

    print(f"[NewsFetcher] {len(all_articles)} headlines scored across all feeds")

    # Topic memory: skip recently used headlines.
    # Stores the last 10 winning headlines to prevent repeat videos.
    # Memory file: output/used_topics.json (persists across runs).
    import json as _json
    import pathlib as _pathlib

    _memory_file = _pathlib.Path(__file__).parent / "output" / "used_topics.json"
    _memory_file.parent.mkdir(exist_ok=True)

    _used_topics = []
    if _memory_file.exists():
        try:
            _used_topics = _json.loads(_memory_file.read_text(encoding="utf-8"))
        except Exception:
            _used_topics = []

    def _title_fingerprint(title: str) -> str:
        """Short normalized fingerprint for deduplication."""
        import re as _re
        _t = _re.sub(r"[^a-zA-Z0-9]", "", title.lower())
        return _t[:60]

    _used_fps = set(_used_topics)

    for _candidate in all_articles:
        _fp = _title_fingerprint(_candidate["title"])
        if _fp not in _used_fps:
            best = _candidate
            print(f"[NewsFetcher] Selected (score={best.get('_score', '?')}): {best['title']}")
            break
    else:
        print("[NewsFetcher] All recent headlines used - resetting topic memory")
        _used_topics = []
        _used_fps = set()
        best = all_articles[0]
        print(f"[NewsFetcher] Selected (score={best.get('_score', '?')}): {best['title']}")

    print(f"[NewsFetcher] Source feed: {best['_feed']}")

    best.pop("_score", None)
    best.pop("_feed", None)

    _fp_winner = _title_fingerprint(best["title"])
    if _fp_winner not in _used_fps:
        _used_topics.append(_fp_winner)
    _used_topics = _used_topics[-10:]
    try:
        _memory_file.write_text(_json.dumps(_used_topics, indent=2), encoding="utf-8")
    except Exception as _me:
        print(f"[NewsFetcher] Could not save topic memory: {_me}")

    # Fetch editorial press photo from the article page.
    best["og_image_url"] = _fetch_og_image_url(best.get("link", ""))

    # PHASE 14: Scrape full article body for script generation
    _article_url = best.get("link", "")
    if _article_url:
        _full_text = _scrape_article_body(_article_url, max_chars=3000)
        if _full_text and len(_full_text) > 100:
            # Combine: headline + RSS description + full article body
            _rss_desc = best.get("description", "") or best.get("summary", "") or ""
            best["full_article_text"] = (
                f"TITLE: {best.get('title', '')}\n\n"
                f"SUMMARY: {_rss_desc}\n\n"
                f"FULL ARTICLE:\n{_full_text}"
            )
            print(f"[ArticleScraper] Full text ready: {len(_full_text)} chars")
        else:
            best["full_article_text"] = (
                f"TITLE: {best.get('title', '')}\n\n"
                f"{best.get('description', '') or best.get('summary', '')}"
            )

    return best


# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    art = fetch_latest_article()
    print("\n--- Result ---")
    for k, v in art.items():
        print(f"  {k}: {v[:160]}")
