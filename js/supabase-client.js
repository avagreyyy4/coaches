// Creates the shared Supabase client used by auth.js and checklist.js.
(function () {
  const cfg = window.SUPABASE_CONFIG || {};
  const isPlaceholder =
    !cfg.url || !cfg.anonKey ||
    cfg.url.includes("YOUR_SUPABASE_PROJECT_URL") ||
    cfg.anonKey.includes("YOUR_SUPABASE_ANON_KEY");

  if (isPlaceholder) {
    const warning = document.getElementById("config-warning");
    if (warning) warning.hidden = false;
    window.supabaseClient = null;
    return;
  }

  window.supabaseClient = window.supabase.createClient(cfg.url, cfg.anonKey);
})();
