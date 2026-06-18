#!/usr/bin/env python3
"""
fetch_market_data.py - Live Market Data Fetcher

Pulls real-time index, sector, and style-factor performance data via yfinance
(no API key required) and writes data/market/market_data.json.

Run this BEFORE generate_newsletter.py so the HTML template receives live figures
instead of hardcoded placeholders.

Output schema (matches generate_newsletter.py context expectations):
{
  "fetched_at": "...",
  "signal_posture": "Risk-On | Risk-Off | Neutral | De-Risking",
  "sp500_wtd": "+1.23%",
  "leading_factor_name": "Low Volatility",
  "leading_factor_value": "+1.74%",
  "top_sector_name": "Energy",
  "top_sector_value": "+2.45%",
  "hero_title": "...",          # auto-generated narrative line
  "executive_summary": "...",   # optional; leave empty to omit section
  "key_themes": [...],
  "global_markets": [
    {"name": "...", "wtd": "...", "mtd": "...", "qtd": "...", "ytd": "...", "y2025": "...", "y2024": "..."}
  ],
  "global_market_insights": [...],
  "style_factors": [...],
  "sector_performance": [...]
}
"""

import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance is not installed. Run: pip install yfinance")
    sys.exit(1)

# ============================================================================
# PATHS
# ============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_DATA_DIR = PROJECT_ROOT / "data" / "market"
LOG_DIR = PROJECT_ROOT / "logs"
MARKET_DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# LOGGING
# ============================================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("fetch_market_data")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    fh = logging.FileHandler(LOG_DIR / "fetch_market_data.log")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger

logger = setup_logging()

# ============================================================================
# TICKER REGISTRIES
# ============================================================================

# Global market indices — Yahoo Finance tickers
GLOBAL_INDICES: List[Dict] = [
    {"name": "S&P 500 (US)",              "ticker": "^GSPC"},
    {"name": "NASDAQ 100 (US)",           "ticker": "^NDX"},
    {"name": "Dow Jones (US)",            "ticker": "^DJI"},
    {"name": "S&P/TSX Composite (CAD)",   "ticker": "^GSPTSE"},
    {"name": "FTSE 100 (UK)",             "ticker": "^FTSE"},
    {"name": "CAC 40 (France)",           "ticker": "^FCHI"},
    {"name": "DAX 40 (Germany)",          "ticker": "^GDAXI"},
    {"name": "STOXX Europe 600 (EUR)",    "ticker": "^STOXX"},
    {"name": "Nikkei 225 (Japan)",        "ticker": "^N225"},
    {"name": "Hang Seng (HK)",            "ticker": "^HSI"},
    {"name": "MSCI World (Developed)",    "ticker": "URTH"},   # ETF proxy
    {"name": "MSCI ACWI",                 "ticker": "ACWI"},   # ETF proxy
    {"name": "MSCI EM (Emerging)",        "ticker": "EEM"},    # ETF proxy
]

# US S&P 500 sector ETFs (SPDR XL series)
SECTORS: List[Dict] = [
    {"name": "Info Tech",             "ticker": "XLK"},
    {"name": "Healthcare",            "ticker": "XLV"},
    {"name": "Financials",            "ticker": "XLF"},
    {"name": "Consumer Discretionary","ticker": "XLY"},
    {"name": "Consumer Staples",      "ticker": "XLP"},
    {"name": "Energy",                "ticker": "XLE"},
    {"name": "Industrials",           "ticker": "XLI"},
    {"name": "Utilities",             "ticker": "XLU"},
    {"name": "Real Estate",           "ticker": "XLRE"},
    {"name": "Materials",             "ticker": "XLB"},
    {"name": "Communication Services","ticker": "XLC"},
]

