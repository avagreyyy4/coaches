#!/usr/bin/env python3
# Run: HEADLESS=false python fetch_and_push.py
#
# Adapted from arms_automation_27/fetch_and_push.py. Only two things differ
# from that script:
#   1. Exports are written to Supabase (table `arms`) instead of Google
#      Sheets — see the SUPABASE HELPERS section below. `arms` holds the
#      full ARMS export; it's separate from `players`, the roster coaches
#      actually track, which gets manually entered and joined against
#      `arms` by full name (see sql/players.sql).
#   2. config.json pulls grad years 2027-2030 (the roster spans all four),
#      not just 2027 like the original script.
# Everything else (ARMS login, navigation, export/download, data cleanup) is
# unchanged from the original. Runs daily (see .github/workflows).

import asyncio, json, os, re, sys, tempfile
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_connection

load_dotenv()

# ===================== ENV =====================
ARMS_USER = os.getenv("ARMS_USERNAME") or os.getenv("ARMS_USER")
ARMS_PASS = os.getenv("ARMS_PASSWORD") or os.getenv("ARMS_PASS")
ARMS_BASE = (os.getenv("ARMS_BASE_URL") or "").rstrip("/")
ARMS_LOGIN_URL = os.getenv("ARMS_LOGIN_URL")
HEADLESS  = (os.getenv("HEADLESS", "true").lower() != "false")

SUPABASE_URL = os.getenv("SUPABASE_URL")
# Service-role key: bypasses RLS so this job can write. Must only ever live
# in GitHub Actions secrets / a local .env — never in the frontend, never
# committed.
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

missing = []
if not ARMS_USER: missing.append("ARMS_USERNAME/ARMS_USER")
if not ARMS_PASS: missing.append("ARMS_PASSWORD/ARMS_PASS")
if not (ARMS_BASE or ARMS_LOGIN_URL): missing.append("ARMS_BASE_URL or ARMS_LOGIN_URL")
if not SUPABASE_URL: missing.append("SUPABASE_URL")
if not SUPABASE_SERVICE_ROLE_KEY: missing.append("SUPABASE_SERVICE_ROLE_KEY")
if not os.getenv("DATABASE_URL"): missing.append("DATABASE_URL")
if missing:
    raise SystemExit(f"[fatal] Missing required env: {', '.join(missing)}")
if not ARMS_LOGIN_URL:
    ARMS_LOGIN_URL = f"{ARMS_BASE}"

# ===================== SUPABASE HELPERS =====================
_sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

def ensure_arms_table():
    """Idempotent: mirrors sql/arms.sql. Lets this script bootstrap a fresh
    Supabase project on its own, same as sql/arms.sql run by hand.

    Also migrates a table created before arms_id existed: two different
    recruits can share the same (grad_year, full_name) — that used to be
    the unique key, so one silently overwrote the other on every sync.
    arms_id (ARMS's own per-recruit `'ID` field) is the real unique key
    now; grad_year/full_name stay as plain, non-unique join columns."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                create table if not exists public.arms (
                  id uuid primary key default gen_random_uuid(),
                  arms_id text not null,
                  grad_year text not null,
                  full_name text not null,
                  data jsonb not null default '{}'::jsonb,
                  source_export text,
                  synced_at timestamptz not null default now(),
                  unique (arms_id)
                );
            """)
            cur.execute("alter table public.arms add column if not exists arms_id text;")
            cur.execute("update public.arms set arms_id = data->>$$'ID$$ where arms_id is null;")
            cur.execute("alter table public.arms alter column arms_id set not null;")
            cur.execute("alter table public.arms drop constraint if exists arms_grad_year_full_name_key;")
            cur.execute("""
                do $do$
                begin
                  if not exists (
                    select 1 from pg_constraint
                    where conrelid = 'public.arms'::regclass and conname = 'arms_arms_id_key'
                  ) then
                    alter table public.arms add constraint arms_arms_id_key unique (arms_id);
                  end if;
                end $do$;
            """)
            cur.execute("""
                create index if not exists arms_full_name_normalized_idx
                  on public.arms (lower(trim(full_name)));
            """)
            cur.execute("alter table public.arms enable row level security;")
            cur.execute('drop policy if exists "Authenticated coaches can read arms" on public.arms;')
            cur.execute("""
                create policy "Authenticated coaches can read arms"
                  on public.arms for select to authenticated using (true);
            """)
    finally:
        conn.close()


