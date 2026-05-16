(function () {
  "use strict";

  const msg             = document.getElementById("msg");
  const newPasswordEl   = document.getElementById("newPassword");
  const confirmPasswordEl= document.getElementById("confirmPassword");
  const btnSubmit       = document.getElementById("btnSubmit");
  const panelForm       = document.getElementById("panelForm");
  const panelSuccess    = document.getElementById("panelSuccess");

  // ── Guard: must be logged in ──────────────────────────────────────────────
  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (me) {
      if (!me || !me.logged_in) {
        window.location.href = "/login";
      }
    })
    .catch(function () { window.location.href = "/login"; });

  // ── Live password-strength indicator ─────────────────────────────────────
  const rules = {
    "r-len":    function (v) { return v.length >= 8; },
    "r-upper":  function (v) { return /[A-Z]/.test(v); },
    "r-lower":  function (v) { return /[a-z]/.test(v); },
    "r-digit":  function (v) { return /\d/.test(v); },
    "r-special":function (v) { return /[^A-Za-z0-9]/.test(v); },
  };

  newPasswordEl.addEventListener("input", function () {
    const val = this.value;
    Object.keys(rules).forEach(function (id) {
      const el  = document.getElementById(id);
      const met = rules[id](val);
      el.classList.toggle("rule-ok",  met);
      el.classList.toggle("rule-fail", val.length > 0 && !met);
    });
  });

  // ── Submit ────────────────────────────────────────────────────────────────
  function showMsg(m, isSuccess) {
    msg.textContent = m || "";
    msg.style.color = isSuccess ? "#4ade80" : "#f87171";
  }

  btnSubmit.addEventListener("click", async function () {
    showMsg("");
    const newPwd  = newPasswordEl.value;
    const confirm = confirmPasswordEl.value;

    if (!newPwd)  { showMsg("Please enter a new password");   return; }
    if (!confirm) { showMsg("Please confirm your password");  return; }
    if (newPwd !== confirm) { showMsg("Passwords do not match"); return; }

    btnSubmit.disabled    = true;
    btnSubmit.textContent = "Saving…";

    try {
      const r    = await fetch("/api/auth/change-password", {
        method:      "POST",
        headers:     { "Content-Type": "application/json" },
        credentials: "same-origin",
        body:        JSON.stringify({ new_password: newPwd, confirm_password: confirm }),
      });
      const text = await r.text();
      let data;
      try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }

      if (r.ok) {
        panelForm.style.display    = "none";
        panelSuccess.style.display = "";
        setTimeout(function () { window.location.href = "/app"; }, 2000);
        return;
      }

      const d = data.detail;
      let errMsg = "Error";
      if (typeof d === "string") errMsg = d;
      else if (Array.isArray(d)) errMsg = d.map(x => x.msg || x).join(" ");
      showMsg(errMsg);
    } catch (e) {
      showMsg("Network error. Please try again.");
    } finally {
      btnSubmit.disabled    = false;
      btnSubmit.textContent = "Set password";
    }
  });

  // Allow Enter key on confirm field
  confirmPasswordEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") btnSubmit.click();
  });

})();
