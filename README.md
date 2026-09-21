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

## Data architecture: two Supabase tables + a join

- **`arms`** (`sql/arms.sql`) — the full ARMS export for grad year 2028,
  refreshed monthly by `scripts/fetch_and_push.py`. This is ARMS's entire
  recruit pool, not just players you're tracking.
- **`players`** (`sql/players.sql`) — the actual roster: the list you enter
  yourself from what coaches give you, one row per player with an
  `assigned_coach` (`ty` / `allie` / `kiz`). **Entering rows here is meant
  to happen directly in the Supabase Table Editor** — there's no in-app
  entry form yet. Say the word if you'd rather have a page for that instead.
- **`player_arms_match`** (a view, defined in `sql/players.sql`) — joins the
  two on normalized full name (`first_name + " " + last_name`, case/
  whitespace-insensitive) so each tracked player comes back enriched with
  whatever ARMS has on them (`arms_data`, plus `matched_in_arms` telling you
  if no ARMS row matched at all).

Which fields count as "missing," and which get highlighted (you mentioned
phone number and hometown/state specifically) still needs to be wired into
the frontend — that logic depends on ARMS's actual export column names
inside `arms_data`, which we won't know until a real sync has run. Once
today's manual trigger produces real rows, I can look at the actual JSON
keys and wire the coach pages to read from `player_arms_match` instead of
`js/mock-data.js`.

### Ingestion script (`scripts/`)

`scripts/fetch_and_push.py` pulls the grad-2028 "Full Info" export from ARMS
and upserts it into `arms`. It's adapted from
`arms_automation_27/fetch_and_push.py` — same ARMS login/navigation/export
logic, two differences:
1. Writes to Supabase instead of Google Sheets.
2. Targets grad year **2028** (`scripts/config.json`), not 2027.

`arms_automation_27`'s own pipeline (→ Google Sheets, class of 2027) is
untouched — this is a separate script/workflow living in this repo.

**Before this runs successfully, check in ARMS**: the export layout it asks
for is `"2028 Full Info"` (following the same naming pattern as the existing
`"2027 Full Info"` layout). If your ARMS admin hasn't created a 2028 version
of that export layout yet, the script will fail with a clear
`layout '2028 Full Info' not found` error — create it in ARMS first, or
update `layoutOptionText` in `scripts/config.json` to match whatever it's
actually called.

**Setup:**
1. Run `sql/arms.sql` then `sql/players.sql` in the Supabase SQL editor.
2. In this repo's GitHub Settings → Secrets and variables → Actions, add:
   `ARMS_USERNAME`, `ARMS_PASSWORD`, `ARMS_BASE_URL` (or `ARMS_LOGIN_URL`),
   `SUPABASE_URL`, and `SUPABASE_SERVICE_ROLE_KEY` (the **secret** key from
   Supabase → Settings → API — not the publishable key already in
   `js/config.js`, and never put the secret key in any frontend file).
3. `.github/workflows/arms-ingest.yml` runs on the 1st of each month at 7am
   UTC. For today's run, trigger it manually from the repo's Actions tab
   (`workflow_dispatch`) once the secrets above are set — it won't fire on
   its own outside the monthly schedule.

`js/mock-data.js` and the "MISSING:" field logic on the coach pages are
still placeholder data — wiring them to `player_arms_match` is the next
step once there's real data to look at.

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
3. Run `sql/calendar_events.sql` in the Supabase SQL editor.
4. Add secrets to this repo's GitHub Actions secrets:
   - `GOOGLE_CALENDAR_SA_JSON` — paste the **entire contents** of the
     downloaded JSON key file. Never commit that JSON file anywhere in
     this repo.
   - `STAFF_CALENDAR_ID` and `WBB_CALENDAR_ID` — each calendar's own ID
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
