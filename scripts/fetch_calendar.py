#!/usr/bin/env python3
# Run: python fetch_calendar.py
#
# Pulls events from named calendars (Staff, WBB) and upserts them into
# Supabase's `calendar_events` table, tagged with calendar_name so the site
# can tell them apart. Each calendar can be wired up either way:
#
#   - Secret iCal URL (FOO_CALENDAR_ICAL_URL): a private read-only feed
#     link from that calendar's Settings -> "Integrate calendar" -> "Secret
#     address in iCal format". Works from any Google account regardless of
#     domain sharing policy, since it's just an unguessable URL, not a
#     grant to a specific identity. Preferred when available.
#   - Service account (FOO_CALENDAR_ID + GOOGLE_CALENDAR_SA_JSON): the
#     calendar owner shares the calendar (view-only) with the service
#     account's email. Needs Calendar API access and (for external
#     accounts) a domain sharing policy that allows it.
#
# Both paths are read-only by construction — an iCal feed can't be used to
# write anything, and the service account only ever requests the read-only
# Calendar scope.

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from icalendar import Calendar as ICalFeed
import recurring_ical_events
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from supabase import create_client
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_connection

load_dotenv()

# Raw JSON contents of the service account key (paste the whole .json file
# as the GitHub secret value / .env value) — never write this to a file in
# the repo. Only needed for calendars using the *_CALENDAR_ID path below.
GOOGLE_CALENDAR_SA_JSON = os.getenv("GOOGLE_CALENDAR_SA_JSON")

# Labels this script knows about. The label is what gets stored as
# calendar_name in Supabase and is what the site should filter/display on —
# not whatever Google's own calendar title happens to be. Add more labels
# here later the same way, wiring up either a *_CALENDAR_ICAL_URL or a
# *_CALENDAR_ID (+ GOOGLE_CALENDAR_SA_JSON) for each.
CALENDAR_LABELS = ["Staff", "WBB"]

# label -> secret iCal feed URL
ICAL_SOURCES = {
    label: url
    for label in CALENDAR_LABELS
    if (url := os.getenv(f"{label.upper()}_CALENDAR_ICAL_URL"))
}

# label -> Calendar ID (service-account path). Skipped for any label already
# covered by ICAL_SOURCES above.
API_SOURCES = {
    label: cal_id
    for label in CALENDAR_LABELS
    if label not in ICAL_SOURCES and (cal_id := os.getenv(f"{label.upper()}_CALENDAR_ID"))
}

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# How far back/forward from "today" to pull events. Re-synced on every run,
# so this window just needs to cover how far coaches page around in the
# site's calendar.
WINDOW_PAST_DAYS = 60
WINDOW_FUTURE_DAYS = 180

missing = []
if not (ICAL_SOURCES or API_SOURCES):
    missing.append("at least one of STAFF_CALENDAR_ICAL_URL / WBB_CALENDAR_ICAL_URL / STAFF_CALENDAR_ID / WBB_CALENDAR_ID")
if API_SOURCES and not GOOGLE_CALENDAR_SA_JSON:
    missing.append("GOOGLE_CALENDAR_SA_JSON (required because a *_CALENDAR_ID source is configured)")
if not SUPABASE_URL: missing.append("SUPABASE_URL")
if not SUPABASE_SERVICE_ROLE_KEY: missing.append("SUPABASE_SERVICE_ROLE_KEY")
if not os.getenv("DATABASE_URL"): missing.append("DATABASE_URL")
if missing:
    raise SystemExit(f"[fatal] Missing required env: {', '.join(missing)}")

_sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def ensure_calendar_events_table():
    """Idempotent: mirrors sql/calendar_events.sql. Lets this script
    bootstrap a fresh Supabase project on its own."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                create table if not exists public.calendar_events (
                  id uuid primary key default gen_random_uuid(),
                  calendar_id text,
                  calendar_name text,
                  uid text not null,
                  title text,
                  description text,
                  location text,
                  start_at timestamptz not null,
                  end_at timestamptz,
                  all_day boolean not null default false,
                  synced_at timestamptz not null default now()
                );
            """)
            cur.execute("""
                alter table public.calendar_events add column if not exists calendar_id text;
            """)
            cur.execute("""
                alter table public.calendar_events add column if not exists calendar_name text;
            """)
            cur.execute("""
                update public.calendar_events set calendar_id = coalesce(calendar_id, '') where calendar_id is null;
            """)
            cur.execute("""
                alter table public.calendar_events alter column calendar_id set not null;
            """)
            cur.execute("""
                alter table public.calendar_events drop constraint if exists calendar_events_uid_start_at_key;
            """)
            cur.execute("""
                alter table public.calendar_events drop constraint if exists calendar_events_calendar_id_uid_start_at_key;
            """)
            cur.execute("""
                alter table public.calendar_events
                  add constraint calendar_events_calendar_id_uid_start_at_key
                  unique (calendar_id, uid, start_at);
            """)
            cur.execute("""
                create index if not exists calendar_events_start_at_idx
                  on public.calendar_events (start_at);
            """)
            cur.execute("alter table public.calendar_events enable row level security;")
            cur.execute(
                'drop policy if exists "Authenticated coaches can read calendar_events" on public.calendar_events;'
            )
            cur.execute("""
                create policy "Authenticated coaches can read calendar_events"
                  on public.calendar_events for select to authenticated using (true);
            """)
    finally:
        conn.close()


