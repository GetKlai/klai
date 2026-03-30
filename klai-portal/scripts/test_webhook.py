#!/usr/bin/env python3
"""
Send a test Vexa webhook to the portal-api.

Usage:
  python scripts/test_webhook.py --status completed --meeting-id <vexa_int_id> --platform google_meet --native-id <abc-def-ghi>
  python scripts/test_webhook.py --status active   --platform google_meet --native-id <abc-def-ghi>
  python scripts/test_webhook.py --list   # list recent meetings from the DB

Requires: requests, psycopg2 or psycopg (for --list)
  pip install requests
"""
import argparse
import os
import sys

import requests

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://getklai.getklai.com/api/bots/internal/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "eea93826eed5f725daa3d27383f090d116c6f6e3bd22b2590f4ef26eb0a562ea")

HEADERS = {"Authorization": f"Bearer {WEBHOOK_SECRET}", "Content-Type": "application/json"}


def send_status(platform: str, native_id: str, status: str, vexa_meeting_id: int | None = None) -> None:
    """Send a status-update webhook (send_status_webhook format)."""
    payload = {
        "event_type": "meeting.status_changed",
        "meeting": {
            "id": vexa_meeting_id,
            "platform": platform,
            "native_meeting_id": native_id,
            "status": status,
        },
    }
    r = requests.post(WEBHOOK_URL, json=payload, headers=HEADERS, timeout=30)
    print(f"→ {r.status_code}: {r.text}")


def send_completed(platform: str, native_id: str, vexa_meeting_id: int) -> None:
    """Send a completion webhook (send_webhook format)."""
    payload = {
        "id": vexa_meeting_id,
        "platform": platform,
        "native_meeting_id": native_id,
        "status": "completed",
        "speaker_events": [],
    }
    r = requests.post(WEBHOOK_URL, json=payload, headers=HEADERS, timeout=60)
    print(f"→ {r.status_code}: {r.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Vexa webhook")
    parser.add_argument("--platform", default="google_meet")
    parser.add_argument("--native-id", help="e.g. abc-def-ghi")
    parser.add_argument("--meeting-id", type=int, help="Vexa internal int meeting ID (needed for --status completed)")
    parser.add_argument("--status", choices=["joining", "active", "recording", "completed"], default="active")
    args = parser.parse_args()

    if not args.native_id:
        parser.error("--native-id is required")

    print(f"Sending webhook: platform={args.platform} native_id={args.native_id} status={args.status} vexa_id={args.meeting_id}")

    if args.status == "completed":
        if not args.meeting_id:
            parser.error("--meeting-id is required for --status completed")
        send_completed(args.platform, args.native_id, args.meeting_id)
    else:
        send_status(args.platform, args.native_id, args.status, args.meeting_id)


if __name__ == "__main__":
    main()
