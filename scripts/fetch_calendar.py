#!/usr/bin/env python3
# Run: python fetch_calendar.py
#
# Pulls events from every Google Calendar shared with a service account
# (the "staff" calendar, plus whatever else gets shared with it later —
# sharing is the whole configuration, nothing to list by hand here) and
# upserts them into Supabase's `calendar_events` table. Uses the read-only
# Calendar scope — this can only ever list/read events, never create, edit,
# or delete anything on any calendar.

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
# Comma-separated Calendar IDs to pull from, e.g. "abc@group.calendar.google.com,def@group.calendar.google.com".
# Recommended: explicit and predictable — only ever reads exactly these
# calendars, nothing more, regardless of what else might get shared with
# the service account later. Leave unset to instead auto-pull from every
# calendar currently shared with it (see _list_shared_calendars).
CALENDAR_IDS = [c.strip() for c in (os.getenv("CALENDAR_IDS") or "").split(",") if c.strip()]

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
if missing:
    raise SystemExit(f"[fatal] Missing required env: {', '.join(missing)}")

_sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _calendar_service():
    info = json.loads(GOOGLE_CALENDAR_SA_JSON)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def _list_shared_calendars(service):
    """Every calendar this service account currently has read access to."""
    items = service.calendarList().list().execute().get("items", [])
    if not items:
        raise SystemExit(
            "[fatal] This service account has no calendars shared with it yet — "
            "ask each calendar's owner to share it (view-only) with the "
            "service account's email."
        )
    return items


def _resolve_calendars(service):
    """
    CALENDAR_IDS set -> use exactly those (safest: explicit, predictable,
    reads only what's listed no matter what else gets shared with the
    service account). Otherwise -> every calendar currently shared with it.
    """
    if not CALENDAR_IDS:
        return _list_shared_calendars(service)

    calendars = []
    for cal_id in CALENDAR_IDS:
        try:
            meta = service.calendars().get(calendarId=cal_id).execute()
        except Exception as e:
            raise SystemExit(
                f"[fatal] Couldn't access calendar '{cal_id}': {e}. "
                "Check the id is correct and the calendar has been shared "
                "with the service account (view-only)."
            )
        calendars.append({"id": cal_id, "summary": meta.get("summary", cal_id)})
    return calendars


def fetch_events():
    """
    For every calendar shared with the service account, lists events in
    [today-WINDOW_PAST_DAYS, today+WINDOW_FUTURE_DAYS] with singleEvents=True
    so recurring events come back as individual instances (no manual
    recurrence expansion needed). Returns [(calendar, event), ...].
    """
    service = _calendar_service()
    calendars = _resolve_calendars(service)

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=WINDOW_PAST_DAYS)).isoformat()
    time_max = (now + timedelta(days=WINDOW_FUTURE_DAYS)).isoformat()

    results = []
    for cal in calendars:
        page_token = None
        while True:
            resp = service.events().list(
                calendarId=cal["id"],
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
                maxResults=2500,
            ).execute()
            for ev in resp.get("items", []):
                results.append((cal, ev))
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


def upsert_events(calendar_and_events):
    records = []
    for cal, ev in calendar_and_events:
        uid = ev.get("id")
        if not uid:
            continue
        start_iso, end_iso, all_day = _start_end(ev)
        if not start_iso:
            continue

        records.append({
            "calendar_id": cal["id"],
            "calendar_name": cal.get("summary") or cal["id"],
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
    calendar_and_events = fetch_events()
    calendars_seen = sorted({cal.get("summary", cal["id"]) for cal, _ in calendar_and_events})
    n = upsert_events(calendar_and_events)
    print(f"[info] upserted {n:,} calendar_events rows from: {', '.join(calendars_seen) or '(none)'}")


if __name__ == "__main__":
    main()
