#!/usr/bin/env python3
"""
clean_news.py - News Cleaning & Topic Deduplication Module

Reads raw RSS fetch output, filters low-quality entries,
removes semantic duplicates, normalizes dates, and scores
relevance for downstream AI summarization.
"""

import json
import logging
import re
import sys
import string
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple

# ============================================================================
# CONFIGURATION & PATHS
# ============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
LOG_DIR = PROJECT_ROOT / "logs"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Quality & Filtering Thresholds
MIN_SUMMARY_LENGTH = 50
TOP_KEYWORDS_COUNT = 4
DUPLICATE_SIMILARITY_THRESHOLD = 0.55  # Jaccard similarity cutoff

# Source Priority Weights (Higher = More Trusted/Impactful)
SOURCE_WEIGHTS = {
    "Reuters Markets": 1.0,
    "CNBC Markets": 0.85,
    "Yahoo Finance": 0.7,
    "Economic Times": 0.6
}

# Clickbait/Noise Pattern Filters (Case-Insensitive)
CLICKBAIT_PATTERNS = re.compile(
    r"^(watch|live|podcast|video|opinion|analysis|exclusive|breaking|poll|quiz|advertisement)\s*:|\
^\d+\s+(things|stocks|ways|reasons)|^why.*\?|^\[.*\]|^update:\s", 
    re.IGNORECASE
)

# Common English & Finance Stopwords (for topic fingerprinting)
STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "with", 
    "by", "about", "as", "is", "are", "was", "were", "be", "been", "have", "has", 
    "had", "do", "does", "did", "will", "would", "could", "should", "may", "might", 
    "must", "can", "of", "up", "down", "out", "into", "through", "during", "before", 
    "after", "above", "below", "between", "against", "under", "over", "from", "said",
    "says", "report", "market", "price", "stock", "price", "trading", "today", "week",
    "year", "time", "new", "first", "last", "next", "one", "two", "many", "much"
}

# ============================================================================
# LOGGING SETUP
# ============================================================================
def setup_logging() -> logging.Logger:
    """Configure logger for cleaning pipeline."""
    logger = logging.getLogger("clean_news")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    fh = logging.FileHandler(LOG_DIR / "clean_news.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger

logger = setup_logging()

# ============================================================================
# TEXT PROCESSING UTILITIES
# ============================================================================
def tokenize(text: str) -> List[str]:
    """Lowercase, strip punctuation, and split into tokens."""
    clean = text.lower().translate(str.maketrans("", "", string.punctuation))
    return [w for w in clean.split() if w not in STOP_WORDS and len(w) > 2]

def extract_keywords(text: str, n: int = TOP_KEYWORDS_COUNT) -> List[str]:
    """Extract top N meaningful words for topic fingerprinting."""
    tokens = tokenize(text)
    freq = Counter(tokens)
    return [word for word, _ in freq.most_common(n)]

def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0

def parse_iso_date(date_str: str) -> Optional[str]:
    """
    Attempt to parse common RSS date formats into ISO 8601.
    Fallback to original string if parsing fails (keeps pipeline resilient).
    """
    if not date_str:
        return None
    
    formats = [
        "%a, %d %b %Y %H:%M:%S %Z",   # RFC 822 (common in RSS)
        "%Y-%m-%dT%H:%M:%S%z",        # ISO 8601 with timezone
        "%Y-%m-%dT%H:%M:%SZ",         # ISO 8601 UTC
        "%Y-%m-%d %H:%M:%S",          # Simple datetime
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return date_str  # Return raw if no match

# ============================================================================
# CORE CLEANING LOGIC
# ============================================================================
def filter_quality(articles: List[Dict]) -> List[Dict]:
    """Remove articles with insufficient content, missing dates, or clickbait titles."""
    valid = []
    for art in articles:
        # Check summary length
        if len(art.get("summary", "").strip()) < MIN_SUMMARY_LENGTH:
            logger.debug(f"🚫 Skipped (short summary): {art['title'][:50]}...")
            continue
            
        # Check title noise patterns
        if CLICKBAIT_PATTERNS.search(art["title"]):
            logger.debug(f"🚫 Skipped (clickbait pattern): {art['title'][:50]}...")
            continue
            
        # Normalize date — parse_iso_date returns raw string on failure, so
        # validate the result is actually parseable as ISO 8601
        parsed_date = parse_iso_date(art.get("published", ""))
        if not parsed_date:
            logger.debug(f"🚫 Skipped (missing date): {art['title'][:50]}...")
            continue
        try:
            datetime.fromisoformat(parsed_date.replace("Z", "+00:00"))
            art["published_iso"] = parsed_date
        except ValueError:
            logger.debug(f"🚫 Skipped (unparseable date '{parsed_date}'): {art['title'][:50]}...")
            continue

        valid.append(art)
        
    logger.info(f"🔍 Quality Filter: {len(articles)} raw -> {len(valid)} valid")
    return valid

def remove_topic_duplicates(articles: List[Dict]) -> List[Dict]:
    """
    Greedy topic deduplication using Jaccard similarity on keyword fingerprints.
    WHY greedy? It's O(N²) worst-case but extremely fast for N<500. 
    Sorting by quality first ensures the best article per topic cluster is kept.
    """
    if not articles:
        return []

    # Sort by summary length (proxy for quality/detail) descending
    articles.sort(key=lambda x: len(x["summary"]), reverse=True)
    
    unique_articles = []
    kept_fingerprints: List[Set[str]] = []

    for art in articles:
        # Create topic fingerprint from title + summary
        text = f"{art['title']} {art['summary']}"
        fingerprint = set(extract_keywords(text, n=6))
        
        is_duplicate = False
        for existing_fp in kept_fingerprints:
            sim = jaccard_similarity(fingerprint, existing_fp)
            if sim > DUPLICATE_SIMILARITY_THRESHOLD:
                is_duplicate = True
                logger.debug(f"🔄 Deduped (sim={sim:.2f}): {art['title'][:50]}...")
                break

        if not is_duplicate:
            kept_fingerprints.append(fingerprint)
            unique_articles.append(art)

    logger.info(f"🧹 Topic Deduplication: {len(articles)} -> {len(unique_articles)} unique topics")
    return unique_articles

def calculate_relevance_scores(articles: List[Dict]) -> List[Dict]:
    """
    Score articles for sorting priority.
    Formula: (Summary_Length_Score * 0.4) + (Source_Trust * 0.3) + (Recency_Score * 0.3)
    """
    scored = []
    now = datetime.now(timezone.utc)

    for art in articles:
        # Normalize summary length (cap at 1000 chars for scoring)
        length_score = min(len(art["summary"]) / 1000.0, 1.0)
        
        # Source weight
        src = art.get("source", "")
        source_weight = SOURCE_WEIGHTS.get(src, 0.5)
        
        # Recency (hours since publication)
        try:
            pub_time = datetime.fromisoformat(art["published_iso"])
            hours_old = max((now - pub_time).total_seconds() / 3600.0, 1.0)
            recency_score = 1.0 / (hours_old ** 0.5)  # Decays over time
        except Exception:
            recency_score = 0.2

        # Weighted composite
        relevance = (length_score * 0.4) + (source_weight * 0.3) + (recency_score * 0.3)
        
        art["relevance_score"] = round(relevance, 4)
        scored.append(art)

    # Sort descending by relevance
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    logger.info("📊 Relevance scoring & sorting applied.")
    return scored

def load_latest_raw() -> Optional[Tuple[Dict, Path]]:
    """Find and load today's raw articles JSON."""
    today = datetime.now().strftime("%Y-%m-%d")
    raw_file = RAW_DIR / f"{today}_articles.json"
    
    if not raw_file.exists():
        # Fallback: find newest raw file if run later in day
        raw_files = sorted(RAW_DIR.glob("*_articles.json"))
        if raw_files:
            raw_file = raw_files[-1]
            logger.warning(f"⚠️ Today's raw file missing. Using latest: {raw_file.name}")
        else:
            logger.error("❌ No raw RSS data found in data/raw/")
            return None

    try:
        with open(raw_file, "r", encoding="utf-8") as f:
            return json.load(f), raw_file
    except json.JSONDecodeError as e:
        logger.error(f"💥 Corrupted JSON in {raw_file}: {e}")
        return None

def save_processed(payload: Dict, original_path: Path) -> Path:
    """Save cleaned dataset to data/processed/ with standardized naming."""
    date_str = original_path.stem.split("_")[0]
    output_path = PROCESSED_DIR / f"{date_str}_cleaned.json"
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        
    logger.info(f"💾 Processed data saved to {output_path}")
    return output_path

# ============================================================================
# MAIN PIPELINE
# ============================================================================
def run_cleaning_pipeline() -> Optional[Path]:
    """Execute the complete news cleaning & filtering pipeline."""
    logger.info("🚀 === Starting News Cleaning Pipeline ===")
    
    load_result = load_latest_raw()
    if not load_result:
        return None
        
    raw_payload, raw_path = load_result
    articles = raw_payload.get("articles", [])
    
    if not articles:
        logger.warning("⚠️ Raw payload contains no articles to clean.")
        return None

    # Pipeline stages
    valid_articles = filter_quality(articles)
    unique_articles = remove_topic_duplicates(valid_articles)
    scored_articles = calculate_relevance_scores(unique_articles)
    
    # Prepare output payload
    cleaned_payload = {
        "metadata": {
            "cleaned_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_input": len(articles),
            "quality_filtered_out": len(articles) - len(valid_articles),
            "topic_duplicates_removed": len(valid_articles) - len(unique_articles),
            "final_article_count": len(scored_articles),
            "pipeline_version": "v1.0.0",
            "source_file": raw_path.name
        },
        "articles": scored_articles
    }
    
    return save_processed(cleaned_payload, raw_path)

if __name__ == "__main__":
    try:
        result_path = run_cleaning_pipeline()
        sys.exit(0 if result_path else 1)
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"💥 Cleaning pipeline failed: {e}", exc_info=True)
        sys.exit(2)