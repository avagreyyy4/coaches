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

  const MISSING_FIELD_POOL = [
    "transcript", "address", "phone number", "email", "GPA", "highlight tape",
  ];

  const HISTORY_DAYS = 45; // how far back the mock log/chain extends

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

  // Picks 1–3 fields (out of the pool) that this placeholder player is
  // missing, deterministically per player.
  function missingFieldsFor(rand) {
    const shuffled = MISSING_FIELD_POOL.slice();
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    const count = 1 + Math.floor(rand() * 3);
    return shuffled.slice(0, count);
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
      recruits.push({
        id: `${coachId}-${todayISO}-${i}`,
        name: `Player ${i + 1}`,
        missing: missingFieldsFor(rand),
        done: overrides[i] === true,
      });
    }

    const done = recruits.filter((r) => r.done).length;

    return {
      today,
      todayTarget: target,
      todayDone: done,
      recruits,
    };
  };
})();
