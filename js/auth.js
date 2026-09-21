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

  function showLoggedIn(session) {
    authPanel.hidden = true;
    appPanel.hidden = false;
    sessionInfo.hidden = false;
    userEmailEl.textContent = session.user.email;
    window.dispatchEvent(new CustomEvent("coach-auth-changed", { detail: { session } }));
  }

  function showLoggedOut() {
    authPanel.hidden = false;
    appPanel.hidden = true;
    sessionInfo.hidden = true;
    window.dispatchEvent(new CustomEvent("coach-auth-changed", { detail: { session: null } }));
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

  window.coachAuthReady = (async function init() {
    if (!window.supabaseClient) {
      showLoggedOut();
      return;
    }
    const {
      data: { session },
    } = await window.supabaseClient.auth.getSession();
    if (session) showLoggedIn(session);
    else showLoggedOut();

    window.supabaseClient.auth.onAuthStateChange((_event, session) => {
      if (session) showLoggedIn(session);
      else showLoggedOut();
    });
  })();
})();
