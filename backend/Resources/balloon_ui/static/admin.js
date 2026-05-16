(function () {
  const msg = document.getElementById("msg");
  const body = document.getElementById("userBody");

  async function load() {
    const r = await fetch("/api/admin/users", { credentials: "same-origin" });
    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      msg.textContent = text.slice(0, 200);
      return;
    }
    if (!r.ok) {
      msg.textContent = data.detail || "Forbidden";
      return;
    }
    body.innerHTML = "";
    (data.users || []).forEach(function (u) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + (u.email || "") + "</td>" +
        "<td>" + (u.role || "") + "</td>" +
        "<td>" + (u.paid ? "yes" : "no") + "</td>" +
        "<td>" + (u.trial_started_at || "—") + "</td>";
      body.appendChild(tr);
    });
  }

  document.getElementById("btnResetPwd").addEventListener("click", async function () {
    msg.textContent = "";
    msg.style.removeProperty("color");
    const email = document.getElementById("resetEmail").value.trim();
    const new_password = document.getElementById("resetPwd").value;
    const confirm_password = document.getElementById("resetPwd2").value;
    if (!email) {
      msg.textContent = "Enter user email";
      return;
    }
    const r = await fetch("/api/admin/reset-user-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ email: email, new_password: new_password, confirm_password: confirm_password }),
    });
    const data = await r.json().catch(function () { return {}; });
    if (!r.ok) {
      msg.textContent = data.detail || "Error";
      return;
    }
    msg.style.color = "#4ade80";
    msg.textContent = data.message || "Password updated";
    document.getElementById("resetPwd").value = "";
    document.getElementById("resetPwd2").value = "";
    load();
  });

  document.getElementById("btnPaid").addEventListener("click", async function () {
    msg.textContent = "";
    msg.style.removeProperty("color");
    const email = document.getElementById("paidEmail").value.trim();
    const r = await fetch("/api/admin/set-paid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ email: email, paid: true }),
    });
    const data = await r.json().catch(function () { return {}; });
    if (!r.ok) {
      msg.textContent = data.detail || "Error";
      return;
    }
    load();
  });

  load();
})();
