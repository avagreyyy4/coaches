# Shared direct-Postgres helper for scripts that need to run DDL (CREATE
# TABLE, indexes, RLS, policies) — the Supabase REST API (used elsewhere in
# these scripts via SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY) can't do that.
#
# Requires DATABASE_URL: Supabase dashboard -> Project Settings -> Database
# -> Connection string -> URI (use the "Session pooler" one). Local-only
# secret in scripts/.env, or a repo secret for scripts that also run in
# GitHub Actions — never commit it.
import os

import psycopg2


def get_connection():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("[fatal] Missing required env: DATABASE_URL")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    return conn