def _find_id_column(df: pd.DataFrame) -> str:
    """ARMS's own per-recruit ID column. Exported header is `'ID` (a
    leading apostrophe — likely ARMS forcing text formatting in Excel),
    so match on the stripped/normalized name rather than hardcoding that
    exact string in case ARMS changes the casing or quoting."""
    for col in df.columns:
        if col.strip().strip("'\"").strip().upper() == "ID":
            return col
    raise RuntimeError("Export has no 'ID' column — can't key rows for Supabase upsert.")

def upsert_arms_rows(df: pd.DataFrame, source_export: str):
    """
    Writes each row of the export into the `arms` table as:
      arms_id, grad_year, full_name, data (the full row as JSON), source_export, synced_at
    `grad_year` comes from the export's own "Grad. Year" column (added so a
    single combined export covering multiple grad years can still be tagged
    correctly per row) — NOT from which filter checkbox was used to pull
    the export, since a combined export can contain more than one year.
    `data` keeps every column ARMS gave us, since the exact CSV columns
    depend on the export layout ARMS is configured with and aren't hardcoded
    here. Upserts on `arms_id` — ARMS's own per-recruit ID — NOT
    (grad_year, full_name): two different recruits can share the same name
    in the same grad year (confirmed happening ~15-20 times per class),
    and keying on the name used to silently collapse them into one row.
    `full_name`/`grad_year` are kept as plain columns since `players` still
    joins against them by name (sql/players.sql), just no longer unique.
    """
    if "Full Name" not in df.columns:
        raise RuntimeError("Export has no 'Full Name' column — can't key rows for Supabase upsert.")
    if "Grad. Year" not in df.columns:
        raise RuntimeError("Export has no 'Grad. Year' column — add it to the ARMS export layout.")
    id_col = _find_id_column(df)

    # Keyed by arms_id so a duplicate row later in the export overwrites the
    # earlier one instead of both landing in the same upsert batch —
    # Postgres's ON CONFLICT DO UPDATE errors ("cannot affect row a second
    # time") if two rows in one batch target the same conflict key. Unlike
    # the old (grad_year, full_name) key, a true duplicate here means the
    # same recruit really did appear twice in the export.
    records_by_key = {}
    dupes = 0
    missing_grad_year = 0
    missing_id = 0
    for _, row in df.iterrows():
        full_name = str(row.get("Full Name", "")).strip()
        if not full_name:
            continue
        grad_year = str(row.get("Grad. Year", "")).strip()
        if not grad_year:
            missing_grad_year += 1
            continue
        arms_id = str(row.get(id_col, "")).strip().lstrip("'")
        if not arms_id:
            missing_id += 1
            continue
        if arms_id in records_by_key:
            dupes += 1
        records_by_key[arms_id] = {
            "arms_id": arms_id,
            "grad_year": grad_year,
            "full_name": full_name,
            "data": json.loads(row.to_json()),
            "source_export": source_export,
        }

    if dupes:
        print(f"[warn] {dupes} duplicate arms_id rows in export — kept the last one for each.")
    if missing_grad_year:
        print(f"[warn] {missing_grad_year} rows had no Grad. Year value — skipped.")
    if missing_id:
        print(f"[warn] {missing_id} rows had no ID value — skipped.")

    records = list(records_by_key.values())
    if not records:
        return 0

    _sb.table("arms").upsert(records, on_conflict="arms_id").execute()
    return len(records)

# ===================== UTILS / CACHE =====================
CACHE_PATH = Path(__file__).with_name(".exports_cache.json")

def _read_cache():
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}

def _write_cache(d):
    try:
        CACHE_PATH.write_text(json.dumps(d, indent=2))
    except Exception:
        pass

def _rx_startswith(s: str):
    return re.compile(rf"^\s*{re.escape(s)}\b", re.I)

def _layout_tokens(layout_text: str):
    toks = re.findall(r"[A-Za-z0-9]+", layout_text.lower())
    stop = {"the","of","for","and","a","an","to","by","in","on"}
    return [t for t in toks if t not in stop]

def _filename_matches_layout(fn: str, tokens):
    s = re.sub(r"[^a-z0-9]+", " ", fn.lower())
    return all(t in s for t in tokens)

# ===================== NAVIGATION / FILTERS =====================
def _rx_exact(s: str):
    return re.compile(rf"^\s*{re.escape(s)}\s*$", re.I)

