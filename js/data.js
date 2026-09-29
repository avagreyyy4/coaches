// Real Supabase-backed data: today's recruit queue per coach, checking a
// recruit off, the season-wide tracker, and synced calendar events.
(function () {
  // Keep in sync with scripts/nightly_queue.py's copy — separate runtimes,
  // no shared source of truth.
  window.DAILY_QUOTA = 7;

  // A transcript on file from before this date is a stale pre-junior-year
  // transcript and still counts as missing. Missing-info flags only apply
  // to the 2028 class at all — 2027 is never flagged.
  const TRANSCRIPT_STALE_CUTOFF = new Date(2026, 5, 15); // June 15, 2026

  function parseTranscriptDate(mmddyyyy) {
    const [m, d, y] = mmddyyyy.split("/").map(Number);
    return new Date(y, m - 1, d);
  }

  function missingFieldsFor(row) {
    if (row.grad_year !== "2028") return [];
    const arms = row.arms_data || {};
    const missing = [];
    if (!arms["Email"]) missing.push("email");
    if (!row.mobile_phone) missing.push("phone number");
    if (!arms["Street 1"] && !arms["Mailing Address"]) missing.push("home address");
    const hasTranscript = arms["Has Transcript"];
    if (!hasTranscript || parseTranscriptDate(hasTranscript) <= TRANSCRIPT_STALE_CUTOFF) {
      missing.push("transcript");
    }
    return missing;
  }

  // Notes are shared across coaches and keyed by normalized full name so
  // they survive the monthly roster tables (see sql/player_notes.sql).
  function noteKey(fullName) {
    return (fullName || "").trim().toLowerCase();
  }

  async function loadNotes(keys) {
    if (!keys.length) return {};
    const { data, error } = await window.supabaseClient
      .from("player_notes")
      .select("player_key,note")
      .in("player_key", keys);
    if (error) {
      console.error("[data] failed to load notes:", error.message);
      return {};
    }
    return Object.fromEntries(data.map((r) => [r.player_key, r.note]));
  }

  // Returns true if the save landed.
  window.setPlayerNote = async function (key, note) {
    const { data: { session } } = await window.supabaseClient.auth.getSession();
    const { error } = await window.supabaseClient.from("player_notes").upsert({
      player_key: key,
      note,
      updated_by: session && session.user ? session.user.email : null,
      updated_at: new Date().toISOString(),
    });
    if (error) console.error("[data] failed to save note:", error.message);
    return !error;
  };

  window.getCoachDashboardData = async function (coachId) {
    const { data, error } = await window.supabaseClient
      .from("player_arms_match")
      .select("*")
      .eq("assigned_coach", coachId)
      .eq("queue_status", "active")
      .eq("arms_removed", false)
      .order("sort_order", { ascending: true });

    if (error) {
      console.error("[data] failed to load today's recruits:", error.message);
      return { todayTarget: 0, todayDone: 0, recruits: [] };
    }

    const notes = await loadNotes(data.map((row) => noteKey(row.full_name)));

    const recruits = data.map((row) => ({
      id: row.player_id,
      note: notes[noteKey(row.full_name)] || "",
      noteKey: noteKey(row.full_name),
      firstName: row.first_name,
      lastName: row.last_name,
      gradYear: row.grad_year,
      acsRank: row.acs_rank,
      phone: row.mobile_phone,
      done: row.checked,
      missing: missingFieldsFor(row),
    }));

    return {
      todayTarget: recruits.length,
      todayDone: recruits.filter((r) => r.done).length,
      recruits,
    };
  };

  // playerId is the real current_players row id (a uuid), not a list index.
  window.setRecruitDone = async function (coachId, playerId, done) {
    const { error } = await window.supabaseClient
      .from("current_players")
      .update({ checked: done })
      .eq("id", playerId);
    if (error) console.error("[data] failed to save checked state:", error.message);
  };

  // "Reached out to" = contacted for good (swept at 1am), or checked today
  // and just waiting on tonight's sweep to make that official. Both count
  // the same player once, so this number doesn't jump when the sweep runs
  // — it just becomes durable.
  window.getSeasonProgress = async function () {
    // Read through player_arms_match so players ARMS has marked out
    // (Dropped / NFU / GradesLow) don't count toward either number.
    const totalReq = window.supabaseClient
      .from("player_arms_match")
      .select("*", { count: "exact", head: true })
      .eq("arms_removed", false);
    const contactedReq = window.supabaseClient
      .from("player_arms_match")
      .select("*", { count: "exact", head: true })
      .eq("arms_removed", false)
      .or("queue_status.eq.contacted,and(queue_status.eq.active,checked.eq.true)");

    const [{ count: total, error: totalErr }, { count: contacted, error: contactedErr }] =
      await Promise.all([totalReq, contactedReq]);

    if (totalErr || contactedErr) {
      console.error("[data] failed to load season progress:", (totalErr || contactedErr).message);
      return { contacted: 0, total: 0 };
    }
    return { contacted, total };
  };

  // Events with start_at in [startDate, endDateExclusive), grouped by the
  // calendar day (YYYY-MM-DD) callers key their day columns with (see
  // window.isoDate in calendar.js — local-Date-based, same basis the
  // calendar grid itself uses for day boundaries).
  //
  // all-day events are the one exception: fetch_calendar.py deliberately
  // stores them as literal UTC midnight of that calendar day (not a real
  // instant), so bucketing those by local time would shift them a day
  // early anywhere west of UTC. They're bucketed by their stored UTC date
  // directly instead; timed events are converted to local time first.
  window.getCalendarEvents = async function (startDate, endDateExclusive) {
    const { data, error } = await window.supabaseClient
      .from("calendar_events")
      .select("calendar_name,title,start_at,end_at,all_day")
      .gte("start_at", startDate.toISOString())
      .lt("start_at", endDateExclusive.toISOString())
      .order("start_at", { ascending: true });

    if (error) {
      console.error("[data] failed to load calendar_events:", error.message);
      return {};
    }

    const byDate = {};
    data.forEach((ev) => {
      const iso = ev.all_day ? ev.start_at.slice(0, 10) : window.isoDate(new Date(ev.start_at));
      (byDate[iso] || (byDate[iso] = [])).push(ev);
    });
    return byDate;
  };
})();
