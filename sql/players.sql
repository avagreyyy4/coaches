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
-- `player_arms_match` joins current_players against arms in three passes:
--   1. Exact match on normalized full name + grad year. `arms` no longer
--      guarantees (grad_year, full_name) is unique — two different
--      recruits can share a name in the same class (see arms.sql) — so
--      this tier also requires exactly one candidate, same as tiers 2/3;
--      an ambiguous exact match falls through to them instead of
--      returning duplicate rows for the player.
--   2. Exact match on normalized full name ALONE (grad year ignored) —
--      catches cases where our roster and ARMS disagree on someone's grad
--      year (e.g. a player listed as 2028 here but 2027 in ARMS) — but
--      ONLY when that full name is globally unique in `arms` across every
--      grad year, so two different same-named recruits can't collide.
--   3. Fallback fuzzy match — same grad year, same last name (first name
--      not compared at all, since ARMS's "Legal \"Nickname\" Last" format
--      means the nickname doesn't even appear first in their full_name —
--      catches "KK"/"Kniya \"KK\"", "Cherri"/"Zaire \"Cherri\"",
--      "Annie"/"Annaliese", "Radhi"/"Radhika") — but ONLY accepted when the
--      two sides' phone numbers also match (digits only, ignoring
--      formatting). Phone is the real safety check here: matching last
--      name alone isn't enough (two different recruits can share it, e.g.
--      "Layla Davis" vs "London Davis" for a player named "Lily Davis") —
--      an exact phone match is what makes it a confirmed identity, not a
--      guess.
-- A player with more than one candidate at either fallback tier is left
-- unmatched rather than picking one arbitrarily.
--
-- If you ran an earlier version of this file, it created a standalone
-- `players` table that the site no longer reads from — drop it whenever
-- convenient (not required, just unused).

create or replace view public.player_arms_match
with (security_invoker = true) as
with exact_matches as (
  select
    p.id as player_id,
    a.id as arms_id,
    count(*) over (partition by p.id) as candidate_count
  from public.current_players p
  join public.arms a
    on lower(trim(a.full_name)) = lower(trim(p.full_name))
    and trim(a.grad_year) = trim(p.grad_year)
),
name_only_candidates as (
  select
    p.id as player_id,
    a.id as arms_id,
    count(*) over (partition by p.id) as candidate_count
  from public.current_players p
  join public.arms a
    on lower(trim(a.full_name)) = lower(trim(p.full_name))
  where not exists (
    select 1 from exact_matches e where e.player_id = p.id and e.candidate_count = 1
  )
),
fuzzy_candidates as (
  select
    p.id as player_id,
    a.id as arms_id,
    count(*) over (partition by p.id) as candidate_count
  from public.current_players p
  join public.arms a
    on trim(a.grad_year) = trim(p.grad_year)
    and lower(trim(a.full_name)) like '%% ' || lower(trim(p.last_name))
    and p.mobile_phone is not null
    and a.data ->> 'Mobile Phone' is not null
    and regexp_replace(p.mobile_phone, '[^0-9]', '', 'g')
      = regexp_replace(a.data ->> 'Mobile Phone', '[^0-9]', '', 'g')
  where not exists (
    select 1 from exact_matches e where e.player_id = p.id and e.candidate_count = 1
  )
),
matches as (
  select player_id, arms_id from exact_matches where candidate_count = 1
  union all
  select player_id, arms_id from name_only_candidates where candidate_count = 1
  union all
  select player_id, arms_id from fuzzy_candidates
  where candidate_count = 1
    and player_id not in (select player_id from name_only_candidates where candidate_count = 1)
)
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
  (a.id is not null) as matched_in_arms,
  p.sort_order
from public.current_players p
left join matches m on m.player_id = p.id
left join public.arms a on a.id = m.arms_id;