async def click_recruiting_recruits(page):
    """From Dashboard left nav, open Recruiting → Recruits."""
    try:
        chevron = page.locator("button, [role='button']").filter(
            has_text=re.compile(r"^\s*Close Menu|Open Menu\s*$", re.I)
        ).first
        if await chevron.count():
            try: await chevron.click(timeout=800)
            except: pass
    except: pass

    for loc in [
        page.get_by_role("link", name=_rx_exact("Recruiting")).first,
        page.get_by_role("button", name=_rx_exact("Recruiting")).first,
        page.locator("nav,aside").get_by_text(_rx_exact("Recruiting")).first,
        page.locator("nav svg use[href*='recruiting-icon'], nav svg use[xlink\\:href*='recruiting-icon']").first
    ]:
        try:
            await loc.scroll_into_view_if_needed(); await loc.click(timeout=3000); break
        except: continue
    else:
        raise RuntimeError("Could not find 'Recruiting' in left navigation.")

    for loc in [
        page.get_by_role("link", name=_rx_exact("Recruits")).first,
        page.get_by_role("menuitem", name=_rx_exact("Recruits")).first,
        page.get_by_text(_rx_exact("Recruits")).first,
    ]:
        try:
            await loc.click(timeout=4000); break
        except: continue
    else:
        raise RuntimeError("Could not click 'Recruits' in the flyout.")

    await page.wait_for_load_state("networkidle")


async def _expand_section(scope, title_regex):
    try:
        hdr = scope.get_by_role("button", name=title_regex).first
        await hdr.wait_for(timeout=1200)
        expanded = await hdr.get_attribute("aria-expanded")
        if expanded is not None and expanded.lower() == "false":
            await hdr.click(); await scope.wait_for_load_state("networkidle"); await asyncio.sleep(0.1); return
    except: pass
    try:
        hdr2 = scope.locator(".mat-expansion-panel-header").filter(has=scope.get_by_text(title_regex)).first
        await hdr2.wait_for(timeout=1200)
        classes = (await hdr2.get_attribute("class")) or ""
        if "mat-expanded" not in classes:
            await hdr2.click(); await asyncio.sleep(0.1)
    except: pass

async def _click_link_in_section(scope, section_title_rx, link_text_rx):
    sec = scope.locator("section,div,aside").filter(has=scope.get_by_text(section_title_rx)).first
    try: await sec.wait_for(timeout=1000)
    except: sec = scope
    for loc in [sec.get_by_role("link", name=link_text_rx).first, sec.get_by_text(link_text_rx).first]:
        try:
            await loc.wait_for(timeout=600); await loc.click(); await asyncio.sleep(0.05); return True
        except: continue
    return False

async def _scroll_until_visible(scope, regex, max_steps=20):
    container = scope.locator(".mat-drawer-content, .mat-sidenav-content, .cdk-virtual-scroll-viewport").first
    for _ in range(max_steps):
        try:
            el = scope.get_by_text(regex).first
            await el.scroll_into_view_if_needed(); await el.wait_for(timeout=300); return True
        except:
            try: await container.evaluate("(el)=>el.scrollBy(0,300)")
            except: await scope.evaluate("()=>window.scrollBy(0,300)")
            await asyncio.sleep(0.05)
    return False

async def ensure_checkbox_checked(scope, name_regex):
    host = scope.locator("mat-checkbox").filter(has=scope.get_by_text(name_regex)).first
    try:
        if await host.count():
            classes = (await host.get_attribute("class")) or ""
            if "mat-checkbox-checked" in classes: return
            for tgt_sel in [".mat-checkbox-inner-container", "label", ".mat-checkbox-layout"]:
                tgt = host.locator(tgt_sel)
                try:
                    await tgt.scroll_into_view_if_needed(); await tgt.click(); return
                except: continue
            await host.click(force=True); return
    except: pass
    try:
        lbl = scope.get_by_label(name_regex).first
        await lbl.scroll_into_view_if_needed()
        try: await lbl.check()
        except: await lbl.click()
        return
    except: pass
    await scope.get_by_text(name_regex).first.click(force=True)

async def _is_checkbox_checked(scope, name_regex) -> bool:
    host = scope.locator("mat-checkbox").filter(has=scope.get_by_text(name_regex)).first
    try:
        if await host.count():
            classes = (await host.get_attribute("class")) or ""
            return "mat-checkbox-checked" in classes
    except: pass
    return False

async def find_filters_scope(page):
    try:
        await page.get_by_text(_rx_exact("Grad. Year")).first.wait_for(timeout=1200); return page
    except: pass
    for fr in page.frames:
        try:
            await fr.get_by_text(_rx_exact("Grad. Year")).first.wait_for(timeout=800); return fr
        except: continue
    return page

