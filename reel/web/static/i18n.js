"use strict";
/* Messages arrive before application scripts so startup labels use the selected language. */
const I18N = window.REEL_I18N || {language: "en", messages: {}};
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
function setLanguage(language) {
  if (!["en", "hy"].includes(language)) throw new Error("Unsupported language");
  document.cookie = `reel-language=${language}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
  location.reload();
}
