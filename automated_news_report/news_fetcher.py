# modules/news_fetcher.py — Fetch the latest BBC news article via RSS

import feedparser
import sys
from config import RSS_FEED_URL


def fetch_latest_article(feed_url: str = RSS_FEED_URL) -> dict:
    """
    Parse the RSS feed and return a dict  with title, link, and summary
    for the most-recent entry.

    Returns:
        {
            "title":   str,
            "link":    str,
            "summary": str,
        }
    Raises:
        RuntimeError if the feed is unreachable or empty.
    """
    print(f"[NewsFetcher] Fetching feed: {feed_url}")
    d = feedparser.parse(feed_url)

    if d.bozo and not d.entries:
        raise RuntimeError(
            f"[NewsFetcher] Feed parse error: {d.bozo_exception}"
        )

    if not d.entries:
        raise RuntimeError("[NewsFetcher] Feed returned no entries.")

    entry = d.entries[0]

    title   = getattr(entry, "title",   "No title")
    link    = getattr(entry, "link",    "")
    summary = getattr(entry, "summary", "")

    # Some feeds wrap summaries in HTML — strip tags naively
    import re
    summary = re.sub(r"<[^>]+>", " ", summary).strip()

    # Fall back to title if summary is empty
    if not summary:
        summary = title

    article = {"title": title, "link": link, "summary": summary}
    print(f"[NewsFetcher] Article: {title}")
    return article


if __name__ == "__main__":
    art = fetch_latest_article()
    for k, v in art.items():
        print(f"  {k}: {v[:120]}")
