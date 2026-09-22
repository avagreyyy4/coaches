// Placeholder data so the UI can be reviewed before real recruit data is
// wired up to Supabase. Every value here is fake — swap this whole file out
// once the real schema exists.
//
// Rules being mocked:
//   - Base daily quota is 5 recruits.
//   - If yesterday's quota wasn't fully reached, the leftover rolls onto
//     today: today's quota = 5 + (yesterday's quota - yesterday's done).
//   - "Today" is always figured in US Eastern time, regardless of the
//     viewer's own timezone, since that's where the coaching staff is.
(function () {
  window.COACHES = [
    { id: "ty", name: "Ty" },
    { id: "allie", name: "Allie" },
    { id: "kiz", name: "Kiz" },
  ];

  const FIRST_NAMES = ["Jordan", "Maya", "Skylar", "Reese", "Peyton", "Harper", "Avery", "Riley", "Sydney", "Kennedy"];
  const LAST_NAMES = ["Bennett", "Carver", "Doyle", "Ellison", "Foster", "Grady", "Holt", "Ibarra", "Jansen", "Kessler"];

  const HISTORY_DAYS = 45; // how far back the mock log/chain extends

  // Real ARMS field is "Has Transcript" (MM/DD/YYYY, or blank). A transcript
  // on file from before this date is a stale pre-junior-year transcript and
  // still counts as missing — mirrors the rule used once this is wired to
  // real data.
  const TRANSCRIPT_STALE_CUTOFF = new Date(2026, 5, 15); // June 15, 2026

  // Deterministic little PRNG so mock output is stable across reloads.
  function seededRandom(seed) {
    let t = seed + 0x6d2b79f5;
    return function () {
      t += 0x6d2b79f5;
      let r = Math.imul(t ^ (t >>> 15), 1 | t);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hashSeed(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
    return h;
  }

  function parseTranscriptDate(mmddyyyy) {
    const [m, d, y] = mmddyyyy.split("/").map(Number);
    return new Date(y, m - 1, d);
  }

  // Missing-info flagging only applies to the active (2028) class — 2027 is
  // not flagged at all. Transcript counts as missing if there's none on
  // file, or the one on file predates the stale cutoff above.
  function missingFieldsFor(recruit) {
    if (recruit.gradYear !== "2028") return [];
    const missing = [];
    if (!recruit.email) missing.push("email");
    if (!recruit.phone) missing.push("phone number");
    if (!recruit.homeAddress) missing.push("home address");
    if (!recruit.hasTranscriptDate || parseTranscriptDate(recruit.hasTranscriptDate) <= TRANSCRIPT_STALE_CUTOFF) {
      missing.push("transcript");
    }
    return missing;
  }

  // ---- Eastern-time "today", independent of the viewer's own timezone ----
  function easternDateParts(date) {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    const parts = {};
    fmt.formatToParts(date).forEach((p) => { parts[p.type] = p.value; });
    return { year: +parts.year, month: +parts.month - 1, day: +parts.day };
  }

  function easternToday() {
    const { year, month, day } = easternDateParts(new Date());
    return dateFromParts(year, month, day);
  }

  // Wall-clock local Date standing in for a specific Eastern calendar day —
  // used only for day arithmetic (adding/subtracting whole days), never for
  // an exact instant, so local DST quirks don't matter here.
  function dateFromParts(year, month, day) {
    return new Date(year, month, day);
  }

  function isoDate(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function addDays(date, n) {
    const d = new Date(date);
    d.setDate(d.getDate() + n);
    return d;
  }

  window.getEasternToday = easternToday;
  window.isoDate = isoDate;
  window.addDays = addDays;

  // ---- Checked-off state, persisted in localStorage so it carries across
  // pages (e.g. check a player on a coach's page, see it reflected on that
  // coach's home-page card too) — still mock/local-only, not Supabase.
  function doneStorageKey(coachId, todayISO) {
    return `coach-checklist-done:${coachId}:${todayISO}`;
  }

  function readDoneOverrides(coachId, todayISO) {
    try {
      const raw = localStorage.getItem(doneStorageKey(coachId, todayISO));
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  window.setRecruitDone = function (coachId, index, done) {
    const todayISO = isoDate(easternToday());
    const arr = readDoneOverrides(coachId, todayISO);
    arr[index] = done;
    try {
      localStorage.setItem(doneStorageKey(coachId, todayISO), JSON.stringify(arr));
    } catch (e) {
      // ignore (e.g. storage disabled/full) — falls back to in-memory only
    }
  };

  // ---- Per-coach rollover chain, cached per coach ----
  const chainCache = {};

  function buildChain(coachId) {
    if (chainCache[coachId]) return chainCache[coachId];

    const today = easternToday();
    const start = addDays(today, -HISTORY_DAYS);
    const rand = seededRandom(hashSeed(coachId));
    const log = {};
    let runningTarget = 5;
    let cursor = start;

    while (cursor <= today) {
      const key = isoDate(cursor);
      const target = runningTarget;
      let done;
      if (key === isoDate(today)) {
        done = Math.floor(rand() * target); // today is in progress
      } else {
        const hitRate = rand();
        done = hitRate > 0.35 ? target : Math.floor(rand() * target);
      }
      log[key] = { target, done };
      runningTarget = 5 + Math.max(0, target - done);
      cursor = addDays(cursor, 1);
    }

    chainCache[coachId] = log;
    return log;
  }

  // Returns { target, done } for a past/today ISO date, or null if the date
  // is in the future (no data yet) or before the mocked history window.
  window.getCoachDayEntry = function (coachId, date) {
    const log = buildChain(coachId);
    return log[isoDate(date)] || null;
  };

  // Today's summary + the list of recruits still to reach out to.
  // Placeholder: base target always 5, players named generically, each
  // flagged with whatever info is "missing" until real recruit data is
  // wired in. "done" state per player is read from localStorage so it's
  // consistent wherever it's shown (this page, the home card, etc.).
  window.getCoachDashboardData = function (coachId) {
    const today = easternToday();
    const todayISO = isoDate(today);
    const target = 5;
    const overrides = readDoneOverrides(coachId, todayISO);

    const rand = seededRandom(hashSeed(coachId + "-recruits-" + todayISO));
    const recruits = [];
    for (let i = 0; i < target; i++) {
      const gradYear = rand() < 0.55 ? "2028" : "2027";
      const hasPhone = rand() < 0.85;
      const hasEmail = rand() < 0.8;
      const hasAddress = rand() < 0.75;
      let hasTranscriptDate = null;
      if (rand() < 0.5) {
        hasTranscriptDate = rand() < 0.5 ? "03/12/2026" : "08/20/2026"; // before / after the stale cutoff
      }

      const recruit = {
        id: `${coachId}-${todayISO}-${i}`,
        firstName: FIRST_NAMES[Math.floor(rand() * FIRST_NAMES.length)],
        lastName: LAST_NAMES[Math.floor(rand() * LAST_NAMES.length)],
        phone: hasPhone ? `704-555-${String(1000 + i).slice(1)}` : null,
        gradYear,
        hasTranscriptDate,
        email: hasEmail,
        homeAddress: hasAddress,
        done: overrides[i] === true,
      };
      recruit.missing = missingFieldsFor(recruit);
      recruits.push(recruit);
    }

    const done = recruits.filter((r) => r.done).length;

    return {
      today,
      todayTarget: target,
      todayDone: done,
      recruits,
    };
  };

  // ---- Season-wide progress across all coaches (home page tracker) ----
  // Placeholder: TOTAL_ROSTER matches the real September roster (117 + 97 +
  // 81 = 295 across the 3 coaches). "Contacted" is a mock baseline that
  // grows day by day (deterministic, stable across reloads) plus whatever's
  // actually been checked off today (live, from localStorage) — so the
  // tracker visibly ticks up both as days pass and as players get checked.
  const TOTAL_ROSTER = 295;

  window.getSeasonProgress = function () {
    const today = easternToday();
    const dayOfMonth = today.getDate();
    const rand = seededRandom(hashSeed(`season-progress-${today.getFullYear()}-${today.getMonth()}`));

    const perDayAvg = TOTAL_ROSTER / 30;
    let baseline = 0;
    for (let d = 1; d < dayOfMonth; d++) {
      baseline += perDayAvg * (0.6 + rand() * 0.8);
    }
    baseline = Math.round(baseline);

    let todayChecked = 0;
    window.COACHES.forEach((coach) => {
      todayChecked += window.getCoachDashboardData(coach.id).todayDone;
    });

    const contacted = Math.min(baseline + todayChecked, TOTAL_ROSTER);
    return { contacted, total: TOTAL_ROSTER };
  };
})();