def _parse_statuses(exp: Dict) -> List[str]:
    f = exp.get("filters") or {}
    s = f.get("status", {})
    vals = s.get("values") or []
    if isinstance(vals, str):
        vals = [v.strip() for v in re.split(r"[,\|/]+", vals) if v.strip()]
    return vals

async def apply_filters(scope, grad_years: Optional[List[str]], statuses: Optional[List[str]] = None):
    await _expand_section(scope, _rx_exact("Status"))
    await _expand_section(scope, _rx_exact("Grad. Year"))

    if statuses:
        await _click_link_in_section(scope, _rx_exact("Status"), _rx_exact("none"))
        for s in statuses:
            rx_s = _rx_exact(s)
            await ensure_checkbox_checked(scope, rx_s)
            if not await _is_checkbox_checked(scope, rx_s):
                await ensure_checkbox_checked(scope, rx_s)  # retry once
            if not await _is_checkbox_checked(scope, rx_s):
                raise RuntimeError(
                    f"Status filter: could not check '{s}' after retry — not found on ARMS "
                    f"or the click didn't register. Aborting rather than exporting with it silently missing."
                )
    else:
        await _click_link_in_section(scope, _rx_exact("Status"), _rx_exact("all"))

    if grad_years:
        await _click_link_in_section(scope, _rx_exact("Grad. Year"), re.compile(r"^\s*none\s*$", re.I))
        for grad_year in grad_years:
            rx_year = _rx_startswith(grad_year)
            await _scroll_until_visible(scope, rx_year)
            await ensure_checkbox_checked(scope, rx_year)
            if not await _is_checkbox_checked(scope, rx_year):
                # Retry once: virtual-scroll lists can reset/reflow scroll position
                # right after a click, so re-search from the top instead of assuming
                # the year doesn't exist.
                try:
                    container = scope.locator(
                        ".mat-drawer-content, .mat-sidenav-content, .cdk-virtual-scroll-viewport"
                    ).first
                    await container.evaluate("(el)=>el.scrollTo(0,0)")
                except: pass
                await asyncio.sleep(0.2)
                await _scroll_until_visible(scope, rx_year)
                await ensure_checkbox_checked(scope, rx_year)
            if not await _is_checkbox_checked(scope, rx_year):
                raise RuntimeError(
                    f"Grad. Year filter: could not check '{grad_year}' after retry — not found on ARMS "
                    f"or the click didn't register. Aborting rather than exporting with it silently missing."
                )

def add_social_urls(df: pd.DataFrame) -> pd.DataFrame:
    if "Twitter" in df.columns:
        df["Twitter"] = (
            df["Twitter"].fillna("").astype(str).str.strip().str.lstrip("@")
            .apply(lambda x: f"https://twitter.com/{x}" if x else "")
        )
    if "Instagram" in df.columns:
        df["Instagram"] = (
            df["Instagram"].fillna("").astype(str).str.strip().str.lstrip("@")
            .apply(lambda x: f"https://instagram.com/{x}" if x else "")
        )
    return df

def add_full_name_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "First Name" in df.columns and "Last Name" in df.columns:
        full_name = (
            df["First Name"].fillna("").astype(str).str.strip() + " " +
            df["Last Name"].fillna("").astype(str).str.strip()
        ).str.strip()
        insert_at = df.columns.get_loc("Last Name") + 1
        df.insert(insert_at, "Full Name", full_name)
        df = df.drop(columns=["First Name", "Last Name"], errors="ignore")

    parent_fields = [
        "Mother's First Name", "Mother's Last Name",
        "Father's First Name", "Father's Last Name",
    ]
    if any(col in df.columns for col in parent_fields):
        mother_first = df.get("Mother's First Name", "").fillna("").astype(str).str.strip()
        mother_last  = df.get("Mother's Last Name", "").fillna("").astype(str).str.strip()
        mother_full = (mother_first + " " + mother_last).str.strip()

        father_first = df.get("Father's First Name", "").fillna("").astype(str).str.strip()
        father_last  = df.get("Father's Last Name", "").fillna("").astype(str).str.strip()
        father_full = (father_first + " " + father_last).str.strip()

        insert_at = df.columns.get_loc("Full Name") + 1 if "Full Name" in df.columns else len(df.columns)
        df.insert(insert_at, "Mother Full Name", mother_full)
        df.insert(insert_at + 1, "Father Full Name", father_full)
        df = df.drop(columns=parent_fields, errors="ignore")

    return df

