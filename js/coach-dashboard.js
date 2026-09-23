// Renders a single coach's page: header, a flexible calendar (3-day / 5-day
// / month, navigable), and today's remaining-recruits list.
// Reads window.COACH_ID set inline on each coach page.
(function () {
  const container = document.getElementById("coach-dashboard");
  if (!container) return;

  const coach = window.COACHES.find((c) => c.id === window.COACH_ID);
  if (!coach) return;

  const state = {
    view: "5day", // "3day" | "5day" | "month" — defaults to a 5-day outlook
    anchor: null, // Date; for day views the leftmost visible day, for month view any day in the visible month
    todayStats: null, // { done, target } for today's real queue, set once render() loads it
  };

  // Matches the coach's abbreviation (e.g. "TC") as a whole word in an
  // event title, case-insensitively — so "TC Lift" matches but "STATIC"
  // doesn't just because it contains "TC".
  const coachCodePattern = new RegExp(`\\b${coach.name}\\b`, "i");
  function isCoachEvent(title) {
    return !!title && coachCodePattern.test(title);
  }

  async function render() {
    document.getElementById("coach-name").textContent = coach.name;

    state.anchor = window.getEasternToday();
    const data = await window.getCoachDashboardData(coach.id);
    state.todayStats = { done: data.todayDone, target: data.todayTarget };
    await renderCalendar();
    renderRecruits(data);
  }

  // ---- Calendar: toolbar wiring ----
  document.getElementById("cal-prev").addEventListener("click", () => {
    shiftAnchor(-1);
    renderCalendar();
  });
  document.getElementById("cal-next").addEventListener("click", () => {
    shiftAnchor(1);
    renderCalendar();
  });
  document.getElementById("cal-today").addEventListener("click", () => {
    state.anchor = window.getEasternToday();
    renderCalendar();
  });
  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.view = btn.dataset.view;
      state.anchor = window.getEasternToday();
      renderCalendar();
    });
  });

  function shiftAnchor(direction) {
    if (state.view === "month") {
      state.anchor = new Date(state.anchor.getFullYear(), state.anchor.getMonth() + direction, 1);
    } else {
      const count = state.view === "3day" ? 3 : 5;
      state.anchor = window.addDays(state.anchor, direction * count);
    }
  }

  async function renderCalendar() {
    document.querySelectorAll(".view-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === state.view);
    });

    const label = document.getElementById("calendar-period-label");
    const body = document.getElementById("calendar-body");

    // Snapshot which render this is so a slower-arriving fetch from a
    // superseded view/anchor can't clobber a newer one.
    const requestToken = (state.renderToken = (state.renderToken || 0) + 1);

    if (state.view === "month") {
      label.textContent = state.anchor.toLocaleDateString("en-US", { month: "long", year: "numeric" });
      const year = state.anchor.getFullYear();
      const month = state.anchor.getMonth();
      const rangeStart = new Date(year, month, 1);
      const rangeEnd = new Date(year, month + 1, 1);
      const eventsByDate = await window.getCalendarEvents(rangeStart, rangeEnd);
      if (requestToken !== state.renderToken) return;
      body.innerHTML = "";
      body.appendChild(buildMonthGrid(year, month, eventsByDate));
    } else {
      const count = state.view === "3day" ? 3 : 5;
      label.textContent = formatDayRangeLabel(state.anchor, count);
      const rangeStart = state.anchor;
      const rangeEnd = window.addDays(state.anchor, count);
      const eventsByDate = await window.getCalendarEvents(rangeStart, rangeEnd);
      if (requestToken !== state.renderToken) return;
      body.innerHTML = "";
      const timeGrid = buildTimeGrid(count, eventsByDate);
      body.appendChild(timeGrid.el);
      requestAnimationFrame(() => {
        // Open scrolled to roughly 8 AM rather than the top of the range.
        timeGrid.scrollEl.scrollTop = Math.max(0, (8 - timeGrid.startHour) * PX_PER_HOUR - 24);
      });
    }
  }

  function formatDayRangeLabel(start, count) {
    const end = window.addDays(start, count - 1);
    const opts = { month: "short", day: "numeric" };
    return `${start.toLocaleDateString("en-US", opts)} – ${end.toLocaleDateString("en-US", opts)}`;
  }

  // ---- Event chips (Google-Calendar-style soft pills) ----
  function buildEventChip(ev) {
    const chip = document.createElement("div");
    chip.className = "event-chip" + (isCoachEvent(ev.title) ? " event-chip-mine" : "");
    chip.textContent = ev.title || "(untitled)";
    chip.title = ev.title || "(untitled)";
    return chip;
  }

  function buildEventList(events, maxVisible) {
    const list = document.createElement("div");
    list.className = "day-events";
    if (!events || !events.length) return list;

    const limit = maxVisible || events.length;
    events.slice(0, limit).forEach((ev) => list.appendChild(buildEventChip(ev)));

    if (events.length > limit) {
      const more = document.createElement("div");
      more.className = "day-events-more";
      more.textContent = `+${events.length - limit} more`;
      list.appendChild(more);
    }

    return list;
  }

  // ---- Time grid (3-day / 5-day): events positioned + sized by time ----
  const PX_PER_HOUR = 48;
  const DEFAULT_START_HOUR = 6;
  const DEFAULT_END_HOUR = 21;
  const MIN_EVENT_MINUTES = 30; // floor for events with no/zero duration, so they stay visible
  const MIN_EVENT_PX = 16;

  function formatClock(d) {
    let h = d.getHours();
    const m = d.getMinutes();
    const ampm = h >= 12 ? "pm" : "am";
    h = h % 12;
    if (h === 0) h = 12;
    return m === 0 ? `${h}${ampm}` : `${h}:${String(m).padStart(2, "0")}${ampm}`;
  }

  function formatHourLabel(h) {
    const ampm = h >= 12 ? "PM" : "AM";
    let hh = h % 12;
    if (hh === 0) hh = 12;
    return `${hh} ${ampm}`;
  }

  // Greedy interval-overlap column assignment: events that overlap in time
  // share the day's width side by side (like Google Calendar), events that
  // don't overlap each get the full width.
  function layoutOverlaps(items) {
    const sorted = items.slice().sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
    let cluster = [];
    let clusterEnd = -Infinity;
    const clusters = [];
    sorted.forEach((item) => {
      if (cluster.length && item.startMin >= clusterEnd) {
        clusters.push(cluster);
        cluster = [];
        clusterEnd = -Infinity;
      }
      cluster.push(item);
      clusterEnd = Math.max(clusterEnd, item.endMin);
    });
    if (cluster.length) clusters.push(cluster);

    clusters.forEach((c) => {
      const colEnds = [];
      c.forEach((item) => {
        let col = colEnds.findIndex((end) => end <= item.startMin);
        if (col === -1) {
          col = colEnds.length;
          colEnds.push(item.endMin);
        } else {
          colEnds[col] = item.endMin;
        }
        item.col = col;
      });
      c.forEach((item) => { item.cols = colEnds.length; });
    });
    return sorted;
  }

  function buildTimeGrid(count, eventsByDate) {
    const days = [];
    for (let i = 0; i < count; i++) days.push(window.addDays(state.anchor, i));

    // Split timed vs all-day, and compute the grid's hour range — the
    // default 6 AM-9 PM window, extended to cover any timed event that
    // falls outside it so nothing gets clipped off the top/bottom.
    let startHour = DEFAULT_START_HOUR;
    let endHour = DEFAULT_END_HOUR;
    const timedByDay = days.map((d) => {
      const iso = window.isoDate(d);
      const dayEvents = eventsByDate[iso] || [];
      const timed = [];
      dayEvents.forEach((ev) => {
        if (ev.all_day) return;
        const start = new Date(ev.start_at);
        const end = ev.end_at ? new Date(ev.end_at) : new Date(start.getTime() + MIN_EVENT_MINUTES * 60000);
        let startMin = start.getHours() * 60 + start.getMinutes();
        let endMin = end.getHours() * 60 + end.getMinutes();
        if (end.getDate() !== start.getDate() || endMin <= startMin) endMin = 24 * 60;
        if (endMin - startMin < MIN_EVENT_MINUTES) endMin = startMin + MIN_EVENT_MINUTES;
        startHour = Math.min(startHour, Math.floor(startMin / 60));
        endHour = Math.max(endHour, Math.ceil(endMin / 60));
        timed.push({ ev, startMin, endMin });
      });
      return timed;
    });

    const totalHeight = (endHour - startHour) * PX_PER_HOUR;

    const wrap = document.createElement("div");
    wrap.className = "time-grid-wrap";
    wrap.style.setProperty("--tg-cols", String(count));

    // Header: weekday + date (today circled) + today's quota stat, one cell
    // per day, sharing the exact grid template the hour rows below use —
    // so headers sit precisely above their own column, not a separate card.
    const todayISO = window.isoDate(window.getEasternToday());
    const header = document.createElement("div");
    header.className = "time-grid-header";
    header.appendChild(document.createElement("div")); // gutter spacer
    days.forEach((d) => {
      const iso = window.isoDate(d);
      const cell = document.createElement("div");
      cell.className = "time-grid-day-header";

      const weekday = document.createElement("div");
      weekday.className = "tg-weekday";
      weekday.textContent = d.toLocaleDateString("en-US", { weekday: "short" });
      cell.appendChild(weekday);

      const dateEl = document.createElement("div");
      dateEl.className = "tg-date";
      dateEl.textContent = d.getDate();
      cell.appendChild(dateEl);

      const stat = document.createElement("div");
      stat.className = "tg-stat";
      if (iso === todayISO) {
        cell.classList.add("today");
        stat.textContent = state.todayStats ? `${state.todayStats.done} of ${state.todayStats.target}` : "—";
      } else {
        stat.textContent = "No data yet";
      }
      cell.appendChild(stat);

      header.appendChild(cell);
    });
    wrap.appendChild(header);

    // All-day row (only rendered if something's actually all-day this range)
    const hasAllDay = days.some((d) => (eventsByDate[window.isoDate(d)] || []).some((ev) => ev.all_day));
    if (hasAllDay) {
      const allDayRow = document.createElement("div");
      allDayRow.className = "time-grid-allday";
      const label = document.createElement("div");
      label.className = "time-grid-allday-label";
      label.textContent = "All day";
      allDayRow.appendChild(label);
      days.forEach((d) => {
        const iso = window.isoDate(d);
        const allDayEvents = (eventsByDate[iso] || []).filter((ev) => ev.all_day);
        const col = document.createElement("div");
        col.className = "time-grid-allday-col";
        allDayEvents.forEach((ev) => col.appendChild(buildEventChip(ev)));
        allDayRow.appendChild(col);
      });
      wrap.appendChild(allDayRow);
    }

    const scrollEl = document.createElement("div");
    scrollEl.className = "time-grid-scroll";

    const body = document.createElement("div");
    body.className = "time-grid-body";

    const hours = document.createElement("div");
    hours.className = "time-grid-hours";
    hours.style.height = `${totalHeight}px`;
    for (let h = startHour; h <= endHour; h++) {
      const lbl = document.createElement("div");
      lbl.className = "time-grid-hour-label";
      lbl.style.top = `${(h - startHour) * PX_PER_HOUR}px`;
      lbl.textContent = formatHourLabel(h);
      hours.appendChild(lbl);
    }
    body.appendChild(hours);

    timedByDay.forEach((dayTimed) => {
      const col = document.createElement("div");
      col.className = "time-grid-col";
      col.style.height = `${totalHeight}px`;

      const laidOut = layoutOverlaps(dayTimed);
      laidOut.forEach((item) => {
        const { ev, startMin, endMin, col: colIndex, cols } = item;
        const gridStartMin = startHour * 60;
        const top = ((startMin - gridStartMin) / 60) * PX_PER_HOUR;
        const height = Math.max(MIN_EVENT_PX, ((endMin - startMin) / 60) * PX_PER_HOUR);
        const gapPct = 3;
        const width = 100 / cols;
        const left = colIndex * width;

        const block = document.createElement("div");
        block.className = "time-event" + (isCoachEvent(ev.title) ? " event-chip-mine" : "");
        block.tabIndex = 0; // keyboard users can Tab to an event to expand+read it too
        block.style.top = `${top}px`;
        block.style.height = `${height}px`;
        block.style.left = `calc(${left}% + 1px)`;
        block.style.width = `calc(${width}% - ${gapPct}px)`;
        // Read by .time-event:hover so the expanded box never shrinks
        // below the event's real (time-proportional) height.
        block.style.setProperty("--slot-height", `${height}px`);
        // No native title="" tooltip here on purpose — hover already
        // expands the block itself to show the full text; a browser
        // tooltip on top of that would just be a redundant second popup.
        block.textContent = `${formatClock(new Date(ev.start_at))} ${ev.title || "(untitled)"}`;
        col.appendChild(block);
      });

      body.appendChild(col);
    });

    scrollEl.appendChild(body);
    wrap.appendChild(scrollEl);

    return { el: wrap, scrollEl, startHour };
  }

  function buildMonthGrid(year, month, eventsByDate) {
    const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
    const grid = document.createElement("div");
    grid.className = "calendar-grid";

    WEEKDAYS.forEach((wd) => {
      const el = document.createElement("div");
      el.className = "calendar-weekday";
      el.textContent = wd;
      grid.appendChild(el);
    });

    const firstOfMonth = new Date(year, month, 1);
    const leadingBlanks = (firstOfMonth.getDay() + 6) % 7; // Monday-first offset
    for (let i = 0; i < leadingBlanks; i++) {
      const blank = document.createElement("div");
      blank.className = "calendar-day empty";
      grid.appendChild(blank);
    }

    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const todayISO = window.isoDate(window.getEasternToday());

    for (let day = 1; day <= daysInMonth; day++) {
      const d = new Date(year, month, day);
      const iso = window.isoDate(d);

      const cell = document.createElement("div");
      cell.className = "calendar-day";

      const dateEl = document.createElement("div");
      dateEl.className = "calendar-day-date";
      dateEl.textContent = day;
      cell.appendChild(dateEl);
      cell.appendChild(buildEventList(eventsByDate[iso], 3));

      if (iso === todayISO) {
        cell.classList.add("today");
      }

      // Clicking a day jumps into a focused view anchored on that day,
      // rather than color-coding whether quota was hit that day.
      cell.setAttribute("role", "button");
      cell.tabIndex = 0;
      const goToDay = () => {
        state.anchor = d;
        state.view = "3day";
        renderCalendar();
      };
      cell.addEventListener("click", goToDay);
      cell.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          goToDay();
        }
      });
      grid.appendChild(cell);
    }

    return grid;
  }

  // ---- Recruit list (always today, regardless of calendar view) ----
  function renderRecruits(data) {
    const summaryEl = document.getElementById("recruits-summary");
    let doneCount = data.todayDone;

    function updateCounts() {
      summaryEl.innerHTML = `<strong>${doneCount} of ${data.todayTarget}</strong> reached out to today`;
    }
    updateCounts();

    const list = document.getElementById("recruits-list");
    list.innerHTML = "";

    data.recruits.forEach((recruit) => {
      const row = document.createElement("label");
      row.className = "recruit-row" + (recruit.done ? " done" : "");

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = recruit.done;
      checkbox.addEventListener("change", () => {
        row.classList.toggle("done", checkbox.checked);
        recruit.done = checkbox.checked;
        doneCount += checkbox.checked ? 1 : -1;
        updateCounts();
        if (state.todayStats) {
          state.todayStats.done = doneCount;
          renderCalendar();
        }
        window.setRecruitDone(coach.id, recruit.id, checkbox.checked);
      });

      const info = document.createElement("div");
      info.className = "recruit-info";

      const name = document.createElement("div");
      name.className = "recruit-name";
      name.textContent = recruit.gradYear
        ? `${recruit.firstName} ${recruit.lastName} (${recruit.gradYear})`
        : `${recruit.firstName} ${recruit.lastName}`;
      info.appendChild(name);

      if (recruit.acsRank) {
        const rank = document.createElement("div");
        rank.className = "recruit-rank";
        rank.textContent = recruit.acsRank;
        info.appendChild(rank);
      }

      if (recruit.phone) {
        const phone = document.createElement("div");
        phone.className = "recruit-phone";
        phone.textContent = recruit.phone;
        info.appendChild(phone);
      }

      if (recruit.missing && recruit.missing.length) {
        const missing = document.createElement("div");
        missing.className = "recruit-missing";
        missing.innerHTML = `<span class="recruit-missing-label">MISSING:</span> ${recruit.missing.join(", ")}`;
        info.appendChild(missing);
      }

      row.appendChild(checkbox);
      row.appendChild(info);
      list.appendChild(row);
    });
  }

  window.onCoachAuthChange((session) => {
    if (session) render();
  });
})();
