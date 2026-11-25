"use strict";
/* Reel dashboard — vanilla JS SPA (no build step). Polls the API every 3 s. */

const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const S = { role: null, poll: null, summary: null, detail: null, sig: null, tab: "meta",
  jobFilter: "", jobSearch: "", jobGroup: "", jobSubject: "", last: {}, quality: "preview" };

const ICONS = {
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 20h16"/>',
  grid: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  dashboard: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
  chart: '<path d="M4 3v17h17M8 15v-4M13 15V7M18 15V4"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  check: '<path d="M20 6 9 17l-5-5"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88M14.47 14.48 20 20M8.12 8.12 12 12"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  play: '<path d="m6 3 14 9-14 9V3z"/>', lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  back: '<path d="m12 19-7-7 7-7M19 12H5"/>', volume: '<path d="M11 5 6 9H2v6h4l5 4V5zM15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/>',
  sparkles: '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
  yt: '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3z"/>',
  hash: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15z"/><path d="M6.5 17A2.5 2.5 0 0 0 4 19.5 2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  cap: '<path d="M22 10 12 5 2 10l10 5 10-5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7l10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  dot: '<circle cx="12" cy="12" r="3"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
};
const ic = (n, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ""}</svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS = { queued: i18nText("ui.app.queued"), running: i18nText("ui.app.processing_3"), stopped: i18nText("ui.app.stopped"), done: i18nText("ui.courses.completed") };
const PHASES = [
  { label: i18nText("ui.app.intake"), icon: "folder", stages: ["join", "probe", "audio"] },
  { label: i18nText("ui.app.transcription"), icon: "mic", stages: ["transcribe"] },
  { label: i18nText("ui.app.cuts"), icon: "scissors", stages: ["segments"] },
  { label: i18nText("stage.render"), icon: "volume", stages: ["render"] },
  { label: i18nText("ui.accounts.privacy"), icon: "shield", stages: ["blur", "privacy", "preview"] },
  { label: i18nText("ui.app.title_and_chapters"), icon: "sparkles", stages: ["metadata", "thumbnail"] },
  { label: i18nText("ui.app.publishing"), icon: "send", stages: ["youtube_upload", "playlist", "thumbnail_set", "slack_post", "classroom_post", "library_add"] },
];
const HOW = [
  ["folder", i18nText("ui.app.1_files"), i18nText("ui.app.upload_the_recording_in_one_or_more")],
  ["mic", i18nText("ui.app.2_transcription"), i18nText("ui.app.whisper_transcribes_speech_locally")],
  ["scissors", i18nText("ui.app.3_cuts"), i18nText("ui.app.the_model_identifies_off_topic_conversations_and")],
  ["volume", i18nText("ui.app.4_audio"), i18nText("ui.app.audio_levels_are_normalized_to_ebu_r128")],
  ["shield", i18nText("ui.app.5_privacy"), i18nText("ui.app.confidently_identified_student_faces_are_blurred")],
  ["sparkles", i18nText("ui.app.6_content"), i18nText("ui.app.title_description_chapters_and_thumbnail")],
  ["send", i18nText("ui.app.7_publishing"), i18nText("ui.app.youtube_unlisted_playlist_slack_library_and_classroom")],
];
const CUT_KIND = { before_class: i18nText("ui.app.before_the_lesson"), after_class: i18nText("ui.app.after_the_lesson"), break: i18nText("ui.app.break") };
const LBL = { lesson: i18nText("ui.app.lessons_3"), offtopic: i18nText("ui.app.off_topic"), unclear: i18nText("ui.app.uncertain"), silence: i18nText("ui.app.silence") };
const PUB = { youtube: ["yt", "YouTube"], playlist: ["layers", i18nText("ui.app.schedule")], slack: ["hash", "Slack"], classroom: ["cap", "Google Classroom"], library: ["book", i18nText("ui.app.library")] };

/* ---------- utils ---------- */
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, { credentials: "same-origin", ...opts,
    headers: { "X-Requested-With": "reel", "Content-Type": "application/json" } }); }
  catch (e) { e.net = true; throw e; }
  if (r.status >= 500) { const e = new Error("server " + r.status); e.net = true; throw e; }
  if (r.status === 401 && !opts.login) { S.role = null; renderLogin(); throw new Error("auth"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} const error = new Error(m); error.status = r.status; throw error; }
  return r.json();
}
const fmtDur = (s) => { if (s == null) return "—"; s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}${i18nText("ui.app.h")} ${m}${i18nText("ui.app.min")}` : m ? `${m}${i18nText("ui.app.min")} ${x}${i18nText("ui.app.s")}` : `${x}${i18nText("ui.app.s")}`; };
const clock = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60, p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(x)}` : `${m}:${p(x)}`; };
const rel = (ts) => { if (!ts) return "—"; const d = Date.now() / 1000 - ts; if (d < 45) return i18nText("ui.app.just_now");
  if (d < 3600) return `${Math.round(d / 60)} ${i18nText("ui.app.min_ago")}`; if (d < 86400) return `${Math.round(d / 3600)} ${i18nText("ui.app.h_ago")}`;
  return new Date(ts * 1000).toLocaleDateString((I18N.language === "hy" ? "hy-AM" : "en-US"), { day: "numeric", month: "short" }); };
const pct = (j) => j.status === "done" ? 100 : Math.max(0, Math.min(99, Math.floor(j.progress?.percent || 0)));
const badge = (st) => `<span class="badge ${st}">${st === "running" ? '<span class="dot"></span>' : ""}${STATUS[st] || st}</span>`;
const set = (sel, html) => { const el = $(sel); if (el && S.last[sel] !== html) { el.innerHTML = html; S.last[sel] = html; } };
function toast(msg) { const el = document.createElement("div"); el.className = "toast"; el.textContent = msg; $("#toasts").append(el); setTimeout(() => el.remove(), 3500); }

/* ---------- theme ---------- */
function getTheme() { try { const value = localStorage.getItem("reel-theme"); return value === "dark" || value === "light" ? value : null; } catch (_) { return null; } }
function applyTheme() {
  document.documentElement.dataset.theme = getTheme() || "dark";
  syncThemeColor();
}
const isDark = () => document.documentElement.dataset.theme === "dark";
function syncThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
  const button = $("#theme");
  if (button) {
    button.setAttribute("aria-label", isDark() ? i18nText("ui.app.switch_to_light_theme") : i18nText("ui.app.switch_to_dark_theme"));
    button.setAttribute("aria-pressed", String(isDark()));
    button.innerHTML = ic(isDark() ? "sun" : "moon");
  }
}
function toggleTheme() {
  const theme = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("reel-theme", theme); } catch (_) {}
  syncThemeColor();
}

