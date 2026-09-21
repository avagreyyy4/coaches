// Expand/collapse the "Coaches" group in the sidebar.
(function () {
  const toggle = document.getElementById("coaches-toggle");
  const subnav = document.getElementById("coaches-subnav");
  if (!toggle || !subnav) return;

  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    subnav.classList.toggle("collapsed", expanded);
  });
})();