def _calendar_service():
    info = json.loads(GOOGLE_CALENDAR_SA_JSON)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def diagnose():
    """
    python fetch_calendar.py --diagnose
    Lists every calendar the service account currently has access to,
    straight from Google — independent of whatever CALENDAR_ID we've
    configured. Doesn't touch Supabase. Useful whenever a calendar 404s:
    tells you definitively whether sharing actually took effect, and
    exactly what id/name Google has on file for it. Only relevant to the
    service-account path; iCal URLs don't need diagnosing this way.
    """
    if not GOOGLE_CALENDAR_SA_JSON:
        print("[diagnose] GOOGLE_CALENDAR_SA_JSON isn't set — nothing to diagnose "
              "(only relevant if you're using the *_CALENDAR_ID/service-account path).")
        return
    info = json.loads(GOOGLE_CALENDAR_SA_JSON)
    print(f"[diagnose] Authenticating as service account: {info.get('client_email')!r}")
    service = _calendar_service()
    items = service.calendarList().list().execute().get("items", [])
    if not items:
        print("[diagnose] This service account has ZERO calendars shared with it right now.")
        return
    print(f"[diagnose] This service account currently sees {len(items)} calendar(s):")
    for item in items:
        print(f"  - id={item.get('id')!r} summary={item.get('summary')!r} access_role={item.get('accessRole')!r}")


def _check_access(service, label, cal_id):
    try:
        service.calendars().get(calendarId=cal_id).execute()
    except Exception as e:
        raise SystemExit(
            f"[fatal] Couldn't access the '{label}' calendar ({cal_id}): {e}. "
            "Check the id is correct and the calendar has been shared "
            "with the service account (view-only)."
        )


def _as_utc_iso(dt):
    """Normalizes an icalendar/Calendar-API date or datetime value to a UTC
    ISO string, and reports whether it was an all-day (date-only) value."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(), False
    # date-only (all-day event)
    return f"{dt.isoformat()}T00:00:00Z", True


def _fetch_ical_records(label, url, time_min, time_max):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    feed = ICalFeed.from_ical(resp.content)

    records = []
    for ev in recurring_ical_events.of(feed).between(time_min, time_max):
        uid = str(ev.get("UID") or "")
        if not uid:
            continue
        start = ev.get("DTSTART")
        if start is None:
            continue
        start_iso, all_day = _as_utc_iso(start.dt)
        end = ev.get("DTEND")
        end_iso = _as_utc_iso(end.dt)[0] if end is not None else None

        records.append({
            "calendar_id": f"ical:{label}",
            "calendar_name": label,
            "uid": uid,
            "title": str(ev.get("SUMMARY")) if ev.get("SUMMARY") else None,
            "description": str(ev.get("DESCRIPTION")) if ev.get("DESCRIPTION") else None,
            "location": str(ev.get("LOCATION")) if ev.get("LOCATION") else None,
            "start_at": start_iso,
            "end_at": end_iso,
            "all_day": all_day,
        })
    return records


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


def _fetch_api_records(service, label, cal_id, time_min_iso, time_max_iso):
    _check_access(service, label, cal_id)
    records = []
    page_token = None
    while True:
        resp = service.events().list(
            calendarId=cal_id,
            timeMin=time_min_iso,
            timeMax=time_max_iso,
            singleEvents=True,
            orderBy="startTime",
            pageToken=page_token,
            maxResults=2500,
        ).execute()
        for ev in resp.get("items", []):
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
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return records


def fetch_events():
    """Pulls events in [today-WINDOW_PAST_DAYS, today+WINDOW_FUTURE_DAYS]
    from every configured calendar (iCal feeds and/or the API), already
    normalized into the record shape upsert_events expects."""
    now = datetime.now(timezone.utc)
    time_min_dt = now - timedelta(days=WINDOW_PAST_DAYS)
    time_max_dt = now + timedelta(days=WINDOW_FUTURE_DAYS)
    time_min_iso = time_min_dt.isoformat()
    time_max_iso = time_max_dt.isoformat()

    records = []
    for label, url in ICAL_SOURCES.items():
        records.extend(_fetch_ical_records(label, url, time_min_dt, time_max_dt))

    if API_SOURCES:
        service = _calendar_service()
        for label, cal_id in API_SOURCES.items():
            records.extend(_fetch_api_records(service, label, cal_id, time_min_iso, time_max_iso))

    return records


def upsert_events(records):
    if not records:
        return 0

    BATCH = 500
    for i in range(0, len(records), BATCH):
        _sb.table("calendar_events").upsert(
            records[i:i + BATCH], on_conflict="calendar_id,uid,start_at"
        ).execute()

    return len(records)


def main():
    if "--diagnose" in sys.argv:
        diagnose()
        return
    ensure_calendar_events_table()
    records = upsert_events(fetch_events())
    labels = sorted(set(ICAL_SOURCES) | set(API_SOURCES))
    print(f"[info] upserted {records:,} calendar_events rows from: {', '.join(labels)}")


if __name__ == "__main__":
    main()