def clean_mobile_numbers(df: pd.DataFrame) -> pd.DataFrame:
    phone_cols = [col for col in df.columns if re.search(r'mobile|cell|phone', col, flags=re.IGNORECASE)]
    for col in phone_cols:
        mask = df[col].notna()
        df.loc[mask, col] = (
            df.loc[mask, col].astype(str).str.strip().str.replace(r'^\+', '', regex=True)
        )
    return df

# ===================== EXPORT FLOW =====================

async def open_right_kebab_and_click_export(page):
    trigger_selectors = [
        "button[aria-haspopup='menu']",
        "button[aria-label*='menu' i]",
        "button[title*='menu' i]",
        "div[role='toolbar'] button",
        "header button",
    ]
    triggers = page.locator(",".join(trigger_selectors))

    bulk_icon = page.locator("mat-icon[aria-label*='Bulk Update Menu' i]").first
    try:
        if await bulk_icon.count():
            btn_from_icon = bulk_icon.locator("xpath=ancestor::button[1]")
            triggers = triggers.union(btn_from_icon)
    except AttributeError:
        pass

    if await triggers.count() == 0:
        raise RuntimeError("Hamburger/3-line menu not found")

    right_idx, right_x = 0, -1
    for i in range(await triggers.count()):
        el = triggers.nth(i)
        try:
            await el.wait_for(timeout=1500)
            box = await el.bounding_box()
            if box and box["y"] < 4000 and box["x"] > right_x:
                right_x, right_idx = box["x"], i
        except:
            continue
    menu_btn = triggers.nth(right_idx)

    async def _open_menu_and_click_export():
        await menu_btn.scroll_into_view_if_needed()
        await menu_btn.click(force=True)

        panel = page.locator(".cdk-overlay-pane:has(.mat-menu-content), [role='menu']").last
        await panel.wait_for(timeout=3000)

        candidates = [
            panel.locator('[data-cy="export"]').first,
            panel.locator('button[role="menuitem"][data-cy="export"]').first,
            panel.get_by_role("menuitem", name=_rx_exact("Export")).first,
            panel.get_by_role("button",   name=_rx_exact("Export")).first,
            panel.locator(".mat-menu-content .mat-menu-item:has-text('Export')").first,
            panel.locator("text=Export").first,
        ]

        for el in candidates:
            try:
                if await el.count() and await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await el.click(timeout=1500)
                    await page.wait_for_load_state("networkidle")
                    return True
            except:
                continue

        try:
            items = panel.locator("[role='menuitem'], .mat-menu-content .mat-menu-item, .mat-menu-content a")
            texts = []
            n = await items.count()
            for i in range(min(n, 20)):
                try:
                    t = (await items.nth(i).inner_text()).strip()
                    if t: texts.append(t)
                except:
                    pass
            if texts:
                print("[debug] hamburger menu items:", " | ".join(texts))
        except:
            pass

        try: await page.keyboard.press("Escape")
        except: pass
        await asyncio.sleep(0.15)
        return False

    for _ in range(3):
        if await _open_menu_and_click_export():
            return

    raise RuntimeError("Export option not found after opening menu")


async def open_export_and_start_job(layout_text: str, page):
    dropdown = None
    for loc in [
        page.locator("#exportLayout"),
        page.get_by_role("combobox").filter(has_text=re.compile("Export Layout|Layout", re.I)).first,
        page.get_by_role("button", name=re.compile(r"Export Layout|Select layout|Layout", re.I)).first,
        page.get_by_label(re.compile(r"Export Layout|Layout", re.I)).first,
    ]:
        try:
            await loc.wait_for(timeout=5000); dropdown = loc; break
        except: continue
    if not dropdown:
        raise RuntimeError("Export modal: layout dropdown not found.")

    await dropdown.scroll_into_view_if_needed(); await dropdown.click()
    picked = False
    for finder in [
        lambda: page.get_by_role("option",   name=_rx_exact(layout_text)).first,
        lambda: page.get_by_role("menuitem", name=_rx_exact(layout_text)).first,
        lambda: page.get_by_text(            _rx_exact(layout_text)).first,
    ]:
        try:
            await finder().click(timeout=5000); picked = True; break
        except: continue
    if not picked:
        raise RuntimeError(f"Export modal: layout '{layout_text}' not found.")

    export_btn_candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*Export\b.*", re.I)).first,
        page.locator("button[type='submit']").first,
        page.locator("button.k-button--primary, button.mat-primary").filter(has_text=re.compile(r"^\s*Export\b", re.I)).first,
        page.get_by_text(re.compile(r"^\s*Export\b.*", re.I)).first,
    ]
    for btn in export_btn_candidates:
        try:
            await btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(200)
            await btn.click(timeout=5000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.5)
            return
        except: continue
    raise RuntimeError("Export modal: could not find/click the Export button.")

