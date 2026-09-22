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

  async function render() {
    document.getElementById("coach-name").textContent = coach.name;

    state.anchor = window.getEasternToday();
    const data = await window.getCoachDashboardData(coach.id);
    state.todayStats = { done: data.todayDone, target: data.todayTarget };
    renderCalendar();
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

  function renderCalendar() {
    document.querySelectorAll(".view-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === state.view);
    });

    const label = document.getElementById("calendar-period-label");
    const body = document.getElementById("calendar-body");
    body.innerHTML = "";

    if (state.view === "month") {
      label.textContent = state.anchor.toLocaleDateString("en-US", { month: "long", year: "numeric" });
      body.appendChild(buildMonthGrid(state.anchor.getFullYear(), state.anchor.getMonth()));
    } else {
      const count = state.view === "3day" ? 3 : 5;
      label.textContent = formatDayRangeLabel(state.anchor, count);
      body.appendChild(buildDayRow(count));
    }
  }

  function formatDayRangeLabel(start, count) {
    const end = window.addDays(start, count - 1);
    const opts = { month: "short", day: "numeric" };
    return `${start.toLocaleDateString("en-US", opts)} – ${end.toLocaleDateString("en-US", opts)}`;
  }

  function buildDayRow(count) {
    const row = document.createElement("div");
    row.className = "calendar-days-row";
    row.dataset.count = String(count);

    const todayISO = window.isoDate(window.getEasternToday());

    for (let i = 0; i < count; i++) {
      const d = window.addDays(state.anchor, i);
      const iso = window.isoDate(d);

      const card = document.createElement("div");
      card.className = "day-card";

      let statText;
      if (iso === todayISO) {
        card.classList.add("today");
        statText = state.todayStats ? `${state.todayStats.done} of ${state.todayStats.target}` : "—";
      } else {
        card.classList.add("future");
        statText = "No data yet";
      }

      const top = document.createElement("div");
      const weekday = document.createElement("div");
      weekday.className = "day-card-weekday";
      weekday.textContent = d.toLocaleDateString("en-US", { weekday: "short" });
      const date = document.createElement("div");
      date.className = "day-card-date";
      date.textContent = d.getDate();
      top.appendChild(weekday);
      top.appendChild(date);

      const stat = document.createElement("div");
      stat.className = "day-card-stat";
      stat.textContent = statText;

      card.appendChild(top);
      card.appendChild(stat);
      row.appendChild(card);
    }

    return row;
  }

  function buildMonthGrid(year, month) {
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
      cell.textContent = day;

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