/* ---------- shell & routing ---------- */
const navLink = (href, icon, label, on) => {
  const compact = {[i18nText("ui.app.upload_lesson")]:i18nText("ui.app.upload"), [i18nText("ui.app.how_it_works")]:i18nText("ui.app.help"), [i18nText("ui.app.statistics")]:i18nText("ui.app.figures")}[label] || label;
  return `<a href="${href}" aria-label="${label}"${on ? ' aria-current="page"' : ""} class="${on ? "active" : ""}">${ic(icon)}<span class="nav-label">${label}</span><span class="nav-short" aria-hidden="true">${compact}</span></a>`;
};
/* Titles and descriptions are text; actions are trusted markup generated by a view. */
function pageHeading(title, description, actions = "") {
  return `<header class="top page-heading"><div class="page-heading-copy"><h1>${esc(title)}</h1>${description ? `<p>${esc(description)}</p>` : ""}</div>${actions ? `<div class="page-actions">${actions}</div>` : ""}</header>`;
}
function pageSkeleton(label = i18nText("ui.app.loading_page")) {
  return `<div class="page-loading" role="status" aria-label="${esc(label)}" aria-busy="true"><div class="page-loading-heading" aria-hidden="true"><div class="skeleton skeleton-title"></div><div class="skeleton skeleton-line"></div></div><div class="page-loading-cards" aria-hidden="true"><div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div></div></div>`;
}
function navigation(active) {
  const pages = S.role === "student" ? [["library","book",i18nText("ui.app.library")],["groups","layers",i18nText("ui.app.courses")]] :
    [["home","dashboard",i18nText("ui.app.dashboard")],["jobs","list",i18nText("ui.app.lessons")],
     ...(["admin","viewer","lecturer"].includes(S.role) ? [["library","book",i18nText("ui.app.library")],["stats","chart",i18nText("ui.app.statistics")]] : []),
     ...(canUpload() ? [["upload","upload",i18nText("ui.app.upload_lesson")],["record","mic",i18nText("ui.app.record")]] : []),["groups","layers",i18nText("ui.app.courses")],
     ...(S.role === "admin" ? [["settings","settings",i18nText("ui.admin.settings")],...(S.user ? [["users","users",i18nText("ui.accounts.users")]] : [])] : []),["about","book",i18nText("ui.app.help")]];
  const link = ([key,icon,label]) => navLink(key === "home" ? "#/" : "#/"+key,icon,label,active===key);
  const menu = pages.filter(([key])=>key !== "about");
  const primary = menu.slice(0,4), more = menu.slice(4);
  const wide = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(min-width:1100px)").matches;
  return `${primary.map(link).join("")}${more.length ? `<details class="nav-more${more.some(([key])=>key===active) ? " has-active" : ""}" data-was-wide="${Boolean(wide)}"${wide ? " open" : ""}><summary>${ic("more")}<span>${i18nText("ui.app.more")}</span></summary><div class="nav-popup">${more.map(link).join("")}</div></details>` : ""}`;
}
function reelBrand() {
  return (`<span class="reel-brand"><img src="/brand/reel-signal-depth.svg" alt="Reel"><span>${i18nText("ui.app.lecture_dashboard")}</span></span>`);
}
function headerAvatar() {
  const name = S.user?.name || S.user?.login || ({admin:i18nText("ui.accounts.administrator"),lecturer:i18nText("ui.groups.lecturer"),student:i18nText("ui.accounts.student"),uploader:i18nText("ui.app.uploader")}[S.role] || i18nText("ui.app.viewer"));
  return S.user?.avatar ? `<img src="${esc(S.user.avatar)}" alt="">` : esc(name.trim().split(/\s+/).slice(0,2).map(x=>x[0]).join(""));
}
function accountMenu() {
  return `<div class="account-menu"><button class="icon-btn header-avatar" id="account-toggle" type="button" aria-label="${i18nText("ui.app.open_account_menu")}" aria-haspopup="menu" aria-expanded="false" aria-controls="account-popup">${headerAvatar()}</button>
    <div id="account-popup" class="account-popup" role="menu" aria-label="${i18nText("ui.app.account_actions")}" hidden>
      <span class="account-menu-name">${esc(S.user?.name || i18nText("ui.app.account_actions"))}</span>
      <a role="menuitem" tabindex="-1" href="${S.user ? "#/profile" : "#/preferences"}">${ic("settings")} ${i18nText("ui.admin.settings")}</a>
      <a role="menuitem" tabindex="-1" class="header-help" href="#/about">${ic("book")} ${i18nText("ui.app.help")}</a>
      <button role="menuitem" tabindex="-1" type="button" id="logout">${ic("logout")} ${i18nText("ui.accounts.sign_out")}</button>
    </div></div>`;
}
function closeAccountMenu(focus = false) {
  const toggle=$("#account-toggle"),menu=$("#account-popup");
  if(menu)menu.hidden=true;
  if(toggle){toggle.setAttribute("aria-expanded","false");if(focus)toggle.focus();}
}
function openAccountMenu() {
  const toggle=$("#account-toggle"),menu=$("#account-popup");
  if(!menu||!toggle)return;
  menu.hidden=false;toggle.setAttribute("aria-expanded","true");menu.querySelector('[role="menuitem"]')?.focus();
}
function shell(active) {
  return `<a class="skip-link" data-skip-main href="#main">${i18nText("ui.app.skip_to_content")}</a><div class="shell"><aside class="side">
    <a class="brand brand-pair" href="#/" aria-label="${i18nText("ui.app.reel_home")}">${reelBrand()}</a>
    <nav class="nav" aria-label="${i18nText("ui.app.main_navigation")}">${navigation(active)}</nav>
    <div class="side-foot">${S.role === "admin" ? (`<span class="pill processing-status" id="worker" role="status"><span class="dot"></span>${i18nText("ui.app.processing_checking")}</span>`) : ""}
      <div class="header-controls">${languageSelector()}${accountMenu()}<button class="icon-btn" id="theme" title="${i18nText("ui.app.dark_light_theme")}" aria-label="${isDark() ? i18nText("ui.app.switch_to_light_theme") : i18nText("ui.app.switch_to_dark_theme")}" aria-pressed="${isDark()}">${ic(isDark() ? "sun" : "moon")}</button></div></div>
  </aside><main class="main" id="main" tabindex="-1">${pageSkeleton()}</main></div>
  ${canUpload() ? '<a class="up-dock" id="up-dock" href="#/upload" hidden></a>' : ""}
  <div id="net" class="net" role="status" hidden>${ic("alert")} ${i18nText("ui.app.connection_lost_reconnecting")}</div>`;
}
function viewPreferences() {
  set("#main",pageHeading(i18nText("ui.admin.settings"), i18nText("ui.app.choose_how_your_dashboard_looks"))+(`<section class="card"><h2>${i18nText("ui.app.dashboard_appearance")}</h2><p>${i18nText("ui.app.use_the_sun_or_moon_button_in")}</p></section>`));
}
function lessonToday() { return new Date(Date.now()+4*3600000).toISOString().slice(0,10); }
function formatLessonDate(value) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value||""))return i18nText("ui.app.not_set");
  return new Date(value+"T12:00:00").toLocaleDateString((I18N.language === "hy" ? "hy-AM" : "en-US"));
}

