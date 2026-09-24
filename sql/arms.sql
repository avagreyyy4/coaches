-- Run this in the Supabase SQL editor, alongside schema.sql and players.sql.
--
-- Holds the full ARMS export (grad years 2027-2030, all recruits ARMS
-- knows about) pulled daily by scripts/fetch_and_push.py. This is NOT the list
-- of players a coach is actually tracking — that's `players` (see
-- players.sql). `arms` gets joined against `players` by full name so a
-- tracked player's row can be enriched with whatever ARMS has on them.
--
-- The exact set of fields ARMS exports depends on its "Full Info" export
-- layout, which isn't fixed here — everything the export gives us is kept
-- in `data` (jsonb) so nothing is lost or guessed at. `grad_year` and
-- `full_name` are pulled out as real columns because the ingestion script
-- needs them to upsert (avoid duplicate rows on every monthly run) and
-- because `full_name` is the join key against `players`.

create table if not exists public.arms (
  id uuid primary key default gen_random_uuid(),
  grad_year text not null,
  full_name text not null,
  data jsonb not null default '{}'::jsonb,
  source_export text,
  synced_at timestamptz not null default now(),
  unique (grad_year, full_name)
);

-- Case/whitespace-insensitive name matching for the join against `players`.
create index if not exists arms_full_name_normalized_idx
  on public.arms (lower(trim(full_name)));

alter table public.arms enable row level security;

-- Coaches (frontend, anon/publishable key) can only read.
create policy "Authenticated coaches can read arms"
  on public.arms
  for select
  to authenticated
  using (true);

-- No insert/update/delete policy for `authenticated` here on purpose: only
-- the service-role key can write to this table, and RLS doesn't apply to
-- it. That key belongs ONLY in GitHub Actions secrets (used by
-- .github/workflows/arms-ingest.yml) — never in the frontend, never
-- committed.
