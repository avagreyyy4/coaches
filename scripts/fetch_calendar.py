#!/usr/bin/env python3
# Run: python fetch_calendar.py
#
# Pulls events from the "staff" Google Calendar (via its secret iCal URL)
# and upserts them into Supabase's `calendar_events` table. Recurring
# events are expanded into individual instances over a window around today
# before being stored, since that's what the site's calendar view needs.

import os
from datetime import datetime, timedelta, timezone

import requests
import icalendar
import recurring_ical_events
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

ICAL_URL = os.getenv("STAFF_CALENDAR_ICAL_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# How far back/forward from "today" to expand recurring events. Re-synced
# on every run, so this window just needs to cover how far coaches page
# around in the site's calendar.
WINDOW_PAST_DAYS = 60
WINDOW_FUTURE_DAYS = 180

missing = []
if not ICAL_URL: missing.append("STAFF_CALENDAR_ICAL_URL")
if not SUPABASE_URL: missing.append("SUPABASE_URL")
if not SUPABASE_SERVICE_ROLE_KEY: missing.append("SUPABASE_SERVICE_ROLE_KEY")
if missing:
    raise SystemExit(f"[fatal] Missing required env: {', '.join(missing)}")

_sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _to_utc_iso(value) -> tuple[str, bool]:
    """Returns (iso_string, all_day) for an icalendar DATE or DATETIME value."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(), False
    # date-only (all-day event)
    dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return dt.isoformat(), True


def fetch_events():
    resp = requests.get(ICAL_URL, timeout=30)
    resp.raise_for_status()
    cal = icalendar.Calendar.from_ical(resp.content)

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=WINDOW_PAST_DAYS)
    end = today + timedelta(days=WINDOW_FUTURE_DAYS)

    return recurring_ical_events.of(cal).between(start, end)


def upsert_events(events):
    records = []
    for ev in events:
        uid = str(ev.get("UID", "")).strip()
        if not uid:
            continue

        start_at, all_day = _to_utc_iso(ev["DTSTART"].dt)
        end_at = None
        if "DTEND" in ev:
            end_at, _ = _to_utc_iso(ev["DTEND"].dt)

        records.append({
            "uid": uid,
            "title": str(ev.get("SUMMARY", "")) or None,
            "description": str(ev.get("DESCRIPTION", "")) or None,
            "location": str(ev.get("LOCATION", "")) or None,
            "start_at": start_at,
            "end_at": end_at,
            "all_day": all_day,
        })

    if not records:
        return 0

    # Upsert in batches to keep request bodies reasonable.
    BATCH = 500
    for i in range(0, len(records), BATCH):
        _sb.table("calendar_events").upsert(
            records[i:i + BATCH], on_conflict="uid,start_at"
        ).execute()

    return len(records)


def main():
    events = fetch_events()
    n = upsert_events(events)
    print(f"[info] upserted {n:,} calendar_events rows (window: -{WINDOW_PAST_DAYS}d to +{WINDOW_FUTURE_DAYS}d)")


if __name__ == "__main__":
    main()