async def maybe_go_to_exports_prompt(page):
    for finder in [
        lambda: page.get_by_role("button", name=re.compile(r"^\s*Take me to Exports page\s*$", re.I)).first,
        lambda: page.get_by_text(re.compile(r"^\s*Take me to Exports page\s*$", re.I)).first,
        lambda: page.get_by_role("button", name=re.compile(r"^\s*Go to Exports\s*$", re.I)).first,
        lambda: page.get_by_role("link",   name=re.compile(r"^\s*Go to Exports\s*$", re.I)).first,
        lambda: page.get_by_text(re.compile(r"^\s*Go to Exports\s*$", re.I)).first,
        lambda: page.get_by_role("button", name=re.compile(r"^\s*Go to Export(s)? Page\s*$", re.I)).first,
        lambda: page.get_by_text(re.compile(r"^\s*Go to Export(s)? Page\s*$", re.I)).first,
    ]:
        try:
            await finder().click(timeout=2000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.3)
            return True
        except:
            continue
    return False

async def disable_auto_refresh_if_present(page):
    try:
        block = page.locator("text=This page will auto-refresh").first
        await block.wait_for(timeout=2000)
        container = block.locator("xpath=..")
        toggle = container.locator("button, [role='button']").filter(has=page.locator("svg")).first
        pressed = await toggle.get_attribute("aria-pressed")
        if pressed is None or pressed.lower() == "true":
            await toggle.click(timeout=1500)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.25)
    except:
        pass


async def fetch_latest_export_from_admin(page, layout_text: str, timeout_s: int = 180, skip_if_same=True):
    tokens = _layout_tokens(layout_text)

    url = page.url.lower()
    if "admin" not in url or "export" not in url:
        for step in [
            lambda: page.get_by_text(_rx_exact("Administration")).first.click(timeout=3000),
            lambda: page.get_by_role("link", name=re.compile(r"Administration", re.I)).first.click(timeout=3000),
        ]:
            try:
                await step(); await page.wait_for_load_state("networkidle"); break
            except: pass
        for step in [
            lambda: page.get_by_text(_rx_exact("Exports")).first.click(timeout=3000),
            lambda: page.get_by_role("link", name=re.compile(r"Exports", re.I)).first.click(timeout=3000),
        ]:
            try:
                await step(); await page.wait_for_load_state("networkidle"); break
            except: pass

    await disable_auto_refresh_if_present(page)

    try:
        submit_hdr = page.locator("table thead th").filter(
            has=page.get_by_text(re.compile(r"^\s*Submit\s*Date\s*$", re.I))
        ).first
        await submit_hdr.click(timeout=1500)
        await page.wait_for_load_state("networkidle")
        await submit_hdr.click(timeout=1500)
        await page.wait_for_load_state("networkidle")
    except:
        pass

    file_col_idx = None
    try:
        ths = page.locator("table thead th"); n_th = await ths.count()
        for i in range(n_th):
            t = (await ths.nth(i).inner_text()).strip().lower()
            if "file" in t and "data" in t:
                file_col_idx = i
                break
    except:
        pass

    async def _find_newest_complete():
        body_rows = page.locator("table tbody tr")
        n = await body_rows.count()
        for i in range(n):
            row = body_rows.nth(i)
            try:
                await row.get_by_text(re.compile(r"\bComplete(d)?\b", re.I)).first.wait_for(timeout=250)
            except:
                continue

            if file_col_idx is not None:
                cell = row.locator("td").nth(file_col_idx)
                link = cell.locator("a").first
            else:
                link = row.locator("a").first

            try:
                fn = (await link.inner_text()).strip()
            except:
                continue

            if not fn or not _filename_matches_layout(fn, tokens):
                continue

            return row, link, fn
        return None

    end = asyncio.get_event_loop().time() + timeout_s
    found = None
    while asyncio.get_event_loop().time() < end:
        found = await _find_newest_complete()
        if found:
            break
        await asyncio.sleep(1.0)

    if not found:
        raise RuntimeError(f"Exports: no COMPLETE file found for layout '{layout_text}' within timeout.")

    row, link_el, filename = found

    cache = _read_cache() if skip_if_same else {}
    if skip_if_same and cache.get(layout_text) == filename:
        print(f"[info] latest file for '{layout_text}' already processed: {filename}")
        return pd.DataFrame()
    if skip_if_same:
        cache[layout_text] = filename
        _write_cache(cache)

    async with page.expect_download() as dl_ctx:
        await link_el.click()
    download = await dl_ctx.value

    with tempfile.TemporaryDirectory() as td:
        path = await download.path()
        if path is None:
            save_to = os.path.join(td, download.suggested_filename or filename or "export.csv")
            await download.save_as(save_to); path = save_to
        try:
            return pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            return pd.read_csv(path, dtype=str)

