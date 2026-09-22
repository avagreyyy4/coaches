// Eastern-time date helpers + the coach roster, shared by the home page and
// every coach page. Per-day calendar history is intentionally left empty —
// there's no real day-by-day log table yet (see js/data.js for what IS
// wired to real data: today's recruit queue + the season tracker).
(function () {
  window.COACHES = [
    { id: "ty", name: "Ty" },
    { id: "allie", name: "Allie" },
    { id: "kiz", name: "Kiz" },
  ];

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

  // No per-day history table yet — every day (including today) renders as
  // "no data" rather than a fabricated number.
  window.getCoachDayEntry = function () {
    return null;
  };
})();