# US Style Factor ETFs (iShares / Invesco proxies)
STYLE_FACTORS: List[Dict] = [
    {"name": "Low Volatility",        "ticker": "USMV"},   # iShares MSCI USA Min Vol
    {"name": "Low Vol High Div",      "ticker": "SPHD"},   # Invesco S&P500 High Div Low Vol
    {"name": "Quality",               "ticker": "QUAL"},   # iShares MSCI USA Quality
    {"name": "Value",                 "ticker": "IVE"},    # iShares S&P500 Value
    {"name": "Pure Value",            "ticker": "RPV"},    # Invesco S&P500 Pure Value
    {"name": "Equal Weight",          "ticker": "RSP"},    # Invesco S&P500 Equal Weight
    {"name": "Momentum",              "ticker": "MTUM"},   # iShares MSCI USA Momentum
    {"name": "Growth",                "ticker": "IVW"},    # iShares S&P500 Growth
    {"name": "High Beta",             "ticker": "SPHB"},   # Invesco S&P500 High Beta
]

# ============================================================================
# DATE HELPERS
# ============================================================================

def _week_start(dt: datetime) -> datetime:
    """Return the Monday of dt's ISO week."""
    return dt - timedelta(days=dt.weekday())

def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1)

def _quarter_start(dt: datetime) -> datetime:
    q_month = ((dt.month - 1) // 3) * 3 + 1
    return dt.replace(month=q_month, day=1)

def _year_start(dt: datetime, year: Optional[int] = None) -> datetime:
    y = year or dt.year
    return datetime(y, 1, 1, tzinfo=dt.tzinfo)

# ============================================================================
# PERFORMANCE CALCULATION
# ============================================================================

def _pct(end: float, start: float) -> str:
    """Return a formatted percentage string like '+1.23%' or '-0.45%'."""
    if start == 0:
        return "N/A"
    val = (end - start) / start * 100
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"

def fetch_performance(ticker: str, name: str) -> Optional[Dict]:
    """
    Fetch WTD / MTD / QTD / YTD / Y2025 / Y2024 for a single ticker.
    Returns None on failure.
    """
    try:
        t = yf.Ticker(ticker)
        # Pull 3 years of daily history to cover all periods
        hist = t.history(period="3y", auto_adjust=True)
        if hist.empty:
            logger.warning(f"⚠️  No history for {ticker} ({name})")
            return None

        hist.index = hist.index.tz_localize(None) if hist.index.tzinfo is None else hist.index.tz_convert(None)
        hist = hist[["Close"]].dropna()

        now = hist.index[-1].to_pydatetime()
        latest = float(hist["Close"].iloc[-1])

        def price_on_or_before(target: datetime) -> Optional[float]:
            subset = hist[hist.index <= target]
            if subset.empty:
                return None
            return float(subset["Close"].iloc[-1])

        week_ago   = _week_start(now) - timedelta(days=1)   # last Fri close
        month_ago  = _month_start(now) - timedelta(days=1)
        quarter_ago= _quarter_start(now) - timedelta(days=1)
        ytd_start  = _year_start(now) - timedelta(days=1)
        y2025_end  = datetime(2025, 12, 31)
        y2025_start= datetime(2024, 12, 31)
        y2024_end  = datetime(2024, 12, 31)
        y2024_start= datetime(2023, 12, 31)

        p_week    = price_on_or_before(week_ago)
        p_month   = price_on_or_before(month_ago)
        p_quarter = price_on_or_before(quarter_ago)
        p_ytd     = price_on_or_before(ytd_start)
        p_2025e   = price_on_or_before(y2025_end)
        p_2025s   = price_on_or_before(y2025_start)
        p_2024e   = price_on_or_before(y2024_end)
        p_2024s   = price_on_or_before(y2024_start)

        return {
            "name":  name,
            "wtd":   _pct(latest, p_week)    if p_week    else "N/A",
            "mtd":   _pct(latest, p_month)   if p_month   else "N/A",
            "qtd":   _pct(latest, p_quarter) if p_quarter else "N/A",
            "ytd":   _pct(latest, p_ytd)     if p_ytd     else "N/A",
            "y2025": _pct(p_2025e, p_2025s)  if (p_2025e and p_2025s) else "N/A",
            "y2024": _pct(p_2024e, p_2024s)  if (p_2024e and p_2024s) else "N/A",
            "latest_close": round(latest, 2),
        }
    except Exception as e:
        logger.error(f"❌ Failed to fetch {ticker} ({name}): {e}")
        return None

# ============================================================================
# MARKET INTELLIGENCE HELPERS
# ============================================================================

def _parse_pct(s: str) -> float:
    """Parse '+1.23%' → 1.23, '-0.45%' → -0.45."""
    try:
        return float(s.replace("+", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return 0.0

def derive_signal_posture(sp500_wtd: str, sector_rows: List[Dict], factor_rows: List[Dict]) -> str:
    """
    Heuristic: classify weekly market posture from S&P 500 WTD + factor/sector signals.
    """
    sp_val = _parse_pct(sp500_wtd)

    if not factor_rows:
        return "Risk-On" if sp_val > 0 else ("Risk-Off" if sp_val < -1.5 else "Neutral")

    low_vol = next((r for r in factor_rows if "low vol" in r["name"].lower()), None)
    high_beta = next((r for r in factor_rows if "high beta" in r["name"].lower()), None)
    low_vol_wtd  = _parse_pct(low_vol["wtd"])  if low_vol  else 0
    high_beta_wtd= _parse_pct(high_beta["wtd"]) if high_beta else 0

    if sp_val > 0.5 and high_beta_wtd > 0:
        return "Risk-On"
    if sp_val < -1.0 and low_vol_wtd > high_beta_wtd:
        return "De-Risking"
    if sp_val < -2.0:
        return "Risk-Off"
    if sp_val < 0 and low_vol_wtd > 0:
        return "Defensive Rotation"
    return "Neutral"

def pick_leading_lagging(rows: List[Dict]) -> Tuple[Dict, Dict]:
    """Return (best_row, worst_row) sorted by WTD."""
    valid = [r for r in rows if r.get("wtd") not in ("N/A", None)]
    sorted_rows = sorted(valid, key=lambda r: _parse_pct(r["wtd"]), reverse=True)
    return sorted_rows[0] if sorted_rows else {}, sorted_rows[-1] if sorted_rows else {}

def build_key_themes(posture: str, sector_rows: List[Dict], factor_rows: List[Dict]) -> List[str]:
    themes = []
    if "risk-off" in posture.lower() or "de-risk" in posture.lower():
        themes.append("Flight to Quality")
        themes.append("Defensive Rotation")
    elif "risk-on" in posture.lower():
        themes.append("Growth Leadership")
        themes.append("Risk-On Sentiment")

    if sector_rows:
        top_sec, _ = pick_leading_lagging(sector_rows)
        bot_sec_name = sorted(sector_rows, key=lambda r: _parse_pct(r.get("wtd","0")))[0].get("name","")
        if top_sec.get("name"):
            themes.append(f"{top_sec['name']} Outperformance")
        if bot_sec_name:
            themes.append(f"{bot_sec_name} Weakness")

    if factor_rows:
        top_f, bot_f = pick_leading_lagging(factor_rows)
        if top_f.get("name"):
            themes.append(f"{top_f['name']} Factor Leadership")

    return list(dict.fromkeys(themes))[:6]  # dedupe, cap at 6

def build_hero_title(posture: str, top_sector: str, leading_factor: str) -> str:
    lines = []
    if "risk-off" in posture.lower() or "de-risk" in posture.lower():
        lines.append("Global De-Risking Event")
    elif "risk-on" in posture.lower():
        lines.append("Broad Market Rally")
    elif "defensive" in posture.lower():
        lines.append("Defensive Rotation in Focus")
    else:
        lines.append("Mixed Market Signals")

    if top_sector:
        lines.append(f"{top_sector} Leadership")
    if leading_factor:
        lines.append(f"{leading_factor} Factor Dynamics")
    return ", ".join(lines)

def build_global_insights(global_rows: List[Dict]) -> List[str]:
    if not global_rows:
        return []
    valid = [r for r in global_rows if r.get("wtd") not in ("N/A",)]
    if not valid:
        return []

    insights = []
    top_r = max(valid, key=lambda r: _parse_pct(r["wtd"]))
    bot_r = min(valid, key=lambda r: _parse_pct(r["wtd"]))

    top_val = _parse_pct(top_r["wtd"])
    bot_val = _parse_pct(bot_r["wtd"])

    if abs(top_val) > 0.1:
        sign = "outperformed" if top_val > 0 else "was the least impacted"
        insights.append(
            f"{top_r['name']} {sign} global peers with a WTD return of {top_r['wtd']}."
        )
    if abs(bot_val) > 0.5 and bot_r["name"] != top_r["name"]:
        insights.append(
            f"{bot_r['name']} saw the steepest weekly decline at {bot_r['wtd']}."
        )

    spread = top_val - bot_val
    if spread > 3:
        insights.append(
            f"Significant regional divergence this week: a {spread:.1f}pp spread between the best and worst performers."
        )

    return insights

# ============================================================================
# MAIN PIPELINE
# ============================================================================

def run_fetch_pipeline() -> Dict:
    logger.info("🚀 === Starting Market Data Fetch ===")
    now_utc = datetime.now(timezone.utc)

    # --- Global Indices ---
    logger.info("📡 Fetching global indices...")
    global_rows = []
    for idx in GLOBAL_INDICES:
        row = fetch_performance(idx["ticker"], idx["name"])
        if row:
            global_rows.append(row)
            logger.info(f"   ✅ {idx['name']}: WTD={row['wtd']}")

    # --- Sector Performance ---
    logger.info("📡 Fetching sector ETFs...")
    sector_rows = []
    for s in SECTORS:
        row = fetch_performance(s["ticker"], s["name"])
        if row:
            sector_rows.append(row)
            logger.info(f"   ✅ {s['name']}: WTD={row['wtd']}")
    # Sort descending by WTD
    sector_rows.sort(key=lambda r: _parse_pct(r.get("wtd", "0")), reverse=True)

    # --- Style Factors ---
    logger.info("📡 Fetching style-factor ETFs...")
    factor_rows = []
    for f in STYLE_FACTORS:
        row = fetch_performance(f["ticker"], f["name"])
        if row:
            factor_rows.append(row)
            logger.info(f"   ✅ {f['name']}: WTD={row['wtd']}")
    factor_rows.sort(key=lambda r: _parse_pct(r.get("wtd", "0")), reverse=True)

    # --- Signal strip derivations ---
    sp500_row = next((r for r in global_rows if "S&P 500 (US)" in r["name"]), None)
    sp500_wtd = sp500_row["wtd"] if sp500_row else "N/A"

    posture = derive_signal_posture(sp500_wtd, sector_rows, factor_rows)
    top_sector, _ = pick_leading_lagging(sector_rows)
    top_factor, _ = pick_leading_lagging(factor_rows)

    top_sector_name  = top_sector.get("name", "")
    top_sector_value = top_sector.get("wtd", "N/A")
    leading_factor_name  = top_factor.get("name", "")
    leading_factor_value = top_factor.get("wtd", "N/A")

    themes  = build_key_themes(posture, sector_rows, factor_rows)
    insights= build_global_insights(global_rows)
    hero    = build_hero_title(posture, top_sector_name, leading_factor_name)

    payload = {
        "fetched_at": now_utc.isoformat(),
        # Signal strip
        "signal_posture":       posture,
        "sp500_wtd":            sp500_wtd,
        "leading_factor_name":  leading_factor_name,
        "leading_factor_value": leading_factor_value,
        "top_sector_name":      top_sector_name,
        "top_sector_value":     top_sector_value,
        # Hero / narrative
        "hero_title":           hero,
        # Template sections
        "executive_summary":    "",   # left empty — auto-filled by Groq in summarize step
        "key_themes":           themes,
        "global_markets":       global_rows,
        "global_market_insights": insights,
        "style_factors":        factor_rows,
        "sector_performance":   sector_rows,
    }

    out_path = MARKET_DATA_DIR / "market_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    logger.info(f"💾 Saved market data → {out_path}")
    logger.info(f"   Signal posture : {posture}")
    logger.info(f"   S&P 500 WTD    : {sp500_wtd}")
    logger.info(f"   Top sector     : {top_sector_name} ({top_sector_value})")
    logger.info(f"   Leading factor : {leading_factor_name} ({leading_factor_value})")
    logger.info("✅ === Market Data Fetch Complete ===")
    return payload


if __name__ == "__main__":
    try:
        run_fetch_pipeline()
        sys.exit(0)
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted.")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"💥 Market fetch failed: {e}", exc_info=True)
        sys.exit(2)
