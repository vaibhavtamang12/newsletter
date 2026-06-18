#!/usr/bin/env python3
"""
generate_newsletter.py - Newsletter Rendering Module (Jinja2)

Loads AI-summarized articles and optional market performance data,
renders Markdown & HTML templates, and archives outputs in date-stamped
directories for version control.

Template context variables:
  - newsletter_title (str)
  - date (str)
  - generated_at (str)
  - executive_summary (str)          # optional narrative paragraph
  - key_themes (list[str])           # optional theme pills
  - articles (list[dict])            # daily news articles
  - global_markets (list[dict])      # optional WTD/MTD/QTD/YTD rows
  - global_market_insights (list[str])
  - style_factors (list[dict])       # optional WTD/MTD/QTD/YTD rows
  - sector_performance (list[dict])  # optional WTD/MTD/QTD/YTD rows
"""

import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

# ============================================================================
# CONFIGURATION & PATHS
# ============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUMMARIES_DIR = PROJECT_ROOT / "data" / "summaries"
MARKET_DATA_DIR = PROJECT_ROOT / "data" / "market"   # optional: drop market_data.json here
NEWSLETTER_DIR = PROJECT_ROOT / "newsletters"
TEMPLATE_DIR = PROJECT_ROOT / "templates"
LOG_DIR = PROJECT_ROOT / "logs"

# Maximum number of news articles to feature in the "Top Stories" section.
# Articles are sorted by relevance_score (if present) before truncation.
MAX_ARTICLES = int(os.getenv("NEWSLETTER_MAX_ARTICLES", "8"))

NEWSLETTER_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# LOGGING SETUP
# ============================================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("generate_newsletter")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    fh = logging.FileHandler(LOG_DIR / "generate_newsletter.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger

logger = setup_logging()

