#!/usr/bin/env python3
"""
summarize_news.py - AI Summarization Module using Groq (free tier)

Reads cleaned news articles, generates structured financial insights
(concise summary, why it matters, market impact) via Groq's fast inference,
and saves AI-enriched JSON ready for newsletter generation.
Handles rate limits, API failures, and malformed responses gracefully.
Uses concurrent processing to speed up batch summarization.
"""

import os
import json
import logging
import sys
import time
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from groq import Groq, RateLimitError, APIStatusError

# ============================================================================
# ENVIRONMENT & PATH CONFIGURATION
# ============================================================================
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SUMMARIES_DIR = PROJECT_ROOT / "data" / "summaries"
LOG_DIR = PROJECT_ROOT / "logs"

SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_CONCURRENT = int(os.getenv("SUMMARIZE_CONCURRENCY", "5"))

if not os.getenv("GROQ_API_KEY"):
    raise EnvironmentError("❌ GROQ_API_KEY not set in environment variables.")

client = Groq()  # reads GROQ_API_KEY automatically

# ============================================================================
# LOGGING SETUP
# ============================================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("summarize_news")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    fh = logging.FileHandler(LOG_DIR / "summarize_news.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger

logger = setup_logging()

# ============================================================================
# PROMPT & API WRAPPER
# ============================================================================
SUMMARY_PROMPT_TEMPLATE = """You are a senior quantitative financial analyst. Analyze the following news article and return a STRICTLY VALID JSON object with exactly these three keys:

1. "concise_summary": A factual 2-3 sentence summary of the core event. No fluff.
2. "why_it_matters": Explain the direct significance for investors, policymakers, or the broader economy.
3. "market_impact": Assess the likely short-term market reaction. Format: "Bullish/Bearish/Neutral | [Sector/Asset] | Brief reasoning"

Rules:
- Maintain objective, professional tone. Avoid speculation or sensationalism.
- Keep responses concise. Total output should be under 250 words.
- Output ONLY valid JSON. Do not wrap in markdown or add explanations.

Article Title: {title}
Article Source: {source}
Article Context: {summary_text}"""


def call_groq_with_retry(prompt: str, max_retries: int = 4, base_delay: float = 2.0) -> Dict:
    """Call Groq with exponential backoff. Returns parsed JSON or fallback dict."""
    attempt = 0
    delay = base_delay
    text = ""

    while attempt <= max_retries:
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content.strip()
            return json.loads(text)

        except RateLimitError as e:
            # Groq returns a Retry-After hint — parse it if available
            retry_after = getattr(e, "response", None)
            wait = delay
            try:
                wait = float(e.response.headers.get("retry-after", delay))
            except Exception:
                pass
            logger.warning(f"⏳ Rate limited. Retrying in {wait:.1f}s... (Attempt {attempt + 1})")
            time.sleep(wait)
            delay *= 2
            attempt += 1
            continue

        except APIStatusError as e:
            if e.status_code in (500, 503):
                logger.warning(f"🌐 Transient error {e.status_code}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                delay *= 2
                attempt += 1
                continue
            logger.error(f"❌ Groq API error {e.status_code}: {e.message}")
            break

        except json.JSONDecodeError:
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            logger.error("💥 Invalid JSON from Groq.")
            break

        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}")
            break

    return {
        "concise_summary": "AI summary unavailable due to processing error.",
        "why_it_matters": "Unable to assess market significance at this time.",
        "market_impact": "Neutral | Market data pending",
        "ai_status": "failed",
    }


# ============================================================================
# CORE SUMMARIZATION LOGIC
# ============================================================================
def summarize_article(article: Dict) -> Dict:
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        title=article.get("title", ""),
        source=article.get("source", "Unknown"),
        summary_text=article.get("summary", "")[:1200],
    )
    insights = call_groq_with_retry(prompt)
    enriched = article.copy()
    enriched.update(insights)
    enriched["ai_model"] = GROQ_MODEL
    enriched["ai_generated_at"] = datetime.now(timezone.utc).isoformat()
    enriched.pop("summary", None)
    return enriched


def summarize_article_indexed(args: Tuple[int, Dict, int]) -> Tuple[int, Dict]:
    idx, article, total = args
    logger.info(f"🤖 Summarizing {idx}/{total}: {article.get('title', '')[:50]}...")
    return idx, summarize_article(article)


def load_latest_cleaned() -> Optional[Tuple[List[Dict], Path]]:
    cleaned_files = sorted(PROCESSED_DIR.glob("*_cleaned.json"))
    if not cleaned_files:
        logger.error("❌ No cleaned data found in data/processed/")
        return None
    target_file = cleaned_files[-1]
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("articles", []), target_file
    except json.JSONDecodeError as e:
        logger.error(f"💥 Corrupted cleaned JSON: {e}")
        return None


def save_summarized(payload: Dict, source_path: Path) -> Path:
    date_str = source_path.stem.split("_")[0]
    output_path = SUMMARIES_DIR / f"{date_str}_summarized.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info(f"💾 Summarized data saved to {output_path}")
    return output_path


# ============================================================================
# MAIN PIPELINE
# ============================================================================
def run_summarization_pipeline() -> Optional[Path]:
    logger.info("🚀 === Starting AI Summarization Pipeline ===")
    logger.info(f"🔧 Model: {GROQ_MODEL} | Concurrency: {MAX_CONCURRENT}")

    load_result = load_latest_cleaned()
    if not load_result:
        return None

    articles, source_path = load_result
    if not articles:
        logger.warning("⚠️ No cleaned articles to summarize.")
        return None

    articles_to_process = articles[:15]
    logger.info(f"📝 Processing top {len(articles_to_process)} articles")

    enriched_articles = [None] * len(articles_to_process)
    success_count = 0
    total = len(articles_to_process)
    tasks = [(i + 1, art, total) for i, art in enumerate(articles_to_process)]

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        futures = {executor.submit(summarize_article_indexed, t): t[0] - 1 for t in tasks}
        for future in as_completed(futures):
            orig_idx = futures[future]
            try:
                _, enriched = future.result()
                enriched_articles[orig_idx] = enriched
                if enriched.get("ai_status") != "failed":
                    success_count += 1
            except Exception as e:
                logger.error(f"❌ Worker failed for article {orig_idx + 1}: {e}")
                enriched_articles[orig_idx] = articles_to_process[orig_idx]

    enriched_articles = [a for a in enriched_articles if a is not None]
    logger.info(f"✅ Done: {success_count}/{len(articles_to_process)} successful")

    payload = {
        "metadata": {
            "summarization_timestamp": datetime.now(timezone.utc).isoformat(),
            "model_used": GROQ_MODEL,
            "total_processed": len(articles_to_process),
            "successful_summaries": success_count,
            "pipeline_version": "v2.1.0",
            "source_file": source_path.name,
            "provider": "groq",
        },
        "articles": enriched_articles,
    }

    return save_summarized(payload, source_path)


if __name__ == "__main__":
    try:
        result_path = run_summarization_pipeline()
        sys.exit(0 if result_path else 1)
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"💥 Pipeline failed: {e}", exc_info=True)
        sys.exit(2)