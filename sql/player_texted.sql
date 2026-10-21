-- Shared "texted" flag per player: a mental note for coaches, nothing more.
-- Run in the Supabase SQL editor (or via psql with DATABASE_URL). Idempotent.
--
-- Keyed by normalized full name for the same reason as player_notes: the
-- monthly players tables regenerate ids. Checking this never changes a
-- recruit's queue_status or checked state — it only records who texted.

create table if not exists public.player_texted (
  player_key text primary key,
  texted boolean not null default false,
  updated_by text,
  updated_at timestamptz not null default now()
);

alter table public.player_texted enable row level security;

drop policy if exists player_texted_select on public.player_texted;
create policy player_texted_select on public.player_texted
  for select to authenticated using (true);

drop policy if exists player_texted_insert on public.player_texted;
create policy player_texted_insert on public.player_texted
  for insert to authenticated with check (true);

drop policy if exists player_texted_update on public.player_texted;
create policy player_texted_update on public.player_texted
  for update to authenticated using (true) with check (true);
