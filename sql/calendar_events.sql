-- Run this in the Supabase SQL editor, alongside the other sql/*.sql files.
-- Safe to re-run even if you already ran an earlier version of this file.
--
-- Holds events pulled from every Google Calendar shared with the
-- fetch_calendar.py service account (the "staff" calendar, plus whatever
-- else gets shared with it later). Recurring events are expanded into
-- individual instances before being stored (so a weekly meeting becomes
-- one row per occurrence) — `uid` is the source event's stable id, repeated
-- across a recurring event's instances and possibly reused across
-- different calendars, so the upsert key is (calendar_id, uid, start_at).

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

-- Migration for anyone who already ran the earlier single-calendar version
-- of this file (harmless no-ops on a fresh table).
alter table public.calendar_events add column if not exists calendar_id text;
alter table public.calendar_events add column if not exists calendar_name text;
update public.calendar_events set calendar_id = coalesce(calendar_id, '') where calendar_id is null;
alter table public.calendar_events alter column calendar_id set not null;

alter table public.calendar_events drop constraint if exists calendar_events_uid_start_at_key;
alter table public.calendar_events drop constraint if exists calendar_events_calendar_id_uid_start_at_key;
alter table public.calendar_events
  add constraint calendar_events_calendar_id_uid_start_at_key
  unique (calendar_id, uid, start_at);

create index if not exists calendar_events_start_at_idx
  on public.calendar_events (start_at);

alter table public.calendar_events enable row level security;

-- Coaches (frontend, anon/publishable key) can only read.
drop policy if exists "Authenticated coaches can read calendar_events" on public.calendar_events;
create policy "Authenticated coaches can read calendar_events"
  on public.calendar_events
  for select
  to authenticated
  using (true);

-- No insert/update/delete policy for `authenticated` here on purpose: only
-- the service-role key writes to this table (used exclusively by
-- .github/workflows/calendar-sync.yml) — never in the frontend, never
-- committed.