function route() {
  if(location.hash==="#/data-request" && S.user){clearInterval(S.poll);app.innerHTML=shell("profile");viewDataRequest();return;}
  clearInterval(S.poll); S.last = {}; S.sig = null;
  if (typeof publicAccountRoute === "function" && publicAccountRoute()) return;
  if (S.role === "bootstrap") {viewSetup();return;}
  if (location.hash === "#/profile" && S.user) {app.innerHTML=shell("profile");viewProfile();return;}
  if (S.role === "student" && !location.hash.startsWith("#/groups") && !/^#\/library(?:\/(?:\d+|settings|watch-stats))?(?:\?|$)/.test(location.hash) && !["#/about","#/preferences"].includes(location.hash)) { location.hash = "#/library"; }
  const h = location.hash || (S.role === "student" ? "#/library" : "#/"); let m, fn, active;
  if ((m = h.match(/^#\/jobs\/(\d+)/))) { fn = () => viewJob(+m[1]); active = "jobs"; }
  else if (h.startsWith("#/library") && S.role !== "uploader") { fn = viewLibrary; active = "library"; }
  else if (h.startsWith("#/stats") && ["admin", "viewer", "lecturer"].includes(S.role)) { fn = viewStats; active = "stats"; }
  else if (h.startsWith("#/jobs")) { fn = viewJobs; active = "jobs"; }
  else if (h.startsWith("#/groups")) { fn = viewGroups; active = "groups"; }
  else if (h.startsWith("#/audit") && S.role === "admin") { fn = viewAudit; active = "groups"; }
  else if (h.startsWith("#/upload")) { fn = viewUpload; active = "upload"; }
  else if (h.startsWith("#/record") && canUpload() && typeof viewRecord === "function") { fn = viewRecord; active = "record"; }
  else if (h.startsWith("#/users") && S.role === "admin" && S.user) { fn = viewUsers; active = "users"; }
  else if (h.startsWith("#/preferences")) { fn = viewPreferences; active = "preferences"; }
  else if (h.startsWith("#/settings")) { fn = () => (typeof viewSettings === "function" ? viewSettings() : null); active = "settings"; }
  else if (h.startsWith("#/about")) { fn = viewAbout; active = "about"; }
  else { fn = viewHome; active = "home"; }
  app.innerHTML = shell(active);
  const tick = async (force = false) => { if (document.hidden && !force) return;
    try { await fn(); const avatar=$("#account-toggle");if(avatar) set("#account-toggle",headerAvatar()); if (S.role !== "student") await updateWorker(); net(true); const error = $("#view-error"); if (error) error.remove(); }
    catch (e) {
      if (e.message === "auth") return;
      if (e.net) net(false); else console.warn(e);
      const main = $("#main");
      if (main && !$("#view-error")) main.insertAdjacentHTML("afterbegin", `<div class="alert err" id="view-error" role="alert"><div class="grow"><b>${i18nText("ui.app.could_not_load_page")}</b>${i18nText("ui.app.your_data_has_not_changed_try_again")}</div><button class="btn" data-retry-view>${i18nText("ui.admin.reload")}</button></div>`);
    } };
  tick(true); S.poll = setInterval(tick, 3000);
  if (typeof renderUpProgress === "function") renderUpProgress();
}
function net(ok) { const el = $("#net"); if (el) el.hidden = ok; }
async function updateWorker() {
  if (S.role !== "admin" || !$("#worker")) return;
  const d = S.summary && Date.now() - S.summary._t < 2500 ? S.summary : await loadSummary();
  if (S.role !== "admin") return;
  const w = d.worker;
  set("#worker", w.alive ? `<span class="dot live"></span>${i18nText("ui.app.processing_running")}${w.dry_run ? (` ${i18nText("ui.app.test")}`) : ""}` : `<span class="dot"></span>${i18nText("ui.app.processing_stopped")}`);
}
async function loadSummary() { const d = await api("/api/summary"); d._t = Date.now(); S.summary = d; return d; }

/* ---------- views ---------- */
function stat(icon, tone, k, v, s) { return `<div class="card stat"><div class="k"><span class="ic ${tone}">${ic(icon)}</span>${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`; }
const thumb = (j) => j.has_thumbnail ? `<div class="thumb"><img src="/api/jobs/${j.id}/thumbnail" loading="lazy" alt="${i18nText("ui.app.lesson_thumbnail")}" onerror="this.hidden=true"></div>` : `<div class="thumb poster" aria-hidden="true">${ic("play")}<span class="poster-label">${esc(j.subject || "Reel")}</span></div>`;
const emptyJobs = () => `<div class="empty"><div class="ic acc">${ic("folder")}</div><b>${i18nText("ui.app.no_lessons_yet")}</b><p>${i18nText("ui.app.lessons_and_queued_recordings_will_appear_here")}</p>${canUpload() ? `<a class="btn primary" href="#/upload">${ic("upload")} ${i18nText("ui.app.upload_lesson")}</a>` : ""}</div>`;

function phaseStates(j, order) {
  const cur = order.indexOf(j.current_stage);
  return PHASES.map((p) => {
    const idx = p.stages.map((s) => order.indexOf(s)), lo = Math.min(...idx), hi = Math.max(...idx);
    if (j.status === "done" || cur > hi) return "done";
    if (cur >= lo && cur <= hi) return j.status === "stopped" ? "failed" : "active";
    return "pending";
  });
}
function nowCard(j, order) {
  if (!j) return `<h2>${ic("play")} ${i18nText("ui.app.processing_now")}</h2><div class="empty"><div class="ic acc">${ic("folder")}</div><b>${i18nText("ui.app.the_queue_is_empty")}</b>
    <p>${i18nText("ui.app.no_new_lessons_are_queued_submit_a")}</p></div>`;
  const st = phaseStates(j, order);
  return `<h2>${ic("play")} ${i18nText("ui.app.processing_now")}</h2>
    <a href="#/jobs/${j.id}" class="row" style="align-items:flex-start"><div class="grow">
      <div class="ellipsis" style="font-weight:650;font-size:16px">${esc(j.title || j.file)}</div>
      <div class="muted">${esc(j.group)} · <b>${esc(j.current_label || "")}</b> ${i18nText("ui.app.started")} ${rel(j.created)}</div></div>${badge(j.status)}</a>
    <div class="row" style="margin-top:16px"><div class="bar grow" role="progressbar" aria-label="${i18nText("ui.app.processing_progress")}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct(j)}"><i style="width:${pct(j)}%"></i></div><b class="mono" data-progress-job="${j.id}" data-progress-value="${pct(j)}">${pct(j)}%</b><span class="muted">${typeof etaText === "function" ? etaText(j) : ""}</span></div>
    <div class="phases">${PHASES.map((p, i) => `<div class="phase ${st[i]}"><div class="ic">${ic(st[i] === "done" ? "check" : st[i] === "failed" ? "x" : p.icon)}</div>${p.label}</div>`).join("")}</div>`;
}
const inboxList = (d) => !d.inbox.length ? "" : `<div class="muted" style="font-weight:600;margin:16px 0 6px">${ic("folder")} ${i18nText("ui.app.waiting_in_the_folder")}</div>
  <div class="pubs">${d.inbox.map((f) => `<div class="pub">${ic("play")}<span class="grow ellipsis">${esc(f.file)}${f.parts > 1 ? ` · ${f.parts} ${i18nText("ui.app.parts")}` : ""}</span><span class="muted">${esc(f.group)} · ${f.size_mb} ${i18nText("ui.app.mb")}</span></div>`).join("")}</div>
  <p class="faint" style="margin:8px 0 0;font-size:12.5px">${d.worker.alive ? i18nText("ui.app.processing_starts_after_the_file_finishes_copying") : i18nText("ui.app.processing_is_paused_the_recording_will_stay")}</p>`;
const demoLessonCard = j=>`<article class="card job-card"><div class="job-card-body"><div class="row"><span class="pill">${esc(j.group)}</span><span class="chip">${i18nText("ui.app.demo")}</span></div><h3>${esc(j.title)}</h3><p>${esc(j.subject||'')}</p><p class="muted">${i18nText("ui.app.no_video")}</p></div></article>`;
const miniRow = (j) => j.demo?demoLessonCard(j):`<a class="jrow" href="#/jobs/${j.id}" style="grid-template-columns:72px minmax(0,1fr) auto">${thumb(j)}
  <div class="grow"><div class="t ellipsis">${esc(j.title || j.file)}</div><div class="sub">${j.dry ? (`${i18nText("ui.app.test_2")} `) : ""}${esc(j.group)}${j.subject ? " · " + esc(j.subject) : ""} · ${rel(j.updated)}</div></div>${badge(j.status)}</a>`;

const ACADEMY_STATUS = {published:i18nText("ui.app.published"),processing:i18nText("ui.app.processing_3"),running:i18nText("ui.app.processing_3"),queued:i18nText("ui.app.queued"),stopped:i18nText("ui.app.needs_attention"),done:i18nText("ui.app.ready"),demo:i18nText("ui.app.demo")};
function academyLessonCard(item) {
  const demo=Boolean(item.demo), href=demo ? null : item.published && item.playable && item.job_status === "done" && S.role !== "uploader" ? `#/library/${item.job_id}` : item.detail_url;
  const image=item.thumbnail ? `<img src="${esc(item.thumbnail)}" loading="lazy" alt="">` : `<span aria-hidden="true">${ic("book")}</span>`;
  const title=esc(item.title || i18nText("ui.app.lesson")), content=`<div class="academy-cover">${image}${demo ? (`<span class="badge demo">${i18nText("ui.app.demo")}</span>`) : ''}</div><div class="academy-lesson-body"><div class="row"><span class="pill">${esc(item.group)}</span><span class="lesson-state ${demo ? 'demo' : esc(item.status)}">${esc(ACADEMY_STATUS[item.status] || i18nText("ui.app.ready"))}</span></div><h3>${title}</h3><p>${esc(item.subject || i18nText("ui.app.no_subject_set"))}</p><div class="academy-lesson-date">${esc(formatLessonDate(item.lesson_date))}${demo ? (` ${i18nText("ui.app.no_video_2")}`) : ''}</div></div>`;
  return `<article class="card academy-lesson${demo ? ' is-demo' : ''}">${href ? `<a class="academy-lesson-link" href="${esc(href)}">${content}</a>` : content}</article>`;
}
async function viewHome() {
  const host=$("#main"),identity=`${S.role}:${S.user?.id ?? ""}`,hash=location.hash;
  const d=await api("/api/academy/overview"),c=d.cards;
  if(host!==$("#main") || hash!==location.hash || identity!==`${S.role}:${S.user?.id ?? ""}`)return;
  set("#main", pageHeading(i18nText("ui.app.dashboard"), i18nText("ui.app.your_groups_and_lessons_at_a_glance"),canUpload() ? `<a class="btn primary" href="#/upload">${ic("upload")} ${i18nText("ui.app.upload_lesson")}</a>` : "")+`
    <div class="grid stats academy-metrics">
      ${stat("layers","acc",i18nText("ui.app.active_groups"),c.active_groups,d.demo.active_groups ? `${i18nText("ui.app.of_these")} ${d.demo.active_groups}${i18nText("ui.app.are_demos")}` : i18nText("ui.app.current_learning_groups"))}
      ${stat("book","info",i18nText("ui.app.lessons_this_week"),c.week_lessons,`${d.actual.week_lessons} ${i18nText("ui.app.real")}${d.demo.week_lessons ? ` · ${d.demo.week_lessons} ${i18nText("ui.app.demo_2")}` : ""}`)}
      ${stat("clock","acc",i18nText("ui.app.processing"),c.processing,`${c.queued} ${i18nText("ui.app.lessons_queued")}`)}
      ${stat("check","ok",i18nText("ui.app.published_lessons"),c.published_lessons,i18nText("ui.app.ready_to_watch_in_the_library"))}
    </div>
    ${S.role === "admin" && d.warnings?.length ? `<section class="card academy-warnings" aria-label="${i18nText("ui.app.lessons_needing_attention")}"><h2>${ic("alert")} ${i18nText("ui.app.needs_attention")}</h2>${d.warnings.map(w=>`<a class="academy-warning" href="${esc(w.href)}"><span>${esc(w.message)}</span><b>${w.count} ${i18nText("ui.app.lessons_2")}</b></a>`).join("")}</section>` : ""}
    <section class="academy-section"><div class="section-heading"><h2>${i18nText("ui.app.recently_published_lessons")}</h2><a class="btn" href="#/library">${i18nText("ui.app.library_2")}</a></div>${d.latest.length ? `<div class="academy-grid">${d.latest.map(academyLessonCard).join("")}</div>` : `<div class="card empty"><b>${i18nText("ui.app.no_lessons_published_yet")}</b><p>${i18nText("ui.app.published_lessons_will_appear_here_with_their")}</p><a class="btn" href="${canUpload() ? '#/upload' : '#/library'}">${canUpload() ? i18nText("ui.app.upload_first_lesson") : i18nText("ui.app.open_library")}</a></div>`}</section>
    <section class="card academy-section"><div class="section-heading"><h2>${i18nText("ui.app.lessons_being_processed")}</h2><a class="btn" href="#/jobs">${i18nText("ui.app.all_lessons")}</a></div>${d.processing.length ? `<div class="academy-queue">${d.processing.map(item=>`<a href="${esc(item.detail_url)}"><span><b>${esc(item.title)}</b><small>${esc(item.group)} · ${esc(item.subject || i18nText("ui.app.no_subject_set"))}</small></span><span class="pill">${esc(ACADEMY_STATUS[item.status])}</span></a>`).join("")}</div>` : (`<p class="muted">${i18nText("ui.app.nothing_is_processing_right_now_new_lessons")}</p>`)}</section>`);
}

const feedRow = (e) => `<a class="ev ${e.status}" href="#/jobs/${e.job}"><span class="ic">${ic({ done: "check", failed: "x", running: "refresh" }[e.status] || "dot")}</span>
  <span class="grow ellipsis"><b>${esc(e.label)}</b> <span class="muted">· ${esc(e.title)}</span></span>
  <span class="muted mono hide-sm">${e.dur != null ? fmtDur(e.dur) : e.status === "running" ? "…" : ""}</span><span class="faint" style="min-width:70px;text-align:right">${rel(e.t)}</span></a>`;

const jobRow = (j) => j.demo?demoLessonCard(j):`<a class="job-card" href="#/jobs/${j.id}">${thumb(j)}
  <div class="job-card-body"><div class="row"><span class="pill">${esc(j.group)}</span><span class="grow"></span>${badge(j.status)}</div>
    <div class="t">${esc(j.title || j.file)}</div><div class="sub">${j.subject ? esc(j.subject) + " · " : ""}${esc(j.file)}</div>
    <div class="bar" role="progressbar" aria-label="${i18nText("ui.app.processing_progress")}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct(j)}"><i style="width:${pct(j)}%"></i></div><div class="job-card-meta"><span>${j.status === "running" ? esc(j.current_label) : `${j.done_stages}/${j.total_stages} ${i18nText("ui.app.stage")}`}</span><span><span data-progress-job="${j.id}" data-progress-value="${pct(j)}">${pct(j)}%</span> · ${typeof etaText === "function" ? etaText(j) : fmtDur(j.duration_sec)}</span></div>
    <div class="job-card-meta"><span>${j.dry ? i18nText("ui.app.test_3") : ""}</span><span>${rel(j.created)}</span></div></div></a>`;

function saveJobFilters() {
  const query=new URLSearchParams();
  for (const [key,value] of [["group",S.jobGroup],["subject",S.jobSubject],["status",S.jobFilter],["q",S.jobSearch]]) if(value) query.set(key,value);
  history.replaceState(null,"", "#/jobs"+(query.size ? "?"+query : ""));
}
let jobsRequest=0;
async function viewJobs() {
  const request=++jobsRequest;
  if (!$("#jobs-list")) {
    const query=new URLSearchParams(location.hash.split("?")[1]||"");
    S.jobGroup=query.get("group")||"";S.jobSubject=query.get("subject")||"";S.jobSearch=query.get("q")||"";
    const requested=query.get("status")||"",status=requested==="running" ? "processing" : requested;S.jobFilter=Object.hasOwn(ACADEMY_STATUS,status) ? status : "";
    $("#main").innerHTML=pageHeading(i18nText("ui.app.lessons"),i18nText("ui.app.find_lessons_by_group_subject_or_readiness"),canUpload() ? `<a class="btn primary" href="#/upload">${ic("upload")} ${i18nText("ui.app.upload_lesson")}</a>` : "")+`
      <section class="card academy-filters" aria-label="${i18nText("ui.app.lesson_filters")}"><label class="fld">${i18nText("ui.app.find_a_lesson")}<input class="input" type="search" id="search" placeholder="${i18nText("ui.app.title_group_or_subject")}" value="${esc(S.jobSearch)}"></label><label class="fld">${i18nText("ui.accounts.group")}<select id="job-group" class="input"></select></label><label class="fld">${i18nText("ui.app.subject")}<select id="job-subject" class="input"></select></label><label class="fld">${i18nText("ui.app.status")}<select id="job-status" class="input"><option value="">${i18nText("ui.app.all_statuses")}</option>${Object.entries(ACADEMY_STATUS).filter(([key])=>key!=="running").map(([key,label])=>`<option value="${key}"${S.jobFilter===key?' selected':''}>${label}</option>`).join("")}</select></label></section><div class="jobs-heading" id="jobs-count" role="status" aria-live="polite"></div><div id="jobs-list">${pageSkeleton(i18nText("ui.app.loading_lessons"))}</div>`;
  }
  const host=$("#jobs-list"),query=new URLSearchParams();
  if(S.jobGroup)query.set("group",S.jobGroup);if(S.jobSubject)query.set("subject",S.jobSubject);if(S.jobFilter)query.set("status",S.jobFilter);
  const d=await api("/api/academy/lessons?"+query);
  if(request!==jobsRequest || host!==$("#jobs-list") || !location.hash.startsWith("#/jobs"))return;
  const option=(item,selected)=>`<option value="${esc(item.name)}"${selected===item.name?' selected':''}>${esc(item.name)} (${item.count})</option>`;
  set("#job-group",(`<option value="">${i18nText("ui.accounts.all_groups")}</option>`)+d.filters.groups.map(item=>option(item,S.jobGroup)).join(""));
  set("#job-subject",(`<option value="">${i18nText("ui.app.all_subjects")}</option>`)+d.filters.subjects.map(item=>option(item,S.jobSubject)).join(""));
  const q=S.jobSearch.trim().toLocaleLowerCase("hy"),list=d.items.filter(item=>!q || [item.title,item.group,item.subject].some(value=>String(value||"").toLocaleLowerCase("hy").includes(q)));
  set("#jobs-count",`${list.length} ${i18nText("ui.app.lessons_3")}${list.some(item=>item.demo) ? ` · ${list.filter(item=>item.demo).length} ${i18nText("ui.app.demo_2")}` : ""}`);
  set("#jobs-list",list.length ? `<div class="academy-grid">${list.map(academyLessonCard).join("")}</div>` : `<div class="card empty"><div class="ic acc">${ic("book")}</div><b>${i18nText("ui.app.no_matching_lessons")}</b><p>${i18nText("ui.app.try_another_search_or_change_the_filters")}</p><button class="btn" data-clear-filters>${i18nText("ui.app.clear_filters")}</button></div>`);
}

async function viewGroups() {
  return renderGroupsPage();
}

/* ---------- upload helpers (transfer controller is in upload.js) ---------- */
const canUpload = () => ["admin","uploader","lecturer"].includes(S.role);
const fmtSize = (b) => (b >= 1e9 ? (b / 1e9).toFixed(2) + (` ${i18nText("ui.app.gb")}`) : Math.max(0, Math.round(b / 1e6)) + (` ${i18nText("ui.app.mb")}`));
const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });

/* ---------- about (for reviewers) ---------- */
const FLOW = [
  ["folder", i18nText("ui.app.intake"), i18nText("ui.app.add_a_file_or_a_folder_of"), "FFmpeg"],
  ["mic", i18nText("ui.app.transcription"), i18nText("ui.app.armenian_speech_with_english_technical_terms"), "MLX Whisper large-v3"],
  ["scissors", i18nText("ui.app.cuts"), i18nText("ui.app.only_confirmed_off_topic_conversations_and_announced"), "Ollama gemma3:12b"],
  ["volume", i18nText("stage.render"), i18nText("ui.app.video_cuts_and_ebu_r128_audio_normalization"), "FFmpeg loudnorm"],
  ["shield", i18nText("ui.accounts.privacy"), i18nText("ui.app.confidently_identified_student_faces_are_blurred_uncertain"), "YuNet + SFace"],
  ["sparkles", i18nText("ui.app.content"), i18nText("ui.app.title_description_chapters_with_correct_timestamps_and"), "Ollama"],
  ["send", i18nText("ui.app.publishing"), i18nText("ui.app.youtube_unlisted_playlist_slack_library_and_classroom_2"), i18nText("ui.app.apis")],
];
const GUARANTEES = [
  ["check", i18nText("ui.app.automatic_processing"), i18nText("ui.app.after_initial_setup_reel_handles_the_recording")],
  ["shield", i18nText("ui.accounts.privacy"), i18nText("ui.app.unblurred_recordings_are_never_published_or_shown")],
  ["alert", i18nText("ui.app.stops_on_critical_errors"), i18nText("ui.app.publishing_stops_on_critical_errors_uncertain_cuts")],
  ["refresh", i18nText("ui.app.safe_retries"), i18nText("ui.app.each_stage_is_saved_processing_resumes_after")],
  ["lock", i18nText("ui.app.local_and_free"), i18nText("ui.app.all_model_processing_runs_locally_on_a")],
  ["clock", i18nText("ui.app.accurate_chapters"), i18nText("ui.app.chapter_timestamps_are_calculated_after_cuts_to")],
];
const MEASURED = [
  [i18nText("ui.app.2_h_16_min_lesson"), i18nText("ui.app.transcription_25_min_5_4_real_time")],
  [i18nText("ui.app.2_h_16_min_lesson"), i18nText("ui.app.face_blurring_39_min_87_frames_s")],
  [i18nText("ui.app.privacy_test"), i18nText("ui.app.results_depend_on_face_size_and_position")],
  [i18nText("ui.app.50_min_lesson"), i18nText("ui.app.total_processing_45_min_macbook_pro_m5")],
  [i18nText("ui.app.cutting_test"), i18nText("ui.app.41_min_unedited_lesson_3_correctly_placed")],
];
function viewAbout() {
  const steps=[
    ["upload",i18nText("ui.app.upload_a_lesson"),i18nText("ui.app.choose_a_group_and_subject_then_add")],
    ["scissors",i18nText("ui.app.reel_prepares_the_lesson"),i18nText("ui.app.reel_processes_audio_removes_confirmed_non_lesson")],
    ["shield",i18nText("ui.app.protect_participants"),i18nText("ui.app.face_processing_and_final_privacy_check_results")],
    ["book",i18nText("ui.app.make_learning_accessible"),i18nText("ui.app.the_finished_lesson_appears_in_the_library")]];
  set("#main",pageHeading(i18nText("ui.app.help"),i18nText("ui.app.from_recording_to_finished_lesson_in_a"))+`
    <section class="card"><h2>${i18nText("ui.app.how_reel_works")}</h2><div class="flow">${steps.map(([icon,title,text],index)=>`<div class="fstep"><div class="ic acc">${ic(icon)}</div><b>${index+1}. ${title}</b><span class="muted">${text}</span></div>`).join("")}</div></section>
    <section class="card academy-section"><h2>${i18nText("ui.app.if_a_lesson_is_not_ready_yet")}</h2><p>${i18nText("ui.app.follow_its_progress_on_the_lessons_page")}</p><a class="btn" href="${S.role==='student'?'#/library':'#/jobs'}">${S.role==='student'?i18nText("ui.app.open_library"):i18nText("ui.app.open_lessons")}</a></section>`);
}

/* ---------- job detail ---------- */
function boardFramingLine(info) {
  if (!info || !Object.prototype.hasOwnProperty.call(info, "board") || typeof info.board !== "string") return "";
  let message;
  if (info.board === "framed") {
    const framing = info.framing || {};
    const number = (value) => Number.isFinite(value) ? String(Number(value.toFixed(2))) : "—";
    message = `${i18nText("ui.app.board_framing_zoom")} ${number(framing.zoom)}${i18nText("ui.app.tilt")} ${number(framing.rotate_deg)}°)`;
  } else {
    const reasons = [
      ["board not found reliably", i18nText("ui.app.board_not_confidently_detected")],
      ["portrait video", i18nText("ui.app.portrait_video")],
      ["framing would cut", i18nText("ui.app.too_much_of_the_board_would_be")],
      ["board detection failed", i18nText("ui.app.detection_failed")],
      ["framed render failed", i18nText("ui.app.cropped_version_could_not_be_rendered")]
    ];
    const match = reasons.find(([prefix]) => info.board.startsWith(prefix));
    message = `${i18nText("ui.app.full_frame")} ${match ? match[1] : info.board}`;
  }
  return `<div class="muted" style="font-size:12px;margin-top:4px">${esc(message)}</div>`;
}
function autoDecisionLine(info) {
  if (!info || !Object.prototype.hasOwnProperty.call(info, "auto_note")
      || typeof info.auto_note !== "string" || !info.auto_note.trim()) return "";
  const notes = [
    ["unclear windows", i18nText("ui.app.uncertain_segment_classification_the_full_lesson_was")],
    ["plan cut", i18nText("ui.app.the_plan_removed_too_much_content_the")],
    ["planned result too short", i18nText("ui.app.the_planned_result_was_too_short_the")],
    ["grounded chapters unavailable", i18nText("ui.app.chapters_were_unreliable_a_basic_title_and")],
    ["LLM metadata unavailable", i18nText("ui.app.a_basic_armenian_title_and_description_were")],
    ["LLM chapter wording unusable", i18nText("ui.app.suggested_wording_was_unusable_transcript_based_chapters")]
  ];
  const more = [
    [/^\d+ transcript segments have broken timestamps/, i18nText("ui.app.some_speech_timestamps_were_unreliable_the_full")],
    [/^loudness /, i18nText("ui.app.audio_levels_missed_the_target_the_video")],
    [/^the group's lecturer photos match/, i18nText("ui.app.the_group_s_lecturer_photos_did_not")],
    [/^lecturer choice file (unreadable|invalid)/, i18nText("ui.app.the_saved_lecturer_choice_was_invalid_automatic")],
    [/^the lecturer chosen on the job page/, i18nText("ui.app.the_selected_lecturer_was_not_recognized_again")],
    [/^lecturer unclear/, i18nText("ui.app.the_lecturer_could_not_be_identified_confidently")],
    [/^only \d+ clear photos/, i18nText("ui.app.too_few_clear_lecturer_samples_all_faces")],
    [/^the recognised lecturer matches/, i18nText("ui.app.too_few_lecturer_matches_all_faces_were")],
    [/^thumbnail skipped/, i18nText("ui.app.thumbnail_creation_failed_publishing_continued_without_it")],
    [/^thumbnail not set after/, i18nText("ui.app.the_youtube_thumbnail_could_not_be_set")],
  ];
  const message = info.auto_note.split(/; (?=(?:the |lecturer |only \d))/).map(part => {
    const known = notes.find(([prefix]) => part.startsWith(prefix));
    const extra = more.find(([pattern]) => pattern.test(part));
    return known ? known[1] : extra ? extra[1] : part;
  }).join(" · ");
  return `<div class="muted" style="font-size:12px;margin-top:4px">${i18nText("ui.app.automatic_decision")} ${esc(message)}</div>`;
}
async function viewJob(id) {
  if (!$("#jd-head")) {
    $("#main").innerHTML = `<a class="back" href="#/jobs">${ic("back")} ${i18nText("ui.app.all_lessons_2")}</a><div id="jd-head"></div><section id="jd-date" class="card lesson-date-card"></section><div id="jd-alert"></div><div id="jd-progress"></div><div id="jd-lecturer"></div>
      <div class="grid two"><div class="grid"><div class="card" id="jd-video"></div><div class="card" id="jd-tabs"></div></div>
      <div class="card"><h2>${ic("layers")} ${i18nText("ui.app.processing_stages")} <span class="muted" id="jd-total" style="margin-left:auto;font-size:13px;font-weight:500"></span></h2><ul class="steps" id="jd-steps"></ul></div></div>`;
  }
  const d = await api(`/api/jobs/${id}`); S.detail = d;
  renderLessonDate(d);
  const yt = d.publications.youtube;
  set("#jd-head", `<div class="top" style="margin-bottom:16px"><div class="grow"><h1>${esc(d.title || d.file)}</h1>
      <p>${esc(d.group)}${d.subject ? ` · <b>${esc(d.subject)}</b>` : ""} · <span class="mono">${esc(d.file)}</span> · ${rel(d.created)} · ${fmtDur(d.duration_sec)}${d.final_sec != null ? " → " + fmtDur(d.final_sec) : ""}</p></div>
    <div class="row">${d.dry ? `<span class="badge dry" title="${i18nText("ui.app.simulated_publications")}">${ic("alert")} ${i18nText("ui.app.test_processing")}</span>` : ""}${badge(d.status)}
      ${yt && yt.url ? `<a class="btn" href="${esc(yt.url)}" target="_blank" rel="noopener">${ic("yt")} YouTube ${ic("external")}</a>` : ""}
      ${d.publications.slack && d.publications.slack.url ? `<a class="btn" href="${esc(d.publications.slack.url)}" target="_blank" rel="noopener">${ic("hash")} Slack ${ic("external")}</a>` : ""}
      ${S.role === "admin" && d.status === "stopped" ? `<button class="btn primary" data-act="retry" data-id="${d.id}">${ic("refresh")} ${i18nText("ui.app.retry")}</button>` : ""}</div></div>`);
  set("#jd-progress", `<div class="lesson-progress"><div class="row"><span class="grow">${d.status === "done" ? i18nText("ui.app.lesson_ready") : i18nText("ui.app.processing_progress_2")}</span><span class="mono" data-progress-job="${d.id}" data-progress-value="${pct(d)}">${pct(d)}%</span></div><div class="bar" role="progressbar" aria-label="${i18nText("ui.app.lesson_processing_progress")}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct(d)}"><i style="width:${pct(d)}%"></i></div><p class="muted">${esc(d.current_label || "")} · ${typeof etaText === "function" ? etaText(d) : ""}</p></div>`);
  let alert = "";
  if (d.status === "stopped" && d.error) {
    const m = d.error.match(/'(\w+)' may already be posted/);
    const needSubj = /choose the lesson's subject/.test(d.error) && S.role === "admin" && (d.subject_choices || []).length;
    alert = `<div class="alert err">${ic("alert")}<div class="grow"><b>${i18nText("ui.app.processing_stopped_2")}</b>${d.hint ? `<div style="margin:4px 0 6px">${esc(d.hint)}</div>` : ""}<span class="mono faint" style="font-size:12px">${esc(d.error)}</span>
      ${m && S.role === "admin" ? `<div class="row" style="margin-top:10px"><span class="muted">${i18nText("ui.app.check")} ${esc(m[1])}${i18nText("ui.app.punctuation")}</span>
        <button class="btn" data-act="resolve" data-id="${d.id}" data-target="${esc(m[1])}" data-state="posted">${i18nText("ui.app.already_published")}</button>
        <button class="btn" data-act="resolve" data-id="${d.id}" data-target="${esc(m[1])}" data-state="not-posted">${i18nText("ui.app.not_published")}</button></div>` : ""}
      ${needSubj ? `<div class="row" style="margin-top:10px;flex-wrap:wrap"><span class="muted">${i18nText("ui.app.subject_2")}</span><select class="input" id="jd-subject" style="max-width:240px">${d.subject_choices.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</select>
        <button class="btn primary" data-act="subject" data-id="${d.id}">${i18nText("ui.app.save_and_retry")}</button></div>` : ""}</div></div>`;
  }
  if (d.upload_media && d.upload_media.low_resolution) alert += `<div class="alert warn">${ic("alert")}<div><b>${i18nText("ui.app.the_uploaded_video_is_below_1080p")}</b>${i18nText("ui.app.some_image_detail_may_be_lost_next")}</div></div>`;
  if (d.upload_media && d.upload_media.resolution_unknown) alert += `<div class="alert warn">${ic("alert")}<div>${i18nText("ui.app.some_parts_have_unconfirmed_dimensions_they_will")}</div></div>`;
  set("#jd-alert", alert);
  const tot = d.stages.reduce((a, s) => a + (s.duration_sec || 0), 0);
  set("#jd-total", tot ? `${i18nText("ui.app.total")} ${fmtDur(tot)}` : "");
  set("#jd-steps", d.stages.map((s) => {
    const icn = { done: "check", running: "refresh", failed: "x" }[s.status] || "dot";
    return `<li class="step ${s.status}"${s.status === "running" ? ' aria-current="step"' : ""}><span class="ic">${ic(icn)}</span><div><div class="n">${esc(s.label)}${s.attempts > 1 ? ` <span class="badge soft">${s.attempts} ${i18nText("ui.app.attempts")}</span>` : ""}</div>
      ${s.error && s.status !== "done" ? `<div class="e">${esc(s.error)}</div>` : ""}
      ${s.name === "render" ? boardFramingLine(s.info) : ""}${autoDecisionLine(s.info)}</div><span class="d">${s.duration_sec != null ? fmtDur(s.duration_sec) : s.status === "running" ? "…" : ""}</span></li>`;
  }).join(""));
  const sig = JSON.stringify([d.has_video, d.metadata && d.metadata.title, d.segments && d.segments.final_sec, d.privacy, d.publications, d.faces.length, d.has_thumbnail, d.has_preview, d.has_full, d.privacy_passed]);
  if (sig !== S.sig) { S.sig = sig; renderVideo(d); renderTabs(); }
  // lecturer.js: called on every 3 s poll; it must re-render only on change.
  if (S.role === "admin" && typeof renderLecturerPanel === "function") renderLecturerPanel(d);
}

function renderLessonDate(d) {
  const target=$("#jd-date");if(!target)return;
  if(S.role!=="admin") {set("#jd-date",`<b>${i18nText("ui.app.lesson_date")} ${esc(formatLessonDate(d.lesson_date))}</b>${d.lesson_date_inferred ? (`<p class="muted">${i18nText("ui.app.the_lesson_date_is_missing_the_import")}</p>`) : ''}`);return;}
  if(target.dataset.job===String(d.id))return;
  target.dataset.job=String(d.id);
  target.innerHTML=`<form id="lesson-date-form"><label class="fld"><span>${i18nText("ui.app.lesson_date_2")}</span><input class="input" id="lesson-date" type="date" max="${lessonToday()}" value="${esc(d.lesson_date||lessonToday())}" required></label><button class="btn" type="submit">${i18nText("ui.app.save_date")}</button></form><p class="muted" id="lesson-date-message" role="status">${d.lesson_date_inferred ? i18nText("ui.app.the_lesson_date_is_missing_the_import_2") : i18nText("ui.app.the_date_appears_on_the_lesson_page")}</p>`;
  $("#lesson-date-form").onsubmit=async event=>{
    event.preventDefault();const button=event.target.querySelector('button'),message=$("#lesson-date-message");button.disabled=true;
    try {const r=await api(`/api/jobs/${d.id}/date`,{method:"POST",body:JSON.stringify({lesson_date:$("#lesson-date").value})});d.lesson_date=r.lesson_date;d.lesson_date_inferred=false;message.textContent=i18nText("ui.app.lesson_date_saved");}
    catch(error){message.textContent=error.message;}finally{button.disabled=false;}
  };
}

function renderVideo(d) {
  const ch = (d.metadata && d.metadata.chapters) || [];
  const q = S.quality === "full" && d.has_full ? "full" : "preview";
  const qsw = d.has_video && d.has_preview && d.has_full ? `<div class="qswitch" role="group" aria-label="${i18nText("ui.app.quality")}">
      <button data-q="preview" class="${q === "preview" ? "on" : ""}" title="${i18nText("ui.app.smaller_file_for_smooth_online_playback")}">${i18nText("ui.app.web")}</button>
      <button data-q="full" class="${q === "full" ? "on" : ""}" title="${i18nText("ui.app.original_quality_large_file")}">HD</button></div>` : "";
  const why = !d.privacy_passed && S.role !== "admin" ? i18nText("ui.app.the_video_will_be_available_after_the") : i18nText("ui.app.the_video_will_appear_after_face_blurring");
  $("#jd-video").innerHTML = `<div class="row" style="margin-bottom:14px"><h2 class="grow" style="margin:0">${ic("play")} ${i18nText("ui.app.final_video")}</h2>${qsw}</div>
    ${d.has_video ? `<video controls preload="metadata" playsinline src="/api/jobs/${d.id}/video${q === "full" ? "?q=full" : ""}"${d.has_thumbnail ? ` poster="/api/jobs/${d.id}/thumbnail"` : ""}></video>`
      : `<div class="video-empty">${d.has_thumbnail ? `<img src="/api/jobs/${d.id}/thumbnail" alt="${i18nText("ui.app.lesson_thumbnail")}">` : ""}<div><div class="ic acc" style="margin:0 auto 10px">${ic(d.privacy_passed ? "clock" : "shield")}</div>${why}</div></div>`}
    ${ch.length ? `<div class="muted" style="margin-top:14px;font-weight:600">${i18nText("ui.app.chapters")} <span class="faint">${i18nText("ui.app.select_to_jump")}</span></div>
      <div class="chapters">${ch.map((c) => `<button class="chap" data-seek="${c.final_t}"><span class="mono">${clock(c.final_t)}</span><span>${esc(c.title)}</span></button>`).join("")}</div>` : ""}`;
  const v = $("#jd-video video");
  if (v && ch.length) v.addEventListener("timeupdate", () => {
    let k = -1; ch.forEach((c, i) => { if (v.currentTime + 0.5 >= c.final_t) k = i; });
    document.querySelectorAll("#jd-video .chap").forEach((b, i) => b.classList.toggle("on", i === k));
  });
}

function renderTabs() {
  const d = S.detail; if (!d || !$("#jd-tabs")) return;
  const tabs = [["meta", i18nText("ui.app.description")], ...(S.role !== "uploader" ? [["cuts", i18nText("ui.app.removed_sections")]] : []), ["privacy", i18nText("ui.accounts.privacy")], ["pub", i18nText("ui.app.publishing")]];
  if (S.role === "uploader" && S.tab === "cuts") S.tab = "meta";
  let body = "";
  if (S.tab === "meta") {
    const m = d.metadata, tr = (d.stages.find((s) => s.name === "transcribe") || {}).info || {}, rd = (d.stages.find((s) => s.name === "render") || {}).info || {};
    body = m ? `<div class="row" style="align-items:flex-start;gap:16px;flex-wrap:wrap">
        ${d.has_thumbnail ? `<img src="/api/jobs/${d.id}/thumbnail" alt="${i18nText("stage.thumbnail")}" style="width:220px;max-width:100%;border-radius:10px">` : ""}
        <div class="grow" style="min-width:220px"><div class="row"><div class="muted grow" style="font-weight:600">${i18nText("ui.app.title")}</div><button class="btn sm" data-copy="title">${ic("copy")} ${i18nText("ui.app.copy")}</button></div><div style="font-size:16px;font-weight:650;margin:2px 0 12px">${esc(m.title)}</div>
        <div class="kv">${tr.speed_x ? `<div><b>${tr.speed_x}x</b><small>${i18nText("ui.app.transcription_speed")}</small></div>` : ""}
          ${rd.lufs != null ? `<div><b>${rd.lufs.toFixed(1)} LUFS</b><small>${i18nText("ui.app.audio_level")}</small></div>` : ""}
          <div><b>${m.chapters.length}</b><small>${i18nText("ui.app.chapters_2")}</small></div></div></div></div>
      <div class="row" style="margin:16px 0 6px"><div class="muted grow" style="font-weight:600">${i18nText("ui.app.youtube_description")}</div><button class="btn sm" data-copy="desc">${ic("copy")} ${i18nText("ui.app.copy")}</button></div><pre class="desc">${esc(m.youtube_description || m.description)}</pre>`
      : `<div class="empty">${i18nText("ui.app.the_title_and_description_will_be_created")}</div>`;
  } else if (S.tab === "cuts") {
    body = (`<div id="cut-insight" aria-live="polite">${i18nText("ui.app.loading_cut_summary")}</div>`);
    if (typeof loadCuts === "function") setTimeout(() => loadCuts(d.id), 0);
  } else if (S.tab === "privacy") {
    const p = d.privacy, b = (d.stages.find((s) => s.name === "blur") || {}).info || {};
    body = p || b.blurred_boxes != null ? `<div class="kv">
        ${b.blurred_boxes != null ? `<div><b>${b.blurred_boxes}</b><small>${i18nText("ui.app.blurred_face_appearances")}</small></div>` : ""}
        ${b.lecturer_boxes != null ? `<div><b>${b.lecturer_boxes}</b><small>${i18nText("ui.app.lecturer_face_appearances_unblurred")}</small></div>` : ""}
        ${p ? `<div><b>${p.frames}</b><small>${i18nText("ui.app.frames_checked")}</small></div><div><b>${p.other_faces}</b><small>${i18nText("ui.app.unblurred_non_lecturer_face_appearances")}</small></div>` : ""}
        ${p && p.warnings != null ? `<div><b>${p.warnings}</b><small>${i18nText("ui.app.isolated_warnings_1_2_frames")}</small></div>` : ""}</div>
      <p class="muted" style="margin:14px 0 0">${i18nText("ui.app.student_faces_are_blurred_the_lecturer_is")}</p>
      ${d.faces.length ? `<div class="muted" style="font-weight:600;margin-top:14px">${i18nText("ui.app.faces_flagged_for_review")} <span class="faint">${i18nText("ui.app.select_to_view_in_the_video")}</span></div>
        <div class="faces">${d.faces.map((f) => `<figure class="${f.kind}"${f.t != null ? ` data-seek="${f.t}"` : ""} title="${f.kind === "hit" ? i18nText("ui.app.blocking_event") : i18nText("ui.app.isolated_warning")}"><img loading="lazy" src="/api/jobs/${d.id}/faces/${encodeURIComponent(f.name)}" alt=""><figcaption class="mono">${f.t != null ? clock(f.t) : ""}</figcaption></figure>`).join("")}</div>`
        : d.faces_hidden && p && (p.other_faces || p.warnings) ? `<p class="faint" style="margin:12px 0 0">${i18nText("ui.app.only_administrators_can_view_these_face_images")}</p>` : ""}`
      : `<div class="empty">${i18nText("ui.app.the_privacy_check_has_not_run_yet")}</div>`;
  } else {
    const SKIPPED = { classroom: ["classroom_post", i18nText("ui.accounts.disabled")], playlist: ["playlist", i18nText("ui.app.no_schedule_yet")] };
    const off = (k) => SKIPPED[k] && (d.stages || []).some((s) => s.name === SKIPPED[k][0] && s.info && s.info.skipped) ? SKIPPED[k][1] : "";
    body = `<div class="pubs">${Object.entries(PUB).map(([k, [i, l]]) => { const p = d.publications[k];
      if (off(k)) return `<div class="pub">${ic(i)}<span class="grow">${l}</span><span class="badge">${off(k)}</span></div>`;
      return `<div class="pub">${ic(i)}<span class="grow">${l}</span>${p ? `${p.dry ? `<span class="badge dry" title="${i18nText("ui.app.test_mode_nothing_was_actually_published")}">${ic("alert")} ${i18nText("ui.app.test_3")}</span>` : `<span class="badge done">${ic("check")} ${i18nText("ui.app.published")}</span>`}${p.url ? `<a class="icon-btn" href="${esc(p.url)}" target="_blank" rel="noopener" title="${i18nText("ui.app.open")}" aria-label="${i18nText("ui.app.open")} ${l}">${ic("external")}</a>` : ""}` : `<span class="badge soft">${i18nText("ui.accounts.pending")}</span>`}</div>`; }).join("")}</div>
      ${d.dry ? `<div class="alert warn" style="margin-top:14px">${ic("alert")}<div>${i18nText("ui.app.this_lesson_was_processed_in")} <b>${i18nText("ui.app.test_mode")}</b> ${i18nText("ui.app.publications_are_simulated_nothing_was_actually_published")}</div></div>` : ""}
      <p class="muted" style="margin:14px 0 0">${i18nText("ui.app.retries_do_not_publish_twice_each_publication")}</p>`;
  }
  $("#jd-tabs").innerHTML = `<div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button class="tab ${S.tab === k ? "on" : ""}" data-tab="${k}" role="tab" aria-selected="${S.tab === k}">${l}</button>`).join("")}</div>${body}`;
}

/* ---------- login ---------- */
function renderLogin(msg = "") {
  clearInterval(S.poll);
  app.innerHTML = `<div class="login"><div class="login-wrap"><section class="login-intro" aria-labelledby="login-title"><button class="icon-btn login-theme" id="theme" type="button" aria-label="${isDark() ? i18nText("ui.app.switch_to_light_theme") : i18nText("ui.app.switch_to_dark_theme")}" aria-pressed="${isDark()}">${ic(isDark() ? "sun" : "moon")}</button>${languageSelector()}<div class="login-brand-pair">${reelBrand()}</div><div class="eyebrow">${i18nText("ui.app.reel_lecture_dashboard")}</div><h1 id="login-title">${i18nText("ui.app.every_lesson")}<br>${i18nText("ui.app.ready_2")}<br><em>${i18nText("ui.app.to_share")}</em></h1><p>${i18nText("ui.app.your_knowledge_reaches_your_students_we_take")}</p><div class="login-flow" aria-hidden="true"><div class="row">${ic("play")} Reel</div><b>${i18nText("ui.app.from_lesson_to_learner")}</b><div class="row">${i18nText("ui.app.recording")} <span class="grow"></span>${i18nText("ui.app.processing_2")} <span class="grow"></span>${i18nText("ui.app.publishing")}</div></div></section>
    <div class="card"><div class="eyebrow">${i18nText("ui.app.your_workspace")}</div><h2>${i18nText("ui.app.welcome_back")}</h2><p>${i18nText("ui.app.sign_in_to_continue_with_your_lessons")}</p>
    <form id="login-form"><label class="fld"><span>${i18nText("ui.accounts.username_or_email")}</span><input class="input" id="login-identity" type="text" autocomplete="username" maxlength="254" placeholder="${i18nText("ui.accounts.username_or_email")}"></label><label class="fld"><span>${i18nText("ui.app.password")}</span><div class="password-field"><input class="input" type="password" id="pw" placeholder="${i18nText("ui.app.your_password")}" autocomplete="current-password" aria-describedby="login-err" required><button type="button" id="show-pw" aria-label="${i18nText("ui.app.show_password")}" aria-pressed="false">${i18nText("ui.app.show")}</button></div></label>
      <div class="err-text" id="login-err" role="alert">${esc(msg)}</div><button class="btn primary" type="submit">${i18nText("ui.app.sign_in")} ${ic("external")}</button></form><p><a class="account-text-link" href="#/forgot">${i18nText("ui.accounts.forgot_your_password")}</a></p><p class="muted">${i18nText("ui.app.registration_is_by_invitation_only_leave_the")}</p>
    <p class="login-foot login-policy">${ic("shield")} ${i18nText("ui.app.your_role_determines_your_access_student_information")}</p></div></div></div>`;
  $("#show-pw").onclick=()=>{const input=$("#pw"),shown=input.type==="password";input.type=shown?"text":"password";$("#show-pw").textContent=shown?i18nText("ui.app.hide"):i18nText("ui.app.show");$("#show-pw").setAttribute("aria-pressed",String(shown));};
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault(); const btn = e.target.querySelector('button[type="submit"]'); btn.disabled = true;
    try { const r = await api("/api/login", { method: "POST", body: JSON.stringify({ login: $("#login-identity").value, password: $("#pw").value }), login: true }); if(r.second_factor){viewSecondFactor();return;} S.role = r.role; S.user = r.user || null; if(r.bootstrap)location.hash="#/setup"; location.reload(); }
    catch (err) { $("#login-err").textContent = err.message === "wrong password" ? i18nText("ui.app.incorrect_password") : err.message; btn.disabled = false; }
  };
}

/* ---------- events ---------- */
document.addEventListener("click", async (e) => {
  if(!e.target.closest(".account-menu"))closeAccountMenu();
  if(e.target.closest('[role="menuitem"]')&&e.target.id!=="logout")closeAccountMenu();
  const t = e.target.closest("[data-filter],[data-href],[data-seek],[data-tab],[data-act],[data-q],[data-copy],[data-cutbar],[data-clear-filters],[data-skip-main],[data-retry-view],#theme,#logout,#account-toggle");
  if (!t) return;
  if (t.dataset.retryView !== undefined) { route(); return; }
  if (t.dataset.skipMain !== undefined) { e.preventDefault(); $("#main").focus(); return; }
  if (t.dataset.clearFilters !== undefined) {
    S.jobFilter = ""; S.jobSearch = ""; S.jobGroup = ""; S.jobSubject = ""; S.last = {}; saveJobFilters();
    const search = $("#search"); if (search) search.value = "";
    await viewJobs(); return;
  }
  if (t.id === "account-toggle") { e.preventDefault(); if(t.getAttribute("aria-expanded")==="true")closeAccountMenu(true);else openAccountMenu();return; }
  if (t.id === "theme") { toggleTheme(); t.innerHTML = ic(isDark() ? "sun" : "moon"); return; }
  if (t.id === "logout") { await api("/api/logout", { method: "POST" }).catch(() => {}); S.role = null; S.user = null; renderLogin(); return; }
  if (t.dataset.filter !== undefined) { S.jobFilter = t.dataset.filter; S.last = {}; saveJobFilters(); viewJobs(); return; }
  if (t.dataset.href) { location.hash = t.dataset.href; return; }
  if (t.dataset.seek) { const v = $("video"); if (v) { v.currentTime = +t.dataset.seek; v.play().catch(() => {}); } return; }
  if (t.dataset.tab) { S.tab = t.dataset.tab; renderTabs(); return; }
  if (t.dataset.q) { S.quality = t.dataset.q; const v = $("#jd-video video"), at = v ? v.currentTime : 0, playing = v && !v.paused;
    renderVideo(S.detail); const nv = $("#jd-video video");
    if (nv) nv.addEventListener("loadedmetadata", () => { nv.currentTime = at; if (playing) nv.play().catch(() => {}); }, { once: true });
    return; }
  if (t.dataset.copy) { const m = S.detail && S.detail.metadata; if (!m) return;
    const txt = t.dataset.copy === "title" ? m.title : (m.youtube_description || m.description);
    try { await navigator.clipboard.writeText(txt); toast(i18nText("ui.app.copied")); } catch (_) { toast(i18nText("ui.app.could_not_copy")); } return; }
  if (t.dataset.cutbar) { const g = S.detail && S.detail.segments, v = $("#jd-video video"); if (!g || !v) return;
    const r = t.getBoundingClientRect(), x = ((e.clientX - r.left) / r.width) * g.original_sec; let fin = 0;
    for (const [a, b] of g.keep || []) { if (x < a) break; if (x <= b) { fin += x - a; break; } fin += b - a; }
    v.currentTime = fin; v.play().catch(() => {}); return; }
  if (t.dataset.act === "subject") {
    const v = ($("#jd-subject") || {}).value;
    if (!v || !confirm(`${i18nText("ui.app.set")}${v}${i18nText("ui.app.as_the_subject_and_return_the_lesson")}`)) return;
    t.disabled = true;
    try { await api(`/api/jobs/${t.dataset.id}/subject`, { method: "POST", body: JSON.stringify({ subject: v }) });
      await api(`/api/jobs/${t.dataset.id}/retry`, { method: "POST" }); toast(i18nText("ui.app.subject_set_lesson_queued")); S.last = {}; }
    catch (err) { toast((`${i18nText("ui.app.error")} `) + err.message); t.disabled = false; }
    return;
  }
  if (t.dataset.act) {
    const msg = t.dataset.act === "retry" ? i18nText("ui.app.return_the_lesson_to_the_queue_completed")
      : t.dataset.state === "posted" ? `${i18nText("ui.app.confirm_the_publication_status_for")} ${t.dataset.target}${i18nText("ui.app.already_published_reel_will_not_publish_it")}`
      : `${i18nText("ui.app.confirm_the_publication_status_for")} ${t.dataset.target}${i18nText("ui.app.not_published_reel_will_publish_it")}`;
    if (!confirm(msg)) return;
    t.disabled = true;
    try {
      if (t.dataset.act === "retry") { await api(`/api/jobs/${t.dataset.id}/retry`, { method: "POST" }); toast(i18nText("ui.app.lesson_queued_completed_stages_will_not_run")); }
      if (t.dataset.act === "resolve") { await api(`/api/jobs/${t.dataset.id}/resolve`, { method: "POST", body: JSON.stringify({ target: t.dataset.target, state: t.dataset.state }) });
        await api(`/api/jobs/${t.dataset.id}/retry`, { method: "POST" }); toast(i18nText("ui.app.recorded_lesson_queued")); }
      S.last = {};
    } catch (err) { toast((`${i18nText("ui.app.error")} `) + err.message); t.disabled = false; }
  }
});
document.addEventListener("change", (e) => {
  const id=e.target.id;if(!["job-group","job-subject","job-status"].includes(id))return;
  if(id==="job-group"){S.jobGroup=e.target.value;S.jobSubject="";}else if(id==="job-subject")S.jobSubject=e.target.value;else S.jobFilter=e.target.value;
  saveJobFilters();viewJobs();
});
document.addEventListener("input", (e) => { if (e.target.id === "search") { S.jobSearch = e.target.value; saveJobFilters(); viewJobs(); } });
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.role) S.last = {}; });

/* ---------- boot lives in boot.js, after all view modules ---------- */

// Native button semantics plus roving menu focus; accounts.js/profile is kept independent.
document.addEventListener("keydown",event=>{
  const toggle=$("#account-toggle"),menu=$("#account-popup");if(!toggle||!menu)return;
  if(event.key==="Escape"&&!menu.hidden){event.preventDefault();closeAccountMenu(true);return;}
  if(event.target===toggle&&["Enter"," ","ArrowDown"].includes(event.key)){event.preventDefault();if(toggle.getAttribute("aria-expanded")==="true"&&event.key!=="ArrowDown")closeAccountMenu(true);else openAccountMenu();return;}
  if(!menu.hidden&&menu.contains(event.target)&&["ArrowDown","ArrowUp","Home","End"].includes(event.key)){
    event.preventDefault();const items=[...menu.querySelectorAll('[role="menuitem"]')],i=items.indexOf(document.activeElement);
    items[event.key==="Home"?0:event.key==="End"?items.length-1:(i+(event.key==="ArrowUp"?-1:1)+items.length)%items.length].focus();
  }
});
document.addEventListener("focusin",event=>{if(!event.target.closest('.account-menu'))closeAccountMenu();});

if(typeof MutationObserver!=="undefined"&&document.body){
  new MutationObserver(()=>{const node=$("#account-toggle");if(node&&S.user)set("#account-toggle",headerAvatar());}).observe(document.body,{childList:true,subtree:true});
}
