#!/usr/bin/env python3
"""
fetch_news.py - RSS News Aggregation Module

Fetches finance news from configured RSS feeds, normalizes entries,
removes duplicates, and saves structured raw JSON output.
Designed for zero-cost, high-reliability daily execution.
"""

import feedparser
import logging
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Set, Optional
from urllib.parse import urlparse
import requests

# ============================================================================
# CONFIGURATION & PATHS
# ============================================================================
# Dynamically resolve project root regardless of execution context
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
LOG_DIR = PROJECT_ROOT / "logs"

# Ensure runtime directories exist
DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# RSS Feed Registry (Finance-focused, publicly available)
RSS_FEEDS: List[Dict[str, str]] = [
    {"name": "Reuters Markets", "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC Markets",    "url": "https://www.cnbc.com/id/15839135/device/rss/rss.html"},
    {"name": "Yahoo Finance",   "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "Economic Times",  "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
]

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
def setup_logging() -> logging.Logger:
    """Configure structured logging to file and console."""
    log_file = LOG_DIR / "fetch_news.log"
    
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    logger = logging.getLogger("fetch_news")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()  # Prevent duplicate handlers on reload

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (rotation handled by GitHub Actions daily runs)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

logger = setup_logging()

# ============================================================================
# DATA PROCESSING UTILITIES
# ============================================================================

def clean_html(raw_text: str) -> str:
    """Strip HTML tags and normalize whitespace for plain-text summaries."""
    if not raw_text:
        return ""
    # Remove tags
    clean = re.compile(r"<[^>]+>").sub("", raw_text)
    # Collapse multiple spaces/newlines into single spaces
    return re.sub(r"\s+", " ", clean).strip()

def normalize_title(title: str) -> str:
    """Lowercase and strip punctuation for reliable title-based deduplication."""
    if not title:
        return ""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()

# ============================================================================
# CORE FETCHING LOGIC
# ============================================================================

def fetch_single_feed(feed_config: Dict[str, str], timeout: int = 15) -> List[Dict]:
    """
    Fetch and parse a single RSS feed using requests + feedparser.
    
    WHY requests + feedparser?
    - `requests` gives us timeout control, custom User-Agent headers, and connection pooling.
    - `feedparser` handles XML/Atom/RSS parsing quirks and malformed feeds gracefully.
    """
    logger.info(f"Requesting: {feed_config['name']}")
    
    try:
        # Spoof standard browser UA to bypass basic bot blocks on finance sites
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) FinanceNewsBot/1.0"
        }
        response = requests.get(feed_config["url"], headers=headers, timeout=timeout)
        response.raise_for_status()  # Raise on 4xx/5xx

        # Parse feed content
        feed = feedparser.parse(response.content)
        
        # feedparser uses 'bozo' flag for parsing warnings
        if feed.bozo:
            logger.warning(f"Parsing warning for {feed_config['name']}: {feed.bozo_exception}")

        articles = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            
            # Fallback date extraction
            published = entry.get("published", entry.get("updated", ""))
            summary = clean_html(entry.get("summary", entry.get("description", "")))

            # Skip incomplete entries
            if not title or not link:
                logger.debug(f"Skipping incomplete entry: {title or link}")
                continue

            articles.append({
                "title": title,
                "link": link,
                "source": feed_config["name"],
                "published": published,
                "summary": summary[:500] if summary else "",  # Truncate raw summary for storage
            })

        logger.info(f"✅ Parsed {len(articles)} entries from {feed_config['name']}")
        return articles

    except requests.exceptions.Timeout:
        logger.error(f"⏱️ Timeout fetching {feed_config['name']}")
        return []
    except requests.exceptions.HTTPError as e:
        logger.error(f"🌐 HTTP error {feed_config['name']}: {e.response.status_code}")
        return []
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching {feed_config['name']}: {e}")
        return []

def deduplicate_articles(articles: List[Dict]) -> List[Dict]:
    """
    Remove duplicates using URL as primary key, normalized title as fallback.
    WHY dual-key? RSS feeds often syndicate the same article with slight URL variations
    (e.g., ?utm_source=rss) or missing links in malformed feeds.
    """
    seen_urls: Set[str] = set()
    seen_titles: Set[str] = set()
    unique = []

    for article in articles:
        url_key = article["link"]
        title_key = normalize_title(article["title"])

        # Extract domain for stricter URL matching (ignores query params)
        try:
            domain = urlparse(url_key).netloc.replace("www.", "")
            url_canonical = f"{domain}/{urlparse(url_key).path.strip('/')}"
        except Exception:
            url_canonical = url_key

        if url_canonical in seen_urls or title_key in seen_titles:
            continue

        seen_urls.add(url_canonical)
        seen_titles.add(title_key)
        unique.append(article)

    logger.info(f"🧹 Deduplication: {len(articles)} fetched -> {len(unique)} unique")
    return unique

def save_raw_output(articles: List[Dict]) -> Path:
    """Save processed articles to data/raw/YYYY-MM-DD_articles.json with metadata."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_articles.json"
    filepath = DATA_RAW_DIR / filename

    payload = {
        "metadata": {
            "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_articles": len(articles),
            "sources_queried": len(RSS_FEEDS),
            "pipeline_version": "v1.0.0"
        },
        "articles": articles
    }

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 Saved raw data to {filepath}")
    except IOError as e:
        logger.error(f"💥 Failed to write raw JSON: {e}")
        raise

    return filepath

# ============================================================================
# MAIN PIPELINE
# ============================================================================

def run_fetch_pipeline() -> Optional[Path]:
    """Execute the complete RSS aggregation pipeline."""
    logger.info("🚀 === Starting RSS Fetch Pipeline ===")
    
    all_articles = []
    for feed in RSS_FEEDS:
        articles = fetch_single_feed(feed)
        all_articles.extend(articles)
        # Polite delay between feeds to respect source rate limits
        time.sleep(1.5)

    if not all_articles:
        logger.warning("⚠️ No articles fetched. Network or feed availability issue?")
        return None

    unique_articles = deduplicate_articles(all_articles)
    
    if not unique_articles:
        logger.error("❌ All fetched articles were duplicates or malformed.")
        return None

    output_path = save_raw_output(unique_articles)
    logger.info("🎉 === RSS Fetch Pipeline Completed Successfully ===")
    return output_path

if __name__ == "__main__":
    try:
        result_path = run_fetch_pipeline()
        sys.exit(0 if result_path else 1)
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"💥 Pipeline failed critically: {e}", exc_info=True)
        sys.exit(2)