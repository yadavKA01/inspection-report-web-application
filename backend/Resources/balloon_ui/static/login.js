(function () {
  "use strict";

  var msg = document.getElementById("msg");
  var trialNote = document.getElementById("trialNote");

  (function showAccessNotice() {
    var p = new URLSearchParams(window.location.search);
    if (p.get("access") === "inactive") {
      show("Your account is not active. Ask your super admin to grant access.", false);
    } else if (p.get("access") === "denied") {
      show("You do not have permission to use the app. Ask your super admin to enable access.", false);
    }
  })();

  function show(m, isSuccess) {
    msg.textContent = m || "";
    msg.style.color = isSuccess ? "#4ade80" : "#f87171";
  }

  async function apiPost(path, body) {
    var r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    var text = await r.text();
    var data;
    try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }
    return { ok: r.ok, status: r.status, data: data };
  }

  BalloonAuth.fetchAuthConfig().then(function (cfg) {
    if (!cfg.require_login) return null;
    return BalloonAuth.fetchMe();
  }).then(function (me) {
    if (!me) return;
    if (me.is_temp_password) { window.location.href = "/change-password"; return; }
    if (me.role === "super_admin") window.location.href = "/admin";
    else window.location.href = "/app";
  }).catch(function () {});

  document.getElementById("btnLogin").addEventListener("click", async function () {
    show("");
    var login = document.getElementById("loginId").value.trim();
    var password = document.getElementById("password").value;
    if (!login) { show("Enter email or username"); return; }
    if (!password) { show("Enter password"); return; }

    var btn = this;
    btn.disabled = true;
    btn.textContent = "Logging in…";
    var res;
    try {
      res = await apiPost("/auth/login", { login: login, password: password });
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Log in";
      show("Cannot reach server. Wait for Render to wake up, then try again.");
      return;
    }
    btn.disabled = false;
    btn.textContent = "Log in";

    if (!res.ok) {
      /* Do not redirect to /login on 401 — that reloads the page and hides the error. */
      if (res.status !== 401 && BalloonAuth.redirectFromAuthError(res.status, res.data)) return;
      show(BalloonAuth.extractError(res.data) || "Login failed.");
      return;
    }

    BalloonAuth.setToken(res.data.access_token);
    if (res.data.requires_password_change) {
      window.location.href = "/change-password";
      return;
    }
    if (res.data.role === "super_admin") window.location.href = "/admin";
    else window.location.href = "/app";
  });

  document.getElementById("password").addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("btnLogin").click();
  });

  BalloonAuth.attachPasswordToggle(
    document.getElementById("password"),
    document.getElementById("btnTogglePassword")
  );
})();
