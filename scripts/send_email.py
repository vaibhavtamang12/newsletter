#!/usr/bin/env python3
"""
send_email.py - Newsletter Delivery Module via Gmail SMTP

Reads validated subscribers, constructs MIME multipart emails (HTML + plain text fallback),
connects securely to Gmail SMTP using App Password, and delivers with retry logic,
rate limiting, and comprehensive delivery logging.
"""

import os
import re
import json
import logging
import sys
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone
from dotenv import load_dotenv

import supabase_client

# ============================================================================
# ENVIRONMENT & PATH CONFIGURATION
# ============================================================================
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NEWSLETTER_DIR = PROJECT_ROOT / "newsletters"
SUBSCRIBERS_FILE = PROJECT_ROOT / "subscribers.txt"
LOG_DIR = PROJECT_ROOT / "logs"
REPORT_DIR = PROJECT_ROOT / "logs" / "delivery_reports"

LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# SMTP & Sender Config
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SENDER_NAME = os.getenv("NEWSLETTER_SENDER_NAME", "Finance Digest Bot")
SENDER_EMAIL = os.getenv("NEWSLETTER_SENDER_EMAIL") or GMAIL_USER
SUBJECT_TEMPLATE = os.getenv("NEWSLETTER_SUBJECT", "[Daily] Finance Market Digest - {date}")

# Validate critical credentials upfront
if not all([GMAIL_USER, GMAIL_APP_PASSWORD, SENDER_EMAIL]):
    raise EnvironmentError(
        "❌ Missing required Gmail SMTP credentials in .env. "
        "Ensure GMAIL_USER, GMAIL_APP_PASSWORD, and SENDER_EMAIL are set."
    )

# Basic RFC 5322 email validation pattern
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# ============================================================================
# LOGGING SETUP
# ============================================================================
def setup_logging() -> logging.Logger:
    """Configure logger for delivery pipeline."""
    logger = logging.getLogger("send_email")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    fh = logging.FileHandler(LOG_DIR / "send_email.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger

logger = setup_logging()

# ============================================================================
# SUBSCRIBER & NEWSLETTER LOADING
# ============================================================================
def load_subscribers() -> List[str]:
    """
    Load validated subscriber emails.

    Primary source: Supabase `subscribers` table (status='active').
    Fallback: local subscribers.txt (useful for local dev/testing or if
    Supabase is temporarily unreachable/unconfigured).
    """
    if supabase_client.is_configured():
        emails = supabase_client.get_active_subscribers()
        if emails is not None:
            valid_list = sorted({e for e in emails if EMAIL_REGEX.match(e)})
            logger.info(f"👥 Loaded {len(valid_list)} validated subscribers from Supabase")
            return valid_list
        logger.warning("⚠️ Falling back to subscribers.txt (Supabase fetch failed).")
    else:
        logger.info("ℹ️ Supabase not configured — using subscribers.txt.")

    if not SUBSCRIBERS_FILE.exists():
        logger.warning("📂 subscribers.txt not found. No delivery targets.")
        return []

    emails = set()
    with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            clean = line.strip().lower()
            # Skip comments, empty lines, and malformed addresses
            if not clean or clean.startswith("#") or not EMAIL_REGEX.match(clean):
                continue
            emails.add(clean)

    valid_list = sorted(emails)
    logger.info(f"👥 Loaded {len(valid_list)} validated subscribers from subscribers.txt")
    return valid_list

def find_latest_newsletter() -> Tuple[Path, Path]:
    """Locate the most recently generated newsletter HTML & Markdown files.
    
    Prioritizes today's UTC date directory to ensure the freshly generated
    newsletter is always sent, not a stale one from a previous run.
    """
    date_dirs = sorted([d for d in NEWSLETTER_DIR.iterdir() if d.is_dir() and d.name.replace("-", "").isdigit()])
    
    if not date_dirs:
        raise FileNotFoundError("❌ No date-stamped newsletter directories found in newsletters/")

    # Prefer today's directory so we always send the freshly generated newsletter
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_dir = NEWSLETTER_DIR / today_str
    target_dir = today_dir if today_dir.exists() else date_dirs[-1]

    html_path = target_dir / "newsletter.html"
    md_path = target_dir / "newsletter.md"

    if not html_path.exists() or not md_path.exists():
        raise FileNotFoundError(f"❌ Missing newsletter files in {target_dir}")

    logger.info(f"📄 Using newsletter from: {target_dir.name}")
    return html_path, md_path

# ============================================================================
# MIME CONSTRUCTION & SMTP LOGIC
# ============================================================================
def build_mime_message(to_email: str, subject: str, html_body: str, text_body: str) -> MIMEMultipart:
    """Construct a standards-compliant MIME multipart/alternative email."""
    msg = MIMEMultipart("alternative")
    
    # Standard headers
    msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="finance-newsletter.local")
    
    # Attach plain text first (fallback), then HTML (preferred)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    
    return msg

