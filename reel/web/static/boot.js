/* Personal and shared sessions use the same cookie and API. */
applyTheme();
window.addEventListener("hashchange", () => {
  if (S.role) route();
  else if (!publicAccountRoute()) renderLogin();
});
if (!publicAccountRoute())
  api("/api/admin-auth/status", { login: true })
    .then((status) => {
      if (status.second_factor) {
        viewSecondFactor(status.resend_after);
        return;
      }
      return api("/api/me")
        .then((r) => {
          S.role = r.role;
          S.user = r.user || null;
          route();
        })
        .catch(() => {});
    })
    .catch(() => renderLogin());
