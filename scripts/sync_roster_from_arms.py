#!/usr/bin/env python3
# Runs right after each ARMS export (see .github/workflows/arms-ingest.yml)
# to copy ARMS's ACS rank onto the roster (current_players -> whichever
# monthly table is active). ARMS is the more accurate source, so it wins
# over the CSV import, even when the roster already has a rank:
#
#   acs_rank <- arms.data 'ACS Rank'
#
# Only overwrites with a non-empty ARMS value, so a blank in ARMS never
# wipes out what the roster has. Players with no ARMS match are untouched.
# Phone is deliberately NOT synced — the roster's number is the one to
# keep. Names, coach, sort order and grad year stay as imported (grad year is
# deliberately excluded: the roster can put someone in a different class
# than ARMS on purpose, and that drives the missing-info rules).
#
# Safe to run any time and to re-run; local runs need only DATABASE_URL.

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_connection


def main():
    load_dotenv()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                update current_players p
                set acs_rank = coalesce(nullif(trim(m.arms_data ->> 'ACS Rank'), ''), p.acs_rank)
                from player_arms_match m
                where m.player_id = p.id
                  and m.matched_in_arms
                  and p.acs_rank is distinct from coalesce(nullif(trim(m.arms_data ->> 'ACS Rank'), ''), p.acs_rank);
            """)
            print(f"[info] Updated ACS rank from ARMS on {cur.rowcount} roster player(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
