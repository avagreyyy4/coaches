-- Run this in the Supabase SQL editor, alongside the other sql/*.sql files.
--
-- Holds events pulled from the "staff" Google Calendar by
-- scripts/fetch_calendar.py. Recurring events are expanded into individual
-- instances before being stored (so a weekly meeting becomes one row per
-- occurrence) — `uid` is the source calendar event's stable id, repeated
-- across a recurring event's instances, so the upsert key is (uid, start_at).

create table if not exists public.calendar_events (
  id uuid primary key default gen_random_uuid(),
  uid text not null,
  title text,
  description text,
  location text,
  start_at timestamptz not null,
  end_at timestamptz,
  all_day boolean not null default false,
  synced_at timestamptz not null default now(),
  unique (uid, start_at)
);

create index if not exists calendar_events_start_at_idx
  on public.calendar_events (start_at);

alter table public.calendar_events enable row level security;

-- Coaches (frontend, anon/publishable key) can only read.
create policy "Authenticated coaches can read calendar_events"
  on public.calendar_events
  for select
  to authenticated
  using (true);

-- No insert/update/delete policy for `authenticated` here on purpose: only
-- the service-role key writes to this table (used exclusively by
-- .github/workflows/calendar-sync.yml) — never in the frontend, never
-- committed.
