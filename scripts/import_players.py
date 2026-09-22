#!/usr/bin/env python3
# Run: python import_players.py ../data/sep_players.csv
#
# Fully automated: creates the month's table (named after the CSV file —
# sep_players.csv -> table "sep_players") if it doesn't exist yet, points
# the `current_players` view at it, then upserts the CSV rows. Safe to
# re-run: re-importing an updated CSV only touches roster fields (name,
# coach, phone, rank, grad year, sort order) for players already in the
# table — it never resets their workflow state (queue_status/checked/
# activated_at/contacted_at).
#
# This script is local-only by design: the CSV (real recruit names) never
# leaves this machine and is never committed to the repo.

import csv
import os
import re
import sys
from pathlib import Path

from psycopg2 import sql
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_connection

# Coach initials as they appear in the CSV's "Coach" column -> our coach ids.
COACH_MAP = {
    "TC": "ty",
    "KG": "kiz",
    "ABC": "allie",
}


def main():
    load_dotenv()

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python import_players.py <path-to-csv>")

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        raise SystemExit(f"[fatal] File not found: {csv_path}")

    table_name = csv_path.stem  # "sep_players.csv" -> "sep_players"
    if not re.fullmatch(r"[a-z][a-z0-9_]*", table_name):
        raise SystemExit(
            f"[fatal] '{table_name}' isn't a safe table name — rename the "
            "file to something like 'sep_players.csv' (lowercase letters, "
            "numbers, underscores only)."
        )

    conn = get_connection()
    try:
        ensure_table(conn, table_name)
        records = load_rows(csv_path)
        if not records:
            print("[info] No valid rows to import.")
            return
        upsert_records(conn, table_name, records)
        print(f"[info] Imported {len(records):,} players into '{table_name}'.")
    finally:
        conn.close()


def ensure_table(conn, table_name):
    tbl = sql.Identifier(table_name)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("""
            create table if not exists public.{tbl} (
              id uuid primary key default gen_random_uuid(),
              first_name text not null,
              last_name text not null,
              full_name text not null,
              acs_rank text,
              grad_year text,
              mobile_phone text,
              assigned_coach text not null check (assigned_coach in ('ty', 'allie', 'kiz')),
              sort_order integer not null,
              queue_status text not null default 'pending' check (queue_status in ('pending', 'active', 'contacted')),
              checked boolean not null default false,
              activated_at timestamptz,
              contacted_at timestamptz,
              created_at timestamptz not null default now(),
              unique (full_name, assigned_coach)
            );
        """).format(tbl=tbl))

        cur.execute(sql.SQL("""
            create index if not exists {idx}
              on public.{tbl} (assigned_coach, queue_status, sort_order);
        """).format(idx=sql.Identifier(f"{table_name}_coach_status_idx"), tbl=tbl))

        cur.execute(sql.SQL("alter table public.{tbl} enable row level security;").format(tbl=tbl))

        select_pol = sql.Identifier(f"{table_name}_select")
        cur.execute(sql.SQL("drop policy if exists {pol} on public.{tbl};").format(pol=select_pol, tbl=tbl))
        cur.execute(sql.SQL("""
            create policy {pol} on public.{tbl}
              for select to authenticated using (true);
        """).format(pol=select_pol, tbl=tbl))

        update_pol = sql.Identifier(f"{table_name}_update")
        cur.execute(sql.SQL("drop policy if exists {pol} on public.{tbl};").format(pol=update_pol, tbl=tbl))
        cur.execute(sql.SQL("""
            create policy {pol} on public.{tbl}
              for update to authenticated using (true) with check (true);
        """).format(pol=update_pol, tbl=tbl))

        # Points the site at this month's table — the frontend always
        # queries current_players and never needs to know the real table
        # name or change when the month rolls over.
        cur.execute(sql.SQL("""
            create or replace view public.current_players
              with (security_invoker = true) as
              select * from public.{tbl};
        """).format(tbl=tbl))

    print(f"[info] Ensured table 'public.{table_name}' (+ RLS policies) exists; "
          f"current_players now points at it.")


def load_rows(csv_path):
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    records = []
    unknown_coaches = set()
    for i, row in enumerate(rows):
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        if not first or not last:
            continue

        coach_code = (row.get("Coach") or "").strip()
        coach_id = COACH_MAP.get(coach_code)
        if not coach_id:
            unknown_coaches.add(coach_code or "(blank)")
            continue

        records.append((
            first,
            last,
            f"{first} {last}",
            (row.get("ACS Rank") or "").strip() or None,
            (row.get("Grad. Year") or "").strip() or None,
            (row.get("Mobile Phone") or "").strip() or None,
            coach_id,
            i,
        ))

    if unknown_coaches:
        print(f"[warn] skipped rows with unrecognized Coach values: {sorted(unknown_coaches)}")

    return records


def upsert_records(conn, table_name, records):
    tbl = sql.Identifier(table_name)
    stmt = sql.SQL("""
        insert into public.{tbl}
          (first_name, last_name, full_name, acs_rank, grad_year, mobile_phone, assigned_coach, sort_order)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (full_name, assigned_coach) do update set
          first_name = excluded.first_name,
          last_name = excluded.last_name,
          acs_rank = excluded.acs_rank,
          grad_year = excluded.grad_year,
          mobile_phone = excluded.mobile_phone,
          sort_order = excluded.sort_order;
    """).format(tbl=tbl)

    with conn.cursor() as cur:
        for rec in records:
            cur.execute(stmt, rec)


if __name__ == "__main__":
    main()
