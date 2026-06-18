#!/usr/bin/env python3
"""
migrate_subscribers.py - One-off migration: subscribers.txt -> Supabase

Reads the legacy subscribers.txt file and upserts each address into the
Supabase `subscribers` table with status='active'. Safe to re-run
(duplicates are merged on the unique `email` column).

Usage:
    python scripts/migrate_subscribers.py
"""

import re
import sys
from pathlib import Path

import supabase_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUBSCRIBERS_FILE = PROJECT_ROOT / "subscribers.txt"

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def main() -> int:
    if not supabase_client.is_configured():
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env. Aborting.")
        return 1

    if not SUBSCRIBERS_FILE.exists():
        print(f"❌ {SUBSCRIBERS_FILE} not found.")
        return 1

    emails = set()
    with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            clean = line.strip().lower()
            if not clean or clean.startswith("#") or not EMAIL_REGEX.match(clean):
                continue
            emails.add(clean)

    if not emails:
        print("ℹ️ No valid emails found in subscribers.txt.")
        return 0

    ok, failed = 0, 0
    for email in sorted(emails):
        if supabase_client.add_subscriber(email):
            print(f"✅ {email}")
            ok += 1
        else:
            print(f"❌ {email} (failed)")
            failed += 1

    print(f"\nDone: {ok} migrated, {failed} failed, {len(emails)} total.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