def send_single_with_retry(server: smtplib.SMTP, msg: MIMEMultipart, to_email: str, max_retries: int = 3) -> bool:
    """
    Send email with exponential backoff retry on transient SMTP errors.
    WHY separate connection & send? SMTP handshake is expensive. Reusing one
    authenticated connection for batch delivery is faster and safer.
    """
    for attempt in range(1, max_retries + 1):
        try:
            server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
            return True
        except smtplib.SMTPResponseException as e:
            logger.warning(f"📡 SMTP error to {to_email} (try {attempt}): {e.smtp_code} {e.smtp_error.decode()}")
            # Permanent errors (5xx) usually won't succeed on retry
            if e.smtp_code >= 500:
                break
        except (smtplib.SMTPException, ConnectionError, OSError) as e:
            logger.warning(f"🌐 Network/SMTP error to {to_email} (try {attempt}): {e}")
        
        if attempt < max_retries:
            wait = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
            logger.info(f"⏳ Waiting {wait}s before retry...")
            time.sleep(wait)
            
    return False

# ============================================================================
# MAIN DELIVERY PIPELINE
# ============================================================================
def run_delivery_pipeline() -> bool:
    """Execute secure batch delivery to all validated subscribers."""
    logger.info("🚀 === Starting Newsletter Delivery Pipeline ===")
    
    subscribers = load_subscribers()
    if not subscribers:
        logger.warning("⚠️ No subscribers to deliver to. Exiting gracefully.")
        return True

    try:
        html_path, md_path = find_latest_newsletter()
        html_content = html_path.read_text(encoding="utf-8")
        text_content = md_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        logger.critical(f"💥 {e}")
        return False

    # Format subject dynamically
    newsletter_date = md_path.parent.name
    # Reformat date from YYYY-MM-DD to "YYYY Month DD" (e.g., "2026 June 26")
    try:
        parsed_date = datetime.strptime(newsletter_date, "%Y-%m-%d")
        formatted_date = parsed_date.strftime("%Y %B %d").replace(" 0", " ")
    except ValueError:
        formatted_date = newsletter_date
    subject = SUBJECT_TEMPLATE.format(date=formatted_date)
    
    delivery_log: List[Dict[str, str]] = []
    stats = {"total": len(subscribers), "sent": 0, "failed": 0}

    # Establish single authenticated SMTP connection
    try:
        logger.info(f"🔌 Connecting to {SMTP_HOST}:{SMTP_PORT} via STARTTLS...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            logger.info("✅ Authenticated successfully with Gmail")

            for i, email in enumerate(subscribers, 1):
                logger.info(f"📤 Delivering {i}/{stats['total']} to {email}")
                
                msg = build_mime_message(email, subject, html_content, text_content)
                success = send_single_with_retry(server, msg, email)
                
                status = "success" if success else "failed"
                delivery_log.append({"email": email, "status": status, "subject": subject})
                
                if success:
                    stats["sent"] += 1
                else:
                    stats["failed"] += 1

                # Polite delay to respect Gmail rate limits & avoid spam triggers
                if i < len(subscribers):
                    time.sleep(0.8)

    except smtplib.SMTPAuthenticationError:
        logger.critical(
            "🔐 Gmail SMTP Authentication failed. To fix:\n"
            "  1. Ensure 2-Step Verification is ON for the Gmail account:\n"
            "     https://myaccount.google.com/security\n"
            "  2. Generate a new App Password (select 'Mail' + 'Other'):\n"
            "     https://myaccount.google.com/apppasswords\n"
            "  3. Update GMAIL_APP_PASSWORD in your .env / GitHub Secret with the\n"
            "     16-character app password (no spaces).\n"
            "  4. Confirm GMAIL_USER matches the account that owns the App Password."
        )
        return False
    except Exception as e:
        logger.critical(f"💥 SMTP connection or delivery failed critically: {e}", exc_info=True)
        return False

    # Save delivery report for auditability
    report_path = REPORT_DIR / f"{newsletter_date}_delivery_report.json"
    report_payload = {
        "metadata": {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "newsletter_date": newsletter_date,
            "smtp_server": SMTP_HOST,
            "pipeline_version": "v1.0.0"
        },
        "stats": stats,
        "delivery_log": delivery_log
    }
    
    try:
        report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        logger.info(f"📊 Delivery report saved to {report_path}")
    except IOError as e:
        logger.error(f"💥 Failed to write delivery report: {e}")

    logger.info(f"📦 Delivery Complete: {stats['sent']} sent, {stats['failed']} failed out of {stats['total']}")
    return stats["failed"] == 0

if __name__ == "__main__":
    try:
        success = run_delivery_pipeline()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"💥 Pipeline execution failed: {e}", exc_info=True)
        sys.exit(2)