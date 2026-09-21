-- Run this in the Supabase SQL editor (Project -> SQL Editor -> New query).

create table if not exists public.progress (
  id uuid primary key default gen_random_uuid(),
  task_key text not null unique,
  checked boolean not null default false,
  coach_id uuid references auth.users(id),
  coach_email text,
  updated_at timestamptz not null default now()
);

-- RLS is mandatory: with it off, the anon key in the frontend could read/write
-- this table with no login at all.
alter table public.progress enable row level security;

-- Policy model: ANY authenticated coach can read and write ANY row
-- (not restricted to auth.uid() = coach_id). This matches "everyone edits
-- everything" chosen for this app.
create policy "Authenticated coaches can read progress"
  on public.progress
  for select
  to authenticated
  using (true);

create policy "Authenticated coaches can insert progress"
  on public.progress
  for insert
  to authenticated
  with check (true);

create policy "Authenticated coaches can update progress"
  on public.progress
  for update
  to authenticated
  using (true)
  with check (true);

-- No delete policy: rows are only ever inserted/updated via upsert from the
-- frontend, so deletes are intentionally left unavailable to the anon key.

-- Enable Realtime for this table so checkbox changes push to other coaches
-- without a refresh. (Also toggle "Realtime" on for the `progress` table in
-- Database -> Replication in the Supabase dashboard if this doesn't take.)
alter publication supabase_realtime add table public.progress;

-- Seed one row per task key up front (optional). Edit js/tasks.js to match
-- your real checklist, then update this list to match, or just let the
-- frontend create rows lazily as coaches check boxes for the first time.
