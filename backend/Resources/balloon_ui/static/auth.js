(function (global) {
  "use strict";

  var TOKEN_KEY = "balloon_token";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY) || null;
  }

  function setToken(token) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  }

  function authHeaders(extra) {
    var h = Object.assign({}, extra || {});
    var t = getToken();
    if (t) h.Authorization = "Bearer " + t;
    if (!h["Content-Type"]) h["Content-Type"] = "application/json";
    return h;
  }

  function extractError(data) {
    if (!data) return "Request failed";
    var d = data.detail;
    if (d == null) return data.error || data.message || "Error";
    if (typeof d === "string") return d;
    if (typeof d === "object" && d.message) return String(d.message);
    if (Array.isArray(d)) {
      return d.map(function (x) {
        return typeof x === "string" ? x : x.msg || JSON.stringify(x);
      }).join(" ");
    }
    try { return JSON.stringify(d); } catch (e) { return String(d); }
  }

  function redirectFromAuthError(status, data) {
    if (status === 401) {
      setToken(null);
      global.location.href = "/login";
      return true;
    }
    if (status === 402) {
      global.location.href = "/payment";
      return true;
    }
    if (status === 403) {
      var d = data && data.detail;
      if (typeof d === "object" && d && d.error === "TRIAL_EXPIRED") {
        global.location.href = "/payment";
        return true;
      }
      if (typeof d === "object" && d && d.error === "PERMISSION_DENIED") {
        global.location.href = "/login?access=denied";
        return true;
      }
      if (typeof d === "string" && /trial|subscription|expired/i.test(d)) {
        global.location.href = "/payment";
        return true;
      }
      if (typeof d === "string" && /do not have access|deactivated|permission/i.test(d)) {
        global.location.href = "/login?access=denied";
        return true;
      }
    }
    return false;
  }

  async function fetchAuthConfig() {
    try {
      var r = await fetch("/api/v1/auth-config");
      if (!r.ok) return { require_login: true, auth_enabled: false, trial_days: 7 };
      return await r.json();
    } catch (e) {
      return { require_login: true, auth_enabled: false, trial_days: 7 };
    }
  }

  async function fetchMe() {
    var t = getToken();
    if (!t) return null;
    var r = await fetch("/auth/me", { headers: { Authorization: "Bearer " + t } });
    if (!r.ok) return null;
    return r.json();
  }

  async function fetchTrialStatus() {
    var t = getToken();
    if (!t) return null;
    var r = await fetch("/api/v1/trial-status", { headers: { Authorization: "Bearer " + t } });
    if (!r.ok) return null;
    return r.json();
  }

  async function requirePageAccess(options) {
    options = options || {};
    var cfg = await fetchAuthConfig();

    if (!cfg.require_login) {
      return { cfg: cfg, me: null };
    }

    var me = await fetchMe();
    if (!me) {
      global.location.href = "/login";
      return null;
    }

    if (me.is_temp_password && !options.allowTempPassword) {
      global.location.href = "/change-password";
      return null;
    }

    if (options.superAdminOnly && me.role !== "super_admin") {
      global.location.href = "/app";
      return null;
    }

    if (options.engineerOnly && me.role === "super_admin") {
      global.location.href = "/admin";
      return null;
    }

    if (me.role !== "super_admin") {
      if (!me.is_active) {
        global.location.href = "/login?access=inactive";
        return null;
      }
      if (!me.can_read) {
        global.location.href = "/login?access=denied";
        return null;
      }
      if (!options.skipTrialCheck) {
        var ts = await fetchTrialStatus();
        if (ts && (ts.subscription_status === "expired" ||
            (ts.subscription_status === "trial" && ts.days_remaining === 0))) {
          global.location.href = "/payment";
          return null;
        }
      }
    }

    return { cfg: cfg, me: me };
  }

  async function requireAppAccess() {
    return requirePageAccess({ engineerOnly: false, skipTrialCheck: false });
  }

  function applyPermissionUi(me) {
    if (!me || me.role === "super_admin") return;
    var readOnly = me.can_read && !me.can_write;
    var noWrite = !me.can_write;
    var noDelete = !me.can_delete;
    document.body.classList.toggle("perm-read-only", readOnly);
    document.querySelectorAll(
      "#btnModeCreate,#btnModeEdit,#btnModeDelete,#btnModeSave," +
      "#fileInput,#inspectionReport,button[data-needs-write]"
    ).forEach(function (el) {
      if (noWrite) el.setAttribute("disabled", "disabled");
    });
    document.querySelectorAll("#btnModeDelete,button[data-needs-delete]").forEach(function (el) {
      if (noDelete) el.setAttribute("disabled", "disabled");
    });
  }

  function attachPasswordToggle(inputEl, btnEl) {
    if (!inputEl || !btnEl) return;
    btnEl.addEventListener("click", function () {
      var show = inputEl.type === "password";
      inputEl.type = show ? "text" : "password";
      btnEl.textContent = show ? "Hide" : "Show";
      btnEl.setAttribute("aria-pressed", show ? "true" : "false");
      btnEl.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btnEl.title = show ? "Hide password" : "Show password";
    });
  }

  function initPasswordToggles(root) {
    var scope = root || document;
    scope.querySelectorAll(".password-toggle").forEach(function (btn) {
      var id = btn.getAttribute("data-target") || "password";
      var input = scope.querySelector("#" + id) || document.getElementById(id);
      attachPasswordToggle(input, btn);
    });
  }

  global.BalloonAuth = {
    TOKEN_KEY: TOKEN_KEY,
    getToken: getToken,
    setToken: setToken,
    authHeaders: authHeaders,
    extractError: extractError,
    redirectFromAuthError: redirectFromAuthError,
    fetchAuthConfig: fetchAuthConfig,
    fetchMe: fetchMe,
    fetchTrialStatus: fetchTrialStatus,
    requirePageAccess: requirePageAccess,
    requireAppAccess: requireAppAccess,
    applyPermissionUi: applyPermissionUi,
    attachPasswordToggle: attachPasswordToggle,
    initPasswordToggles: initPasswordToggles,
  };
})(window);