# ============================================================================
# JINJA2 TEMPLATE ENGINE
# ============================================================================
def setup_jinja_env() -> Environment:
    """Configure Jinja2 environment with safe defaults and custom filters."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True
    )

    def format_iso_date(iso_str: str) -> str:
        if not iso_str:
            return "Unknown"
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%b %d, %Y %H:%M UTC")
        except ValueError:
            return iso_str[:16].replace("T", " ")

    def perf_class(value) -> str:
        """Return a CSS class ('pos' | 'neg' | 'neutral') based on a
        performance string like '+2.45%' or '-0.02%'."""
        s = str(value).strip()
        if s.startswith("-"):
            return "neg"
        if s.startswith("+"):
            return "pos"
        try:
            return "pos" if float(s.rstrip("%")) > 0 else "neg" if float(s.rstrip("%")) < 0 else "neutral"
        except (ValueError, TypeError):
            return "neutral"

    def impact_class(value) -> str:
        """Return a CSS class based on the leading sentiment word of a
        market_impact string, e.g. 'Bullish | Sector | reasoning'."""
        s = str(value).strip().lower()
        if s.startswith("bullish"):
            return "bullish"
        if s.startswith("bearish"):
            return "bearish"
        return "neutral-impact"

    env.filters['format_date'] = format_iso_date
    env.filters['perf_class'] = perf_class
    env.filters['impact_class'] = impact_class
    return env

jinja_env = setup_jinja_env()

# ============================================================================
# DATA LOADING
# ============================================================================
def load_latest_summarized() -> Optional[Tuple[List[Dict], Path]]:
    """Locate and load the most recent AI-summarized JSON payload."""
    summary_files = sorted(SUMMARIES_DIR.glob("*_summarized.json"))
    if not summary_files:
        logger.error("❌ No summarized data found in data/summaries/")
        return None

    target_file = summary_files[-1]
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("articles", []), target_file
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"💥 Failed to load summaries: {e}")
        return None


def load_market_data() -> Optional[Dict]:
    """
    Load optional structured market performance data.

    Expected file: data/market/market_data.json
    Expected schema:
    {
      "executive_summary": "...",
      "key_themes": ["theme1", "theme2"],
      "global_markets": [
        {"name": "S&P/TSX Composite (CAD)", "wtd": "-0.02%", "mtd": "-0.22%", "qtd": "5.67%", "ytd": "8.81%"}
      ],
      "global_market_insights": ["Insight 1...", "Insight 2..."],
      "style_factors": [
        {"name": "Low Volatility", "wtd": "+1.74%", "mtd": "3.03%", "qtd": "0.56%", "ytd": "4.13%"}
      ],
      "sector_performance": [
        {"name": "Energy", "wtd": "+2.45%", "mtd": "0.65%", "qtd": "-2.20%", "ytd": "27.16%"}
      ]
    }

    If the file doesn't exist, returns None and the template simply
    omits those sections (all market table blocks are wrapped in
    {% if ... %} guards).
    """
    MARKET_DATA_DIR.mkdir(parents=True, exist_ok=True)
    market_files = sorted(MARKET_DATA_DIR.glob("market_data*.json"))
    if not market_files:
        logger.info("ℹ️  No market data file found — skipping market tables.")
        return None

    target = market_files[-1]
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"📈 Loaded market data: {target.name}")
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"⚠️ Could not load market data: {e}")
        return None

# ============================================================================
# CONTEXT PREPARATION
# ============================================================================
def format_iso_date(iso_str: str) -> str:
    if not iso_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %H:%M UTC")
    except ValueError:
        return iso_str[:16].replace("T", " ")


def prepare_context(articles: List[Dict], market_data: Optional[Dict] = None) -> Dict:
    """Format all data into a Jinja2-friendly context dictionary."""
    # Surface the most relevant articles first, then cap the total shown
    # so the email stays digestible.
    sorted_articles = sorted(
        articles,
        key=lambda a: a.get("relevance_score", 0) or 0,
        reverse=True,
    )
    sorted_articles = sorted_articles[:MAX_ARTICLES]

    formatted_articles = []
    for art in sorted_articles:
        formatted_articles.append({
            "title": art.get("title", "Untitled"),
            "link": art.get("link", "#"),
            "source": art.get("source", "Unknown"),
            "published": format_iso_date(art.get("published_iso") or art.get("published")),
            "concise_summary": art.get("concise_summary", "Summary unavailable."),
            "why_it_matters": art.get("why_it_matters", ""),
            "market_impact": art.get("market_impact", ""),
        })

    now = datetime.now(timezone.utc)
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = now.astimezone(ist)
    context = {
        "newsletter_title": "Daily Finance Digest",
        "date": now_ist.strftime("%A, %B %d, %Y"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "generated_at_ist": now_ist.strftime("%Y-%m-%d %I:%M %p IST"),
        "articles": formatted_articles,
        # Market report fields — populated from market_data if available
        "executive_summary": None,
        "key_themes": [],
        "global_markets": [],
        "global_market_insights": [],
        "style_factors": [],
        "sector_performance": [],
        # Signal strip defaults (overridden by market_data when present)
        "signal_posture":       "Neutral",
        "sp500_wtd":            "N/A",
        "leading_factor_name":  "",
        "leading_factor_value": "",
        "top_sector_name":      "",
        "top_sector_value":     "",
        "hero_title":           "",
    }

    if market_data:
        context["executive_summary"] = market_data.get("executive_summary")
        context["key_themes"] = market_data.get("key_themes", [])
        context["global_markets"] = market_data.get("global_markets", [])
        context["global_market_insights"] = market_data.get("global_market_insights", [])
        context["style_factors"] = market_data.get("style_factors", [])
        context["sector_performance"] = market_data.get("sector_performance", [])
        # Signal strip — derived by fetch_market_data.py
        context["signal_posture"]       = market_data.get("signal_posture", "Neutral")
        context["sp500_wtd"]            = market_data.get("sp500_wtd", "N/A")
        context["leading_factor_name"]  = market_data.get("leading_factor_name", "")
        context["leading_factor_value"] = market_data.get("leading_factor_value", "")
        context["top_sector_name"]      = market_data.get("top_sector_name", "")
        context["top_sector_value"]     = market_data.get("top_sector_value", "")
        context["hero_title"]           = market_data.get("hero_title", "")

    return context

# ============================================================================
# RENDERING & ARCHIVAL
# ============================================================================
def render_template(template_name: str, context: Dict) -> str:
    try:
        template = jinja_env.get_template(template_name)
        return template.render(**context)
    except TemplateNotFound:
        logger.critical(f"❌ Template missing: {template_name}")
        raise
    except Exception as e:
        logger.error(f"💥 Template rendering failed: {e}")
        raise


def save_newsletter(content: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logger.info(f"💾 Saved: {output_path.name} ({len(content)} chars)")
    return output_path

# ============================================================================
# MAIN PIPELINE
# ============================================================================
def run_generation_pipeline() -> Optional[Tuple[Path, Path]]:
    """Execute the complete newsletter generation & archival pipeline."""
    logger.info("🚀 === Starting Newsletter Generation ===")

    load_result = load_latest_summarized()
    if not load_result:
        return None

    articles, source_path = load_result
    if not articles:
        logger.warning("⚠️ No summarized articles available for rendering.")

    market_data = load_market_data()
    context = prepare_context(articles, market_data)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_dir = NEWSLETTER_DIR / date_str

    html_content = render_template("newsletter.html", context)
    md_content = render_template("newsletter.md", context)

    html_path = save_newsletter(html_content, archive_dir / "newsletter.html")
    md_path = save_newsletter(md_content, archive_dir / "newsletter.md")

    logger.info("✅ Newsletter generation complete.")
    return html_path, md_path


if __name__ == "__main__":
    try:
        result = run_generation_pipeline()
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"💥 Generation pipeline failed: {e}", exc_info=True)
        sys.exit(2)