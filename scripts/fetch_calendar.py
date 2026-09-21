#!/usr/bin/env python3
# Run: python fetch_calendar.py
#
# Pulls events from two named Google Calendars (Staff, WBB) via a service
# account each has been shared with (view-only), and upserts them into
# Supabase's `calendar_events` table, tagged with calendar_name so the site
# can tell them apart. Uses the read-only Calendar scope — this can only
# ever list/read events, never create, edit, or delete anything on any
# calendar.

import json
import os
from datetime import datetime, timedelta, timezone

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# Raw JSON contents of the service account key (paste the whole .json file
# as the GitHub secret value / .env value) — never write this to a file in
# the repo.
GOOGLE_CALENDAR_SA_JSON = os.getenv("GOOGLE_CALENDAR_SA_JSON")

# Each named calendar this script pulls from. The label (dict key) is what
# gets stored as calendar_name in Supabase and is what the site should
# filter/display on — not whatever Google's own calendar title happens to
# be. Add more named calendars here later the same way.
CALENDAR_SOURCES = {
    "Staff": os.getenv("STAFF_CALENDAR_ID"),
    "WBB": os.getenv("WBB_CALENDAR_ID"),
}
CALENDAR_SOURCES = {label: cal_id for label, cal_id in CALENDAR_SOURCES.items() if cal_id}

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# How far back/forward from "today" to pull events. Re-synced on every run,
# so this window just needs to cover how far coaches page around in the
# site's calendar.
WINDOW_PAST_DAYS = 60
WINDOW_FUTURE_DAYS = 180

missing = []
if not GOOGLE_CALENDAR_SA_JSON: missing.append("GOOGLE_CALENDAR_SA_JSON")
if not SUPABASE_URL: missing.append("SUPABASE_URL")
if not SUPABASE_SERVICE_ROLE_KEY: missing.append("SUPABASE_SERVICE_ROLE_KEY")
if not CALENDAR_SOURCES: missing.append("STAFF_CALENDAR_ID and/or WBB_CALENDAR_ID")
if missing:
    raise SystemExit(f"[fatal] Missing required env: {', '.join(missing)}")

_sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _calendar_service():
    info = json.loads(GOOGLE_CALENDAR_SA_JSON)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def _check_access(service, label, cal_id):
    try:
        service.calendars().get(calendarId=cal_id).execute()
    except Exception as e:
        raise SystemExit(
            f"[fatal] Couldn't access the '{label}' calendar ({cal_id}): {e}. "
            "Check the id is correct and the calendar has been shared "
            "with the service account (view-only)."
        )


def fetch_events():
    """
    For each named calendar, lists events in
    [today-WINDOW_PAST_DAYS, today+WINDOW_FUTURE_DAYS] with singleEvents=True
    so recurring events come back as individual instances (no manual
    recurrence expansion needed). Returns [(label, cal_id, event), ...].
    """
    service = _calendar_service()

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=WINDOW_PAST_DAYS)).isoformat()
    time_max = (now + timedelta(days=WINDOW_FUTURE_DAYS)).isoformat()

    results = []
    for label, cal_id in CALENDAR_SOURCES.items():
        _check_access(service, label, cal_id)
        page_token = None
        while True:
            resp = service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
                maxResults=2500,
            ).execute()
            for ev in resp.get("items", []):
                results.append((label, cal_id, ev))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return results


def _start_end(event):
    """Returns (start_iso, end_iso, all_day) from a Calendar API event's start/end."""
    start = event.get("start", {})
    end = event.get("end", {})

    if "dateTime" in start:
        start_iso = start["dateTime"]
        end_iso = end.get("dateTime")
        return start_iso, end_iso, False

    # all-day event: only a "date" (YYYY-MM-DD)
    start_date = start.get("date")
    end_date = end.get("date")
    start_iso = f"{start_date}T00:00:00Z" if start_date else None
    end_iso = f"{end_date}T00:00:00Z" if end_date else None
    return start_iso, end_iso, True


def upsert_events(events):
    records = []
    for label, cal_id, ev in events:
        uid = ev.get("id")
        if not uid:
            continue
        start_iso, end_iso, all_day = _start_end(ev)
        if not start_iso:
            continue

        records.append({
            "calendar_id": cal_id,
            "calendar_name": label,
            "uid": uid,
            "title": ev.get("summary") or None,
            "description": ev.get("description") or None,
            "location": ev.get("location") or None,
            "start_at": start_iso,
            "end_at": end_iso,
            "all_day": all_day,
        })

    if not records:
        return 0

    BATCH = 500
    for i in range(0, len(records), BATCH):
        _sb.table("calendar_events").upsert(
            records[i:i + BATCH], on_conflict="calendar_id,uid,start_at"
        ).execute()

    return len(records)


def main():
    events = fetch_events()
    n = upsert_events(events)
    print(f"[info] upserted {n:,} calendar_events rows from: {', '.join(CALENDAR_SOURCES.keys())}")


if __name__ == "__main__":
    main()
