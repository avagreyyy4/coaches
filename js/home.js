// Renders the 3 coach cards + season-wide tracker on the home page.
(function () {
  const grid = document.getElementById("coach-grid");
  if (!grid) return;

  const trackerEl = document.getElementById("season-tracker");

  async function render() {
    grid.innerHTML = "";
    const results = await Promise.all(
      window.COACHES.map((coach) => window.getCoachDashboardData(coach.id))
    );

    window.COACHES.forEach((coach, i) => {
      const data = results[i];

      const card = document.createElement("a");
      card.className = "coach-card";
      card.href = `${coach.id}.html`;

      const name = document.createElement("p");
      name.className = "coach-card-name";
      name.textContent = coach.name;

      const ratio = document.createElement("div");
      ratio.className = "coach-card-ratio";
      ratio.innerHTML = `
        <span class="done">${data.todayDone}</span>
        <span class="sep">/</span>
        <span class="goal">${data.todayTarget}</span>
      `;

      card.appendChild(name);
      card.appendChild(ratio);

      if (data.todayTarget > window.DAILY_QUOTA) {
        const note = document.createElement("p");
        note.className = "coach-card-note";
        note.textContent = `Includes ${data.todayTarget - window.DAILY_QUOTA} carried over from yesterday`;
        card.appendChild(note);
      }

      grid.appendChild(card);
    });

    renderTracker();
  }

  async function renderTracker() {
    if (!trackerEl) return;
    const { contacted, total } = await window.getSeasonProgress();
    const pct = total > 0 ? Math.round((contacted / total) * 100) : 0;

    trackerEl.innerHTML = `
      <div class="season-tracker-head">
        <span class="season-tracker-label">Total players reached out to</span>
        <span class="season-tracker-count"><strong>${contacted}</strong> of ${total}</span>
      </div>
      <div class="season-tracker-bar">
        <div class="season-tracker-fill" id="season-tracker-fill"></div>
      </div>
    `;

    // Set the fill on the next frame (not inline above) so it starts from
    // the CSS default scaleX(0) and actually transitions in, rather than
    // painting straight at its final value.
    const fill = document.getElementById("season-tracker-fill");
    requestAnimationFrame(() => {
      fill.style.transform = `scaleX(${pct / 100})`;
    });
  }

  window.onCoachAuthChange((session) => {
    if (session) render();
  });
})();
