(function () {
  "use strict";

  var msg = document.getElementById("msg");
  var newPasswordEl = document.getElementById("newPassword");
  var confirmPasswordEl = document.getElementById("confirmPassword");
  var btnSubmit = document.getElementById("btnSubmit");
  var panelForm = document.getElementById("panelForm");
  var panelSuccess = document.getElementById("panelSuccess");

  BalloonAuth.requirePageAccess({ allowTempPassword: true }).catch(function () {
    window.location.href = "/login";
  });

  var rules = {
    "r-len": function (v) { return v.length >= 8; },
    "r-upper": function (v) { return /[A-Z]/.test(v); },
    "r-lower": function (v) { return /[a-z]/.test(v); },
    "r-digit": function (v) { return /\d/.test(v); },
    "r-special": function (v) { return /[^A-Za-z0-9]/.test(v); },
  };

  newPasswordEl.addEventListener("input", function () {
    var val = this.value;
    Object.keys(rules).forEach(function (id) {
      var el = document.getElementById(id);
      var met = rules[id](val);
      el.classList.toggle("rule-ok", met);
      el.classList.toggle("rule-fail", val.length > 0 && !met);
    });
  });

  function showMsg(m, isSuccess) {
    msg.textContent = m || "";
    msg.style.color = isSuccess ? "#4ade80" : "#f87171";
  }

  btnSubmit.addEventListener("click", async function () {
    showMsg("");
    var newPwd = newPasswordEl.value;
    var confirm = confirmPasswordEl.value;
    if (!newPwd) { showMsg("Please enter a new password"); return; }
    if (!confirm) { showMsg("Please confirm your password"); return; }
    if (newPwd !== confirm) { showMsg("Passwords do not match"); return; }

    btnSubmit.disabled = true;
    btnSubmit.textContent = "Saving…";

    try {
      var r = await fetch("/auth/change-password", {
        method: "POST",
        headers: BalloonAuth.authHeaders(),
        body: JSON.stringify({ new_password: newPwd, confirm_password: confirm }),
      });
      var text = await r.text();
      var data;
      try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }

      if (r.ok) {
        panelForm.style.display = "none";
        panelSuccess.style.display = "";
        setTimeout(function () {
          BalloonAuth.fetchMe().then(function (me) {
            if (me && me.role === "super_admin") window.location.href = "/admin";
            else window.location.href = "/app";
          });
        }, 1500);
        return;
      }

      if (BalloonAuth.redirectFromAuthError(r.status, data)) return;
      showMsg(BalloonAuth.extractError(data));
    } catch (e) {
      showMsg("Network error. Please try again.");
    } finally {
      btnSubmit.disabled = false;
      btnSubmit.textContent = "Set password";
    }
  });

  confirmPasswordEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") btnSubmit.click();
  });

  BalloonAuth.initPasswordToggles(document);
})();
