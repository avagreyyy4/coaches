-- Run this in the Supabase SQL editor, alongside schema.sql and arms.sql —
-- but only for the join view below. Player data itself no longer lives in
-- a standalone `players` table here.
--
-- Player rosters now live in monthly tables (sep_players, oct_players,
-- ...), created and kept up to date by scripts/import_players.py from a
-- CSV that never leaves your machine — see that script and the README's
-- "Player roster import" section. That script also creates/repoints a
-- `current_players` view at whichever month's table is active, so the
-- join below (and the site) never needs to know the real table name.
--
-- `player_arms_match` joins current_players against arms by normalized
-- full name (case/whitespace-insensitive) so a tracked player's row comes
-- back enriched with whatever ARMS has on them (phone, address/hometown/
-- state, transcript, etc., all inside arms.data). Field-level "missing"
-- checks and which fields to highlight (phone number, hometown/state)
-- happen in the frontend once we know ARMS's real export column names —
-- this view just makes the matched data available to query.
--
-- If you ran an earlier version of this file, it created a standalone
-- `players` table that the site no longer reads from — drop it whenever
-- convenient (not required, just unused).

create or replace view public.player_arms_match
with (security_invoker = true) as
select
  p.id as player_id,
  p.first_name,
  p.last_name,
  p.full_name,
  p.assigned_coach,
  p.queue_status,
  p.checked,
  p.acs_rank,
  p.grad_year,
  p.mobile_phone,
  a.id as arms_id,
  a.grad_year as arms_grad_year,
  a.data as arms_data,
  a.synced_at as arms_synced_at,
  (a.id is not null) as matched_in_arms
from public.current_players p
left join public.arms a
  on lower(trim(a.full_name)) = lower(trim(p.full_name));
