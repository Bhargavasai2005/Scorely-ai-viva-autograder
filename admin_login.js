(function () {
  "use strict";

  var form = document.getElementById("admin-login-form");
  if (!form) return;

  var errorEl = document.getElementById("admin-login-error");

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    var user = document.getElementById("admin-username").value;
    var pass = document.getElementById("admin-password").value;
    if (errorEl) {
      errorEl.hidden = true;
      errorEl.textContent = "";
    }

    fetch("/api/admin-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: user, password: pass }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (out) {
        if (out.ok && out.data.success) {
          localStorage.setItem("adminLoggedIn", "true");
          window.location.href = "/viva/admin";
          return;
        }
        if (errorEl) {
          errorEl.textContent =
            (out.data && out.data.message) || "Invalid username or password.";
          errorEl.hidden = false;
        }
      })
      .catch(function () {
        if (errorEl) {
          errorEl.textContent = "Could not reach server. Try again.";
          errorEl.hidden = false;
        }
      });
  });
})();
