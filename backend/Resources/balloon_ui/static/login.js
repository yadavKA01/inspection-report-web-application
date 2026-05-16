(function () {
  "use strict";

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const msg           = document.getElementById("msg");

  const panelChoice   = document.getElementById("panelChoice");
  const panelLogin    = document.getElementById("panelLogin");
  const panelRegister = document.getElementById("panelRegister");
  const panelSuccess  = document.getElementById("panelSuccess");
  const noAccountHint = document.getElementById("noAccountHint");
  const btnTabLogin   = document.getElementById("btnTabLogin");
  const btnTabRegister = document.getElementById("btnTabRegister");

  const emailInput    = document.getElementById("email");
  const passwordInput = document.getElementById("password");

  const regFirstname  = document.getElementById("regFirstname");
  const regLastname   = document.getElementById("regLastname");
  const regEmail      = document.getElementById("regEmail");
  const regPassword   = document.getElementById("regPassword");
  const regPassword2  = document.getElementById("regPassword2");

  // ── Redirect if already logged in ────────────────────────────────────────
  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (me) {
      if (me && me.logged_in && !me.trial_expired) {
        window.location.href = "/app";
      }
      if (me && me.logged_in && me.trial_expired) {
        window.location.href = "/payment";
      }
    })
    .catch(function () {});

  // ── Helpers ───────────────────────────────────────────────────────────────
  function show(m, isSuccess) {
    msg.textContent = m || "";
    msg.style.color = isSuccess ? "#4ade80" : "#f87171";
  }

  function setChoiceTabs(mode) {
    if (!btnTabLogin || !btnTabRegister) return;
    var loginActive = mode === "login";
    btnTabLogin.classList.toggle("choice-tab-active", loginActive);
    btnTabRegister.classList.toggle("choice-tab-active", !loginActive);
  }

  function showPanel(name) {
    if (panelChoice) {
      panelChoice.style.display = name === "success" ? "none" : "";
    }
    panelLogin.style.display    = name === "login"    ? "" : "none";
    panelRegister.style.display = name === "register" ? "" : "none";
    panelSuccess.style.display  = name === "success"  ? "" : "none";
    if (name === "login")    setChoiceTabs("login");
    if (name === "register") setChoiceTabs("register");
    show("");
  }

  /**
   * POST JSON to *path*.
   * Returns { ok, status, data } always — never throws.
   */
  async function apiPost(path, body) {
    try {
      const r    = await fetch(path, {
        method:      "POST",
        headers:     { "Content-Type": "application/json" },
        credentials: "same-origin",
        body:        JSON.stringify(body),
      });
      const text = await r.text();
      let data;
      try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }
      return { ok: r.ok, status: r.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { detail: "Network error" } };
    }
  }

  function extractError(data) {
    if (!data) return "Request failed";
    const d = data.detail;
    if (d == null)             return data.error || data.message || "Error";
    if (typeof d === "string") return d;
    if (Array.isArray(d))      return d.map(x => (typeof x === "string" ? x : x.msg || JSON.stringify(x))).join(" ");
    if (d && typeof d === "object" && d.msg) return String(d.msg);
    try { return JSON.stringify(d); } catch (e) { return String(d); }
  }

  // ── LOGIN ─────────────────────────────────────────────────────────────────
  document.getElementById("btnLogin").addEventListener("click", async function () {
    show("");
    noAccountHint.style.display = "none";

    const email    = emailInput.value.trim();
    const password = passwordInput.value;

    if (!email)    { show("Please enter your email address"); return; }
    if (!password) { show("Please enter your password");      return; }

    const btn = this;
    btn.disabled    = true;
    btn.textContent = "Logging in…";

    const { ok, status, data } = await apiPost("/api/auth/login", { email, password });

    btn.disabled    = false;
    btn.textContent = "Log in";

    if (ok) {
      if (data.requires_password_change) {
        window.location.href = "/change-password";
      } else {
        window.location.href = "/app";
      }
      return;
    }

    const errMsg = extractError(data);
    // Unknown email (legacy 404 or current 401) — not a missing HTTP route
    if (status === 404 || (status === 401 && errMsg.indexOf("Account not found") !== -1)) {
      show("Account not found. Please create an account.");
      noAccountHint.style.display = "block";
      return;
    }

    if (status === 503) {
      show(errMsg || "Database unavailable. Check the server terminal and MongoDB.");
      return;
    }

    if (status === 403) {
      show(extractError(data) || "Your ID has expired. Please take a subscription.");
      return;
    }

    if (status === 401) {
      show(errMsg || "Invalid email or password");
      return;
    }

    show(errMsg);
  });

  if (btnTabLogin) {
    btnTabLogin.addEventListener("click", function () {
      noAccountHint.style.display = "none";
      showPanel("login");
      emailInput.focus();
    });
  }
  if (btnTabRegister) {
    btnTabRegister.addEventListener("click", function () {
      regEmail.value = emailInput.value.trim();
      showPanel("register");
      regFirstname.focus();
    });
  }

  // Allow pressing Enter in the password field to trigger login
  passwordInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("btnLogin").click();
  });

  // ── SHOW REGISTER PANEL (from “no account” hint) ──────────────────────────
  document.getElementById("btnShowRegister").addEventListener("click", function () {
    regEmail.value = emailInput.value.trim();
    showPanel("register");
    regFirstname.focus();
  });

  // ── BACK TO LOGIN ─────────────────────────────────────────────────────────
  document.getElementById("btnBackToLogin").addEventListener("click", function () {
    showPanel("login");
  });

  // ── REGISTER ──────────────────────────────────────────────────────────────
  document.getElementById("btnRegister").addEventListener("click", async function () {
    show("");

    const firstname = regFirstname.value.trim();
    const lastname  = regLastname.value.trim();
    const email     = regEmail.value.trim();
    const password  = regPassword.value;
    const confirm_password = regPassword2.value;

    if (!firstname) { show("Please enter your first name"); return; }
    if (!lastname)  { show("Please enter your last name");  return; }
    if (!email)     { show("Please enter your email");      return; }
    if (password || confirm_password) {
      if (password !== confirm_password) {
        show("Passwords do not match");
        return;
      }
    }

    const btn = this;
    btn.disabled    = true;
    btn.textContent = "Creating account…";

    const payload = { email, firstname, lastname };
    if (password) {
      payload.password = password;
      payload.confirm_password = confirm_password;
    }

    const { ok, data } = await apiPost("/api/auth/register", payload);

    btn.disabled    = false;
    btn.textContent = "Create account";

    if (ok) {
      if (data.registration_kind === "password") {
        document.getElementById("successMsg").textContent =
          data.message || "Account created. You can log in now.";
        showPanel("success");
        return;
      }
      document.getElementById("successMsg").textContent =
        data.message || "Account created. Check your email for your temporary password.";
      showPanel("success");
      return;
    }

    show(extractError(data));
  });

  // ── BACK FROM SUCCESS ─────────────────────────────────────────────────────
  document.getElementById("btnGoLogin").addEventListener("click", function () {
    showPanel("login");
    noAccountHint.style.display = "none";
  });

})();