async def start_export_from_admin(layout_text: str, page):
    for step in [
        lambda: page.get_by_role("link", name=re.compile(r"Administration", re.I)).first.click(timeout=3000),
        lambda: page.get_by_text(re.compile(r"^\s*Administration\s*$", re.I)).first.click(timeout=3000),
    ]:
        try:
            await step(); await page.wait_for_load_state("networkidle"); break
        except: pass
    for step in [
        lambda: page.get_by_role("link", name=re.compile(r"Exports", re.I)).first.click(timeout=3000),
        lambda: page.get_by_text(re.compile(r"^\s*Exports\s*$", re.I)).first.click(timeout=3000),
    ]:
        try:
            await step(); await page.wait_for_load_state("networkidle"); break
        except: pass

    for sel in [
        "button[aria-label*='Menu']",
        "button[title*='Menu']",
        "button:has(svg)",
        "button:has-text('≡')",
        "button:has(.kebab), button:has(.hamburger)",
    ]:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(timeout=5000)
            await btn.scroll_into_view_if_needed()
            await btn.click()
            await page.wait_for_timeout(1000)
            break
        except Exception:
            continue
    else:
        raise RuntimeError("Hamburger/3-line menu not found")

    for sel in [
        "text=Export",
        "button:has-text('Export')",
        "div[role='menu'] >> text=Export",
    ]:
        try:
            export_btn = page.locator(sel).first
            await export_btn.wait_for(timeout=5000)
            await export_btn.click()
            await page.wait_for_load_state("networkidle")
            break
        except Exception:
            continue
    else:
        raise RuntimeError("Export option not found after opening menu")

    dropdown = None
    for loc in [
        page.locator("#exportLayout"),
        page.get_by_role("combobox").filter(has_text=re.compile("Export Layout|Layout", re.I)).first,
        page.get_by_role("button", name=re.compile(r"Export Layout|Select layout|Layout", re.I)).first,
        page.get_by_label(re.compile(r"Export Layout|Layout", re.I)).first,
    ]:
        try:
            await loc.wait_for(timeout=4000); await loc.click(); dropdown = loc; break
        except: continue
    if not dropdown:
        raise RuntimeError("Admin Export: layout selector not found.")

    picked = False
    for finder in [
        lambda: page.get_by_role("option",   name=_rx_exact(layout_text)).first,
        lambda: page.get_by_role("menuitem", name=_rx_exact(layout_text)).first,
        lambda: page.get_by_text(            _rx_exact(layout_text)).first,
    ]:
        try:
            el = finder(); await el.scroll_into_view_if_needed(); await el.click(timeout=4000); picked = True; break
        except: continue
    if not picked:
        raise RuntimeError(f"Admin Export: layout '{layout_text}' not found.")

    for btn in [
        page.get_by_role("button", name=re.compile(r"^\s*Export\b", re.I)).first,
        page.locator("button[type='submit']").first,
        page.locator("button.k-button--primary, button.mat-primary").filter(has_text=re.compile(r"^\s*Export\b", re.I)).first,
    ]:
        try:
            await btn.scroll_into_view_if_needed(); await page.wait_for_timeout(200)
            await btn.click(timeout=4000); await page.wait_for_load_state("networkidle"); return
        except: continue
    raise RuntimeError("Admin Export: could not click the final Export button.")

# ===================== CORE FLOW =====================
def _parse_grad_years(exp: Dict) -> List[str]:
    """
    Reads filters.gradYear.selector, which may be a single year string
    ("2028") or a list of years (["2027", "2028"]) for a combined export.
    Returns every 4-digit year found, in order, deduped.
    """
    f = exp.get("filters") or {}
    sel = (f.get("gradYear") or {}).get("selector", "")
    items = sel if isinstance(sel, list) else [sel]
    years = []
    for item in items:
        for m in re.findall(r"\b(20\d{2}|19\d{2})\b", str(item)):
            if m not in years:
                years.append(m)
    return years

