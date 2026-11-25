"use strict";
/* Messages arrive before application scripts so startup labels use the selected language. */
const I18N = window.REEL_I18N || {language: "hy", messages: {}};
document.documentElement.lang = I18N.language;
function t(key, values = {}) {
  if (!Object.prototype.hasOwnProperty.call(I18N.messages, key)) {
    throw new Error(`Unknown message: ${key}`);
  }
  return I18N.messages[key].replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, name) => {
    if (!Object.prototype.hasOwnProperty.call(values, name)) throw new Error(`Missing message value: ${name}`);
    return String(values[name]);
  });
}
const i18nText = t;
function languageSelector() {
  return `<label class="language-picker"><span>${t("language.label")}</span><select class="input" data-language aria-label="${t("language.label")}"><option value="hy" ${I18N.language === "hy" ? "selected" : ""}>${t("language.hy_name")}</option><option value="en" ${I18N.language === "en" ? "selected" : ""}>${t("language.en")}</option></select></label>`;
}
async function setLanguage(language) {
  if (!["en", "hy"].includes(language)) throw new Error("Unsupported language");
  if (typeof R !== "undefined" && R.rec && R.rec.state !== "inactive") throw new Error(t("language.finish_recording"));
  if (typeof uploadBusy !== "undefined" && uploadBusy) throw new Error(t("language.finish_upload"));
  if (typeof S !== "undefined" && S.user?.id) {
    const response = await fetch("/api/accounts/language", {method: "PUT", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-Requested-With": "reel"}, body: JSON.stringify({language})});
    if (!response.ok) throw new Error(t("language.save_failed"));
  }
  try { localStorage.setItem("reel-language", language); } catch (_) {}
  document.cookie = `reel-language=${language}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
  location.reload();
}
document.addEventListener("change", event => {
  if (event.target.matches("[data-language]")) setLanguage(event.target.value).catch(error => {
    event.target.value = I18N.language;
    if (typeof toast === "function") toast(error.message);
  });
});
