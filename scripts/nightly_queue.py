#!/usr/bin/env python3
# Runs nightly (see .github/workflows/nightly-queue.yml) to roll each
# coach's recruit queue over to a new day:
#
#   1. Anyone currently active whose box was checked gets marked contacted
#      and drops out of the active list for good.
#   2. Anyone active but still unchecked stays active untouched — they
#      carry over onto today's list rather than getting reassigned.
#   3. Each coach gets topped back up to carryover + 5 by activating their
#      next 5 pending players (in sort_order). If a coach hit quota every
#      day, that's just 5; if they left some unchecked, today's list is
#      bigger by exactly that leftover — same rollover rule js/mock-data.js
#      describes, just backed by real state instead of a mock chain.
#
# Reads/writes through the `current_players` view, so it always lands on
# whichever month's table scripts/import_players.py last pointed it at.
# Local-safe to run manually (DATABASE_URL only, no ARMS/Google creds
# needed) for testing before the schedule fires for real.

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_connection

DAILY_QUOTA = 5
COACHES = ["ty", "allie", "kiz"]


def main():
    load_dotenv()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                update current_players
                set queue_status = 'contacted', contacted_at = now()
                where queue_status = 'active' and checked = true;
            """)
            print(f"[info] Marked {cur.rowcount} checked player(s) as contacted.")

            for coach in COACHES:
                cur.execute("""
                    select count(*) from current_players
                    where assigned_coach = %s and queue_status = 'active';
                """, (coach,))
                carried_over = cur.fetchone()[0]

                cur.execute("""
                    update current_players
                    set queue_status = 'active', activated_at = now()
                    where id in (
                      select id from current_players
                      where assigned_coach = %s and queue_status = 'pending'
                      order by sort_order
                      limit %s
                    );
                """, (coach, DAILY_QUOTA))
                added = cur.rowcount

                print(f"[info] {coach}: {carried_over} carried over + {added} new "
                      f"= {carried_over + added} on today's list.")
                if added < DAILY_QUOTA:
                    print(f"[warn] {coach} only had {added} pending player(s) left to add "
                          f"(wanted {DAILY_QUOTA}) — roster may be running low.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