async def do_one_export(page, exp: Dict):
    name = exp.get("name", "Unnamed")
    label = exp.get("label", name)
    grad_years = _parse_grad_years(exp)
    layout_text = exp.get("export", {}).get("layoutOptionText") or name.replace("_", " ")
    print(f"\n=== Export: {name} (grad years {grad_years or 'all'}) ===", flush=True)

    try:
        await page.get_by_role("button", name=_rx_exact("Cancel")).first.click(timeout=800)
        await page.wait_for_load_state("networkidle")
    except:
        pass

    await click_recruiting_recruits(page)

    scope = await find_filters_scope(page)
    # Deliberately not caught here: a filter that silently failed to apply
    # would otherwise export (and upsert) an incomplete dataset with no
    # visible error. Let it propagate so the whole export is skipped — the
    # caller's per-export try/except in run() reports it as [error].
    await apply_filters(scope, grad_years, _parse_statuses(exp))

    try:
        await open_right_kebab_and_click_export(page)
        await open_export_and_start_job(layout_text, page)
        await maybe_go_to_exports_prompt(page)
    except Exception as e:
        print(f"[warn] hamburger path failed: {e} — falling back to Admin → Exports")
        await start_export_from_admin(layout_text, page)

    df = await fetch_latest_export_from_admin(page, layout_text, skip_if_same=False)
    if df is None or df.empty:
        print(f"[info] No new rows for '{layout_text}' (skipped).")
        return

    df = clean_mobile_numbers(df)
    df = add_full_name_columns(df)
    df = add_social_urls(df)

    try:
        n = upsert_arms_rows(df, label)
        print(f"[info] upserted {n:,} rows into Supabase 'arms' (source_export={label})")
    except Exception as e:
        print(f"[error] failed to write to Supabase for {name}: {e}")


async def run():
    ensure_arms_table()

    cfg_path = Path(__file__).with_name("config.json")
    with cfg_path.open() as f:
        config = json.load(f)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900})
        page = await context.new_page()

        print("[info] Logging into ARMS ...")
        await page.goto(ARMS_LOGIN_URL, wait_until="load")
        print("[debug] at URL:", page.url)

        try:
            await page.get_by_label(re.compile(r"Email|Username", re.I)).first.fill(ARMS_USER)
        except:
            await page.locator('input[type="email"], input[name*="user" i], input[type="text"]').first.fill(ARMS_USER)

        try:
            btn_next = page.get_by_role("button", name=_rx_exact("Next")).first
            if await btn_next.count():
                await btn_next.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(800)
        except:
            pass

        async def _find_password_locator():
            candidates = [
                page.get_by_label(re.compile(r"Password", re.I)).first,
                page.locator('input[type="password"]').first,
                page.locator('input[name*="pass" i]').first,
            ]
            for loc in candidates:
                try:
                    await loc.wait_for(timeout=6000)
                    return loc
                except:
                    pass
            for fr in page.frames:
                candidates = [
                    fr.get_by_label(re.compile(r"Password", re.I)).first,
                    fr.locator('input[type="password"]').first,
                    fr.locator('input[name*="pass" i]').first,
                ]
                for loc in candidates:
                    try:
                        await loc.wait_for(timeout=4000)
                        return loc
                    except:
                        pass
            return None

        pwd = await _find_password_locator()
        if not pwd:
            try:
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(400)
                pwd = await _find_password_locator()
            except:
                pass

        if not pwd:
            raise RuntimeError("Could not find password field after waiting")

        await pwd.fill(ARMS_PASS)

        submitted = False
        for b in [
            page.get_by_role("button", name=re.compile(r"Sign in|Log in|Login", re.I)).first,
            page.locator('button[type="submit"]').first,
        ]:
            try:
                await b.click(timeout=4000)
                submitted = True
                break
            except:
                continue
        if not submitted:
            try:
                await pwd.press("Enter")
            except:
                pass

        await page.wait_for_load_state("networkidle")
        print("[info] Login complete.")

        for exp in config.get("exports", []):
            try:
                await do_one_export(page, exp)
            except Exception as e:
                print(f"[error] export failed for {exp.get('name','Unnamed')}: {e}")

        print("\n[done] All exports processed.")
        await context.close(); await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
