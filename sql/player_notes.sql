-- Shared per-player notes. Run in the Supabase SQL editor (or via psql with
-- DATABASE_URL). Idempotent.
--
-- Keyed by normalized full name (lower/trimmed) rather than a roster row id:
-- the monthly players tables (sep_players, oct_players, ...) each generate
-- fresh ids, so a note keyed on that id would vanish every month. Any coach
-- can read and write any note, same "everyone edits everything" model as
-- the checkboxes.

create table if not exists public.player_notes (
  player_key text primary key,
  note text not null default '',
  updated_by text,
  updated_at timestamptz not null default now()
);

alter table public.player_notes enable row level security;

drop policy if exists player_notes_select on public.player_notes;
create policy player_notes_select on public.player_notes
  for select to authenticated using (true);

drop policy if exists player_notes_insert on public.player_notes;
create policy player_notes_insert on public.player_notes
  for insert to authenticated with check (true);

drop policy if exists player_notes_update on public.player_notes;
create policy player_notes_update on public.player_notes
  for update to authenticated using (true) with check (true);
