# Coach Checklist

Static site (GitHub Pages) + Supabase for auth, per-coach recruiting dashboards.

## Pages

- `index.html` — home page: a card per coach (Ty, Allie, Kiz) showing
  `{done}/{goal}` recruits reached out to today.
- `ty.html`, `allie.html`, `kiz.html` — each coach's own page: a monthly
  calendar on the left, today's remaining recruit outreach list on the right.

## Current status: UI is built on mock data

`js/mock-data.js` generates fake but realistic numbers (daily targets, a
month of calendar history, today's recruit names) so the design could be
reviewed before real data was ready. **Nothing on these pages reads from or
writes to Supabase yet** — checking a box in the recruit list only updates
the page in memory and resets on reload.

The `progress` table + RLS policies in `sql/schema.sql` were built for an
earlier, simpler "shared checklist" version of this app and no longer match
what the UI needs (per-coach daily quotas with rollover, a recruit list per
day, calendar history). That table already exists in the live Supabase
project but nothing references it now. The real data model is `arms` +
`players` (see "Data architecture" below) — `progress` will likely get
dropped once the coach pages are wired to real data.

## How auth works (this part is real)

- Page loads publicly, but nothing in `#app-panel` renders until a coach
  logs in via Supabase Auth with email + password — see `js/auth.js`.
- Sessions persist across visits (Supabase stores the session in
  `localStorage` by default) — coaches stay logged in.
- `js/config.js` has the project URL and publishable (anon) key filled in.
  Never put the secret key here or anywhere in this repo.

## Setup / deploy

1. **Enable GitHub Pages.** Push this repo to GitHub, then in
   Settings → Pages, serve from the `main` branch root.
2. **Add the Pages URL to Supabase's redirect allow-list**
   (Authentication → URL Configuration → Redirect URLs) so signup-confirmation
   emails redirect back correctly once this isn't just `localhost`.
3. **Decide on signups.** The login screen has a self-service "Sign Up"
   button. For a 3-coach team you may want to turn off public sign-ups
   (Authentication → Providers → Email) and create the three accounts
   yourself instead.

## Data architecture

- **`arms`** (`sql/arms.sql`, auto-created by `scripts/fetch_and_push.py`)
  — the full ARMS export for grad years 2028 and 2027 (the roster spans
  both), refreshed monthly. This is ARMS's entire recruit pool, not just
  players being tracked.
- **A monthly players table** (`sep_players`, `oct_players`, ...) — the
  actual roster, imported from a CSV you hand-manage locally by
  `scripts/import_players.py` (see below). Each month is its own table;
  it doesn't carry over from the previous month.
- **`current_players`** — a view `import_players.py` repoints at whichever
  month's table is currently active. The site and the join below always
  read through this view, so nothing about the site changes month to
  month — only which real table `current_players` points at.
- **`player_arms_match`** (`sql/players.sql`) — joins `current_players`
  against `arms` on normalized full name (case/whitespace-insensitive) so
  each tracked player comes back enriched with whatever ARMS has on them
  (`arms_data`, plus `matched_in_arms` telling you if no ARMS row matched).

Which fields count as "missing," and which get highlighted (phone number
and hometown/state specifically) still needs to be wired into the
frontend — that logic depends on ARMS's actual export column names inside
`arms_data`, which we won't know until a real sync has run. `js/mock-data.js`
and the "MISSING:" field logic on the coach pages are still placeholder
data — wiring them to `player_arms_match` is the next step once there's
real data on both sides to look at.

### Player roster import (`scripts/import_players.py`) — local only

Reads a roster CSV and loads it into Supabase. **This script only ever
runs on your own machine** — the CSV (real recruit names) is never
committed to the repo and never touches GitHub Actions.

The table name comes straight from the filename: `sep_players.csv`
becomes table `sep_players`, `oct_players.csv` becomes `oct_players`, etc.
Coach codes in the CSV's `Coach` column are mapped to our coach ids in
`COACH_MAP` at the top of the script — currently `TC` → Ty, `KG` → Kiz,
`ABC` → Allie; update that dict if the codes ever change.

Fully automated — the script creates the table (with RLS/policies) itself
if it doesn't exist yet, and repoints `current_players` at it, using a
direct Postgres connection (`DATABASE_URL`, see below). No manual SQL
required. Safe to re-run: re-importing an updated CSV only touches roster
fields (name, coach, phone, rank, grad year, sort order) for players
already in the table — it never resets `queue_status`/`checked`/
`activated_at`/`contacted_at` for someone already being worked.

Each month is a clean start: nothing carries over automatically from the
previous month's table into the new one.

**Usage:**
```
cd scripts
python import_players.py ../data/sep_players.csv
```
Put the CSV anywhere outside git tracking — `data/` and
`scripts/*_players.csv` are both gitignored.

### Ingestion script (`scripts/`)

`scripts/fetch_and_push.py` pulls one combined "Full Info" export from
ARMS covering **both grad year 2028 and 2027** (`scripts/config.json`'s
`gradYear.selector` checks both boxes in one pass — the roster spans both
years) and upserts it into `arms`. It's adapted from
`arms_automation_27/fetch_and_push.py` — same ARMS login/navigation/export
logic, differences:
1. Writes to Supabase instead of Google Sheets.
2. Pulls both grad years in one export, not just 2027.
3. The ARMS export layout (`"2028 Full Info"`) has a **"Grad. Year"**
   column added to it, so each row's actual grad year is read from the
   export data itself rather than assumed from which filter box was
   checked — necessary once a single export can contain more than one
   grad year. `upsert_arms_rows` fails loudly if that column is ever
   missing from the layout.

`arms_automation_27`'s own pipeline (→ Google Sheets, class of 2027) is
untouched — this is a separate script/workflow living in this repo.

Confirmed working end-to-end with real data (1,991 rows landed in `arms`
from the 2028-only version of this export before the 2027/combined change;
re-verify row count after the next run picks up both years). Duplicate
`(grad_year, full_name)` rows in the real export are deduped automatically
before upserting. If ARMS ever renames the layout, the script fails with a
clear `layout '...' not found` error — update `layoutOptionText` in
`scripts/config.json` to match.

**Setup:**
1. Run `sql/players.sql` in the Supabase SQL editor (defines
   `player_arms_match`; needs `current_players` to already exist — run
   `import_players.py` at least once first, see above). `sql/arms.sql` is
   no longer required — the script creates that table itself now — but is
   kept as a reference for what the table looks like.
2. In this repo's GitHub Settings → Secrets and variables → Actions, add:
   `ARMS_USERNAME`, `ARMS_PASSWORD`, `ARMS_BASE_URL` (or `ARMS_LOGIN_URL`),
   `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (the **secret** key from
   Supabase → Settings → API — not the publishable key already in
   `js/config.js`), and `DATABASE_URL` (see "Direct database access" below).
3. `.github/workflows/arms-ingest.yml` runs on the 1st of each month at 7am
   UTC. For today's run, trigger it manually from the repo's Actions tab
   (`workflow_dispatch`) once the secrets above are set — it won't fire on
   its own outside the monthly schedule.

### Direct database access (`DATABASE_URL`)

`scripts/fetch_and_push.py`, `scripts/fetch_calendar.py`, and
`scripts/import_players.py` all create their own tables (and RLS
policies) automatically on first run — none of them need you to run SQL
by hand anymore for that part. The Supabase REST API
(`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`) can't run `CREATE TABLE`, so
this needs a direct Postgres connection instead:

Supabase dashboard → **Project Settings → Database → Connection string**
→ **Session pooler** tab → copy the URI, then substitute in your actual
database password (found/reset on that same page — this is a separate
password from any API key) in place of `[YOUR-PASSWORD]`.

Add the result as `DATABASE_URL`: in `scripts/.env` for local runs, and as
a GitHub Actions secret for `arms-ingest.yml`/`calendar-sync.yml`. Just as
sensitive as the service-role key — never in the frontend, never
committed.

### Calendar sync (`scripts/fetch_calendar.py`)

Pulls events from two named Google Calendars — **Staff** and **WBB** — into
a Supabase `calendar_events` table (`sql/calendar_events.sql`), so the site
can show real events instead of relying on Google's own embed widget.
Recurring events come back pre-expanded into individual instances over a
rolling window (60 days back, 180 days forward from "today") each time it
runs. Each row is tagged with `calendar_name` — always exactly `"Staff"` or
`"WBB"` (set by the script, not whatever Google's own calendar title is) —
so the site can filter/display them separately.

Uses the Calendar API with a service account rather than a calendar's iCal
link, since that link isn't available here (organization Workspace
calendar + not the owner). The service account only ever gets **read-only**
access — both by the Calendar API scope requested (`calendar.readonly`)
and by the sharing permission each calendar's owner grants it ("See all
event details," not an edit permission) — it cannot create, modify, or
delete anything on any calendar.

Adding a third calendar later: give it a name, add a `FOO_CALENDAR_ID`
secret for it, and add `"Foo": os.getenv("FOO_CALENDAR_ID")` to
`CALENDAR_SOURCES` at the top of `fetch_calendar.py`.

**Setup:**
1. In [Google Cloud Console](https://console.cloud.google.com): create/pick
   a project → enable the **Google Calendar API** → **IAM & Admin → Service
   Accounts** → create one (no project role needed) → its **Keys** tab →
   **Add Key → Create new key → JSON**. That downloads the credential file.
2. For each calendar (Staff, WBB): ask its owner to share it with the
   service account's email (looks like
   `xxx@your-project.iam.gserviceaccount.com`), permission **"See all event
   details"** (view-only). Then copy its **Calendar ID** (that calendar's
   settings → Integrate calendar → Calendar ID — not the iCal address).
3. `sql/calendar_events.sql` is no longer required — the script creates
   that table itself — but is kept as a reference for what it looks like.
4. Add secrets to this repo's GitHub Actions secrets:
   - `GOOGLE_CALENDAR_SA_JSON` — paste the **entire contents** of the
     downloaded JSON key file. Never commit that JSON file anywhere in
     this repo.
   - `STAFF_CALENDAR_ID` and `WBB_CALENDAR_ID` — each calendar's own ID
   - `DATABASE_URL` — see "Direct database access" above
     from step 2.
5. `.github/workflows/calendar-sync.yml` runs every 6 hours, or trigger it
   manually from the Actions tab.

Not wired into any page yet — once real rows land in `calendar_events`,
that's the next thing to build into a coach page (or a shared events view).

## Local preview

Any static file server works, e.g.:
```
python3 -m http.server 8000
```
then open http://localhost:8000
