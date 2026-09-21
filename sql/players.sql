-- Run this in the Supabase SQL editor, alongside schema.sql and arms.sql.
--
-- `players` is the roster coaches actually track — the list you enter
-- yourself from what coaches give you, NOT the full ARMS export (that's
-- `arms`, see arms.sql). Entering rows here is expected to happen directly
-- in the Supabase Table Editor for now.
--
-- `player_arms_match` joins players against arms by normalized full name
-- (case/whitespace-insensitive) so a tracked player's row can be enriched
-- with whatever ARMS has on them (phone, address/hometown/state,
-- transcript, etc., all inside arms.data). Field-level "missing" checks and
-- which fields to highlight (phone number, hometown/state) happen in the
-- frontend once we know ARMS's real export column names — this view just
-- makes the matched data available to query.

create table if not exists public.players (
  id uuid primary key default gen_random_uuid(),
  first_name text not null,
  last_name text not null,
  assigned_coach text not null check (assigned_coach in ('ty', 'allie', 'kiz')),
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now()
);

create index if not exists players_full_name_normalized_idx
  on public.players (lower(trim(first_name || ' ' || last_name)));

alter table public.players enable row level security;

-- Same "everyone authenticated can edit everything" policy as the rest of
-- this app (see schema.sql) — any coach can read/add/edit/remove any player.
create policy "Authenticated coaches can read players"
  on public.players for select to authenticated using (true);

create policy "Authenticated coaches can insert players"
  on public.players for insert to authenticated with check (true);

create policy "Authenticated coaches can update players"
  on public.players for update to authenticated using (true) with check (true);

create policy "Authenticated coaches can delete players"
  on public.players for delete to authenticated using (true);

-- security_invoker so this view enforces the querying user's own RLS
-- (players + arms policies above) instead of running as the view owner.
create or replace view public.player_arms_match
with (security_invoker = true) as
select
  p.id as player_id,
  p.first_name,
  p.last_name,
  p.assigned_coach,
  p.created_at,
  a.id as arms_id,
  a.grad_year,
  a.data as arms_data,
  a.synced_at as arms_synced_at,
  (a.id is not null) as matched_in_arms
from public.players p
left join public.arms a
  on lower(trim(a.full_name)) = lower(trim(p.first_name || ' ' || p.last_name));
