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
-- `full_name` are pulled out as real columns because `full_name` is the
-- join key against `players` — but they are NOT unique: two different
-- recruits can share the same full name in the same grad year (confirmed
-- happening ~15-20 times per class), and ARMS's own per-recruit `'ID`
-- field (kept in `data`, surfaced here as `arms_id`) is what the
-- ingestion script actually upserts on, so same-named recruits no longer
-- overwrite each other.

create table if not exists public.arms (
  id uuid primary key default gen_random_uuid(),
  arms_id text not null,
  grad_year text not null,
  full_name text not null,
  data jsonb not null default '{}'::jsonb,
  source_export text,
  synced_at timestamptz not null default now(),
  unique (arms_id)
);

-- Migration for a database created before arms_id existed: add the
-- column, backfill it from the ARMS ID already stored in `data`, then
-- swap the unique constraint from (grad_year, full_name) to arms_id.
-- Safe to re-run — every step is idempotent/guarded.
alter table public.arms add column if not exists arms_id text;
update public.arms set arms_id = data->>$$'ID$$ where arms_id is null;
alter table public.arms alter column arms_id set not null;
alter table public.arms drop constraint if exists arms_grad_year_full_name_key;
do $$
begin
  if not exists (
    select 1 from pg_constraint where conrelid = 'public.arms'::regclass and conname = 'arms_arms_id_key'
  ) then
    alter table public.arms add constraint arms_arms_id_key unique (arms_id);
  end if;
end $$;

-- Case/whitespace-insensitive name matching for the join against `players`.
-- Not unique — see note above.
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
