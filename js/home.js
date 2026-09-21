// Renders the 3 coach cards on the home page from mock data.
(function () {
  const grid = document.getElementById("coach-grid");
  if (!grid) return;

  function render() {
    grid.innerHTML = "";
    window.COACHES.forEach((coach) => {
      const data = window.getCoachDashboardData(coach.id);

      const card = document.createElement("a");
      card.className = "coach-card";
      card.href = `${coach.id}.html`;

      const head = document.createElement("div");
      head.className = "coach-card-head";
      head.innerHTML = `
        <span class="badge">${coach.name.charAt(0)}</span>
        <span class="coach-card-name">${coach.name}</span>
      `;

      const ratio = document.createElement("div");
      ratio.className = "coach-card-ratio";
      ratio.innerHTML = `
        <span class="done">${data.todayDone}</span>
        <span class="sep">/</span>
        <span class="goal">${data.todayTarget}</span>
      `;

      const label = document.createElement("p");
      label.className = "coach-card-label";
      label.textContent = "daily recruits reached out to";

      card.appendChild(head);
      card.appendChild(ratio);
      card.appendChild(label);

      if (data.todayTarget > 5) {
        const note = document.createElement("p");
        note.className = "coach-card-note";
        note.textContent = `Includes ${data.todayTarget - 5} carried over from yesterday`;
        card.appendChild(note);
      }

      grid.appendChild(card);
    });
  }

  window.addEventListener("coach-auth-changed", (e) => {
    if (e.detail.session) render();
  });
})();
