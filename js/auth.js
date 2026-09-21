// Handles login (email + password), signup, logout, and session state.
// Supabase persists the session in localStorage by default, so coaches stay
// logged in across visits without any extra work here.
(function () {
  const authPanel = document.getElementById("auth-panel");
  const appPanel = document.getElementById("app-panel");
  const sessionInfo = document.getElementById("session-info");
  const userEmailEl = document.getElementById("user-email");
  const logoutBtn = document.getElementById("logout-btn");

  const passwordForm = document.getElementById("password-form");
  const passwordEmail = document.getElementById("password-email");
  const passwordPassword = document.getElementById("password-password");
  const passwordMessage = document.getElementById("password-message");
  const signupBtn = document.getElementById("signup-btn");

  function setMessage(el, text, kind) {
    el.textContent = text;
    el.classList.remove("error", "success");
    if (kind) el.classList.add(kind);
  }

  function requireClient() {
    if (!window.supabaseClient) {
      setMessage(passwordMessage, "Supabase isn't configured yet — see js/config.js.", "error");
      return false;
    }
    return true;
  }

  passwordForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireClient()) return;
    setMessage(passwordMessage, "Logging in…");
    const { error } = await window.supabaseClient.auth.signInWithPassword({
      email: passwordEmail.value.trim(),
      password: passwordPassword.value,
    });
    if (error) {
      setMessage(passwordMessage, error.message, "error");
    } else {
      setMessage(passwordMessage, "");
    }
  });

  signupBtn.addEventListener("click", async () => {
    if (!requireClient()) return;
    if (!passwordEmail.value || !passwordPassword.value) {
      setMessage(passwordMessage, "Enter an email and password first.", "error");
      return;
    }
    setMessage(passwordMessage, "Signing up…");
    const { error } = await window.supabaseClient.auth.signUp({
      email: passwordEmail.value.trim(),
      password: passwordPassword.value,
    });
    if (error) {
      setMessage(passwordMessage, error.message, "error");
    } else {
      setMessage(
        passwordMessage,
        "Account created. Check your email to confirm, then log in.",
        "success"
      );
    }
  });

  logoutBtn.addEventListener("click", async () => {
    if (!window.supabaseClient) return;
    await window.supabaseClient.auth.signOut();
  });

  // Other scripts (home.js, coach-dashboard.js) need to know the current
  // auth state, but they may finish loading either before or after auth
  // actually resolves — a plain "dispatch an event and hope something's
  // listening" missed the event whenever auth resolved first (this is what
  // was silently breaking the home page: auth worked, but nothing was
  // listening yet when it fired). window.onCoachAuthChange fixes that by
  // replaying the current state immediately to anyone who subscribes late,
  // in addition to notifying on every future change.
  let authResolved = false;
  let currentSession = null;
  const subscribers = [];

  window.onCoachAuthChange = function (callback) {
    subscribers.push(callback);
    if (authResolved) callback(currentSession);
  };

  function setAuthState(session) {
    authResolved = true;
    currentSession = session;
    subscribers.forEach((cb) => cb(session));
  }

  function showLoggedIn(session) {
    authPanel.hidden = true;
    appPanel.hidden = false;
    sessionInfo.hidden = false;
    userEmailEl.textContent = session.user.email;
    setAuthState(session);
  }

  function showLoggedOut() {
    authPanel.hidden = false;
    appPanel.hidden = true;
    sessionInfo.hidden = true;
    setAuthState(null);
  }

  // When a page is restored from the browser's back/forward cache (bfcache),
  // none of the script above re-runs — the DOM is just repainted exactly as
  // it was frozen, which can leave it stuck on whatever it was mid-render
  // (e.g. still showing neither panel). Forcing a reload on a bfcache
  // restore guarantees the auth check and render always run fresh.
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) {
      window.location.reload();
    }
  });

  // NOTE: deliberately not `await supabaseClient.auth.getSession()` here.
  // That call can hang indefinitely in a background/inactive tab — it
  // acquires a browser lock internally that some tabs don't get until they
  // regain focus, which is exactly why the whole page used to sit stuck
  // until you switched away and back. Instead: render from
  // onAuthStateChange's initial event, with a short timeout fallback so the
  // page never waits forever on it either.
  window.coachAuthReady = (function init() {
    if (!window.supabaseClient) {
      showLoggedOut();
      return;
    }

    let rendered = false;
    const renderOnce = (session) => {
      if (rendered) return;
      rendered = true;
      if (session) showLoggedIn(session);
      else showLoggedOut();
    };

    window.supabaseClient.auth.onAuthStateChange((_event, session) => {
      rendered = false; // allow later real changes (login/logout) to re-render
      renderOnce(session);
    });

    setTimeout(() => renderOnce(null), 2500);
  })();
})();
