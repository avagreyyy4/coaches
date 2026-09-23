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

  window.getCoachDashboardData = async function (coachId) {
    const { data, error } = await window.supabaseClient
      .from("player_arms_match")
      .select("*")
      .eq("assigned_coach", coachId)
      .eq("queue_status", "active")
      .order("sort_order", { ascending: true });

    if (error) {
      console.error("[data] failed to load today's recruits:", error.message);
      return { todayTarget: 0, todayDone: 0, recruits: [] };
    }

    const recruits = data.map((row) => ({
      id: row.player_id,
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
    const totalReq = window.supabaseClient
      .from("current_players")
      .select("*", { count: "exact", head: true });
    const contactedReq = window.supabaseClient
      .from("current_players")
      .select("*", { count: "exact", head: true })
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
