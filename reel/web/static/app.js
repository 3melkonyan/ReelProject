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

const STATUS = { queued: "Հերթում", running: "Մշակվում է", stopped: "Կանգնած", done: "Ավարտված" };
const PHASES = [
  { label: "Ընդունում", icon: "folder", stages: ["join", "probe", "audio"] },
  { label: "Տրանսկրիպցիա", icon: "mic", stages: ["transcribe"] },
  { label: "Կտրում", icon: "scissors", stages: ["segments"] },
  { label: "Մոնտաժ և ձայն", icon: "volume", stages: ["render"] },
  { label: "Գաղտնիություն", icon: "shield", stages: ["blur", "privacy", "preview"] },
  { label: "Վերնագիր, գլուխներ", icon: "sparkles", stages: ["metadata", "thumbnail"] },
  { label: "Հրապարակում", icon: "send", stages: ["youtube_upload", "playlist", "thumbnail_set", "slack_post", "classroom_post", "library_add"] },
];
const HOW = [
  ["folder", "1. Պանակ", "Տեսագրությունը վերբեռնում եք վահանակից՝ մեկ կամ մի քանի մասով"],
  ["mic", "2. Տրանսկրիպցիա", "Whisper-ը տեղային վերածում է խոսքը տեքստի"],
  ["scissors", "3. Կտրում", "LLM-ը գտնում է դասից դուրս զրույցներն ու ընդմիջումները"],
  ["volume", "4. Ձայն", "Ձայնի մակարդակը հավասարեցվում է (EBU R128)"],
  ["shield", "5. Գաղտնիություն", "Մշուշվում են վստահ ճանաչված ուսանողների դեմքերը"],
  ["sparkles", "6. Բովանդակություն", "Վերնագիր, նկարագրություն, գլուխներ և շապիկ"],
  ["send", "7. Հրապարակում", "YouTube (unlisted) + playlist, Slack, Library (Classroom՝ եթե միացված է խմբի համար)"],
];
const CUT_KIND = { before_class: "Դասից առաջ", after_class: "Դասից հետո", break: "Ընդմիջում" };
const LBL = { lesson: "դաս", offtopic: "դասից դուրս", unclear: "անորոշ", silence: "լռություն" };
const PUB = { youtube: ["yt", "YouTube"], playlist: ["layers", "Դասացանկ"], slack: ["hash", "Slack"], classroom: ["cap", "Google Classroom"], library: ["book", "Գրադարան"] };

/* ---------- utils ---------- */
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, { credentials: "same-origin", ...opts,
    headers: { "X-Requested-With": "reel", "Content-Type": "application/json" } }); }
  catch (e) { e.net = true; throw e; }
  if (r.status >= 500) { const e = new Error("server " + r.status); e.net = true; throw e; }
  if (r.status === 401 && !opts.login) { S.role = null; renderLogin(); throw new Error("auth"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
  return r.json();
}
const fmtDur = (s) => { if (s == null) return "—"; s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}ժ ${m}ր` : m ? `${m}ր ${x}վ` : `${x}վ`; };
const clock = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60, p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(x)}` : `${m}:${p(x)}`; };
const rel = (ts) => { if (!ts) return "—"; const d = Date.now() / 1000 - ts; if (d < 45) return "հենց հիմա";
  if (d < 3600) return `${Math.round(d / 60)} ր առաջ`; if (d < 86400) return `${Math.round(d / 3600)} ժ առաջ`;
  return new Date(ts * 1000).toLocaleDateString("hy-AM", { day: "numeric", month: "short" }); };
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
    button.setAttribute("aria-label", isDark() ? "Միացնել բաց թեման" : "Միացնել մուգ թեման");
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
  const compact = {"Վերբեռնել դաս":"Վերբեռնել", "Ինչպես է աշխատում":"Օգնություն", "Վիճակագրություն":"Թվեր"}[label] || label;
  return `<a href="${href}" aria-label="${label}"${on ? ' aria-current="page"' : ""} class="${on ? "active" : ""}">${ic(icon)}<span class="nav-label">${label}</span><span class="nav-short" aria-hidden="true">${compact}</span></a>`;
};
/* Titles and descriptions are text; actions are trusted markup generated by a view. */
function pageHeading(title, description, actions = "") {
  return `<header class="top page-heading"><div class="page-heading-copy"><h1>${esc(title)}</h1>${description ? `<p>${esc(description)}</p>` : ""}</div>${actions ? `<div class="page-actions">${actions}</div>` : ""}</header>`;
}
function pageSkeleton(label = "Բեռնում ենք էջը") {
  return `<div class="page-loading" role="status" aria-label="${esc(label)}" aria-busy="true"><div class="page-loading-heading" aria-hidden="true"><div class="skeleton skeleton-title"></div><div class="skeleton skeleton-line"></div></div><div class="page-loading-cards" aria-hidden="true"><div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div></div></div>`;
}
function navigation(active) {
  const pages = S.role === "student" ? [["library","book","Գրադարան"],["groups","layers","Դասընթացներ"]] :
    [["home","dashboard","Վահանակ"],["jobs","list","Դասեր"],
     ...(["admin","viewer","lecturer"].includes(S.role) ? [["library","book","Գրադարան"],["stats","chart","Վիճակագրություն"]] : []),
     ...(canUpload() ? [["upload","upload","Վերբեռնել դաս"],["record","mic","Ձայնագրել"]] : []),["groups","layers","Դասընթացներ"],
     ...(S.role === "admin" ? [["settings","settings","Կարգավորումներ"],...(S.user ? [["users","users","Օգտատերեր"]] : [])] : []),["about","book","Օգնություն"]];
  const link = ([key,icon,label]) => navLink(key === "home" ? "#/" : "#/"+key,icon,label,active===key);
  const menu = pages.filter(([key])=>key !== "about");
  const primary = menu.slice(0,4), more = menu.slice(4);
  const wide = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(min-width:1100px)").matches;
  return `${primary.map(link).join("")}${more.length ? `<details class="nav-more${more.some(([key])=>key===active) ? " has-active" : ""}" data-was-wide="${Boolean(wide)}"${wide ? " open" : ""}><summary>${ic("more")}<span>Ավելին</span></summary><div class="nav-popup">${more.map(link).join("")}</div></details>` : ""}`;
}
function reelBrand() {
  return '<span class="reel-brand"><img src="/brand/reel-signal-depth.svg" alt="Reel"><span>դասերի վահանակ</span></span>';
}
function headerAvatar() {
  const name = S.user?.name || S.user?.login || ({admin:"Ադմին",lecturer:"Դասախոս",student:"Ուսանող",uploader:"Վերբեռնող"}[S.role] || "Դիտորդ");
  return S.user?.avatar ? `<img src="${esc(S.user.avatar)}" alt="">` : esc(name.trim().split(/\s+/).slice(0,2).map(x=>x[0]).join(""));
}
function accountMenu() {
  return `<div class="account-menu"><button class="icon-btn header-avatar" id="account-toggle" type="button" aria-label="Բացել հաշվի մենյուն" aria-haspopup="menu" aria-expanded="false" aria-controls="account-popup">${headerAvatar()}</button>
    <div id="account-popup" class="account-popup" role="menu" aria-label="Հաշվի գործողություններ" hidden>
      <span class="account-menu-name">${esc(S.user?.name || "Հաշվի գործողություններ")}</span>
      <a role="menuitem" tabindex="-1" href="${S.user ? "#/profile" : "#/preferences"}">${ic("settings")} Կարգավորումներ</a>
      <a role="menuitem" tabindex="-1" class="header-help" href="#/about">${ic("book")} Օգնություն</a>
      <button role="menuitem" tabindex="-1" type="button" id="logout">${ic("logout")} Ելք</button>
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
  return `<a class="skip-link" data-skip-main href="#main">Անցնել բովանդակությանը</a><div class="shell"><aside class="side">
    <a class="brand brand-pair" href="#/" aria-label="Reel գլխավոր">${reelBrand()}</a>
    <nav class="nav" aria-label="Հիմնական նավիգացիա">${navigation(active)}</nav>
    <div class="side-foot">${S.role === "admin" ? '<span class="pill processing-status" id="worker" role="status"><span class="dot"></span>Մշակում՝ ստուգվում է…</span>' : ""}
      <div class="header-controls">${accountMenu()}<button class="icon-btn" id="theme" title="Մուգ/բաց թեմա" aria-label="${isDark() ? "Միացնել բաց թեման" : "Միացնել մուգ թեման"}" aria-pressed="${isDark()}">${ic(isDark() ? "sun" : "moon")}</button></div></div>
  </aside><main class="main" id="main" tabindex="-1">${pageSkeleton()}</main></div>
  ${canUpload() ? '<a class="up-dock" id="up-dock" href="#/upload" hidden></a>' : ""}
  <div id="net" class="net" role="status" hidden>${ic("alert")} Կապը վահանակի հետ ընդհատվել է. կրկին փորձում ենք…</div>`;
}
function viewPreferences() {
  set("#main",pageHeading("Կարգավորումներ", "Փոխեք վահանակի տեսքը ձեր նախընտրությամբ։")+'<section class="card"><h2>Վահանակի տեսքը</h2><p>Թեման կարող եք փոխել վերնագծի արևի կամ լուսնի կոճակով։ Ընտրությունը պահպանվում է այս սարքում։</p></section>');
}
function lessonToday() { return new Date(Date.now()+4*3600000).toISOString().slice(0,10); }
function formatLessonDate(value) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value||""))return "Նշված չէ";
  return new Date(value+"T12:00:00").toLocaleDateString("hy-AM");
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
      if (main && !$("#view-error")) main.insertAdjacentHTML("afterbegin", `<div class="alert err" id="view-error" role="alert"><div class="grow"><b>Էջը բեռնել չհաջողվեց</b>Ձեր տվյալները չեն փոխվել։ Փորձեք կրկին։</div><button class="btn" data-retry-view>Կրկին բեռնել</button></div>`);
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
  set("#worker", w.alive ? `<span class="dot live"></span>Մշակում՝ աշխատում է${w.dry_run ? " · փորձնական" : ""}` : `<span class="dot"></span>Մշակում՝ կանգնած`);
}
async function loadSummary() { const d = await api("/api/summary"); d._t = Date.now(); S.summary = d; return d; }

/* ---------- views ---------- */
function stat(icon, tone, k, v, s) { return `<div class="card stat"><div class="k"><span class="ic ${tone}">${ic(icon)}</span>${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`; }
const thumb = (j) => j.has_thumbnail ? `<div class="thumb"><img src="/api/jobs/${j.id}/thumbnail" loading="lazy" alt="Դասի շապիկ" onerror="this.hidden=true"></div>` : `<div class="thumb poster" aria-hidden="true">${ic("play")}<span class="poster-label">${esc(j.subject || "Reel")}</span></div>`;
const emptyJobs = () => `<div class="empty"><div class="ic acc">${ic("folder")}</div><b>Դեռ դասեր չկան</b><p>Դասերն ու մշակման հերթում եղած տեսագրությունները կհայտնվեն այստեղ։</p>${canUpload() ? `<a class="btn primary" href="#/upload">${ic("upload")} Վերբեռնել դաս</a>` : ""}</div>`;

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
  if (!j) return `<h2>${ic("play")} Հիմա մշակվում է</h2><div class="empty"><div class="ic acc">${ic("folder")}</div><b>Հերթը դատարկ է</b>
    <p>Մշակման հերթում նոր դաս չկա։ Տեսագրությունը կարող եք ուղարկել վերբեռնման էջից։</p></div>`;
  const st = phaseStates(j, order);
  return `<h2>${ic("play")} Հիմա մշակվում է</h2>
    <a href="#/jobs/${j.id}" class="row" style="align-items:flex-start"><div class="grow">
      <div class="ellipsis" style="font-weight:650;font-size:16px">${esc(j.title || j.file)}</div>
      <div class="muted">${esc(j.group)} · <b>${esc(j.current_label || "")}</b> · սկսվել է ${rel(j.created)}</div></div>${badge(j.status)}</a>
    <div class="row" style="margin-top:16px"><div class="bar grow" role="progressbar" aria-label="Մշակման առաջընթաց" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct(j)}"><i style="width:${pct(j)}%"></i></div><b class="mono" data-progress-job="${j.id}" data-progress-value="${pct(j)}">${pct(j)}%</b><span class="muted">${typeof etaText === "function" ? etaText(j) : ""}</span></div>
    <div class="phases">${PHASES.map((p, i) => `<div class="phase ${st[i]}"><div class="ic">${ic(st[i] === "done" ? "check" : st[i] === "failed" ? "x" : p.icon)}</div>${p.label}</div>`).join("")}</div>`;
}
const inboxList = (d) => !d.inbox.length ? "" : `<div class="muted" style="font-weight:600;margin:16px 0 6px">${ic("folder")} Սպասում են պանակում</div>
  <div class="pubs">${d.inbox.map((f) => `<div class="pub">${ic("play")}<span class="grow ellipsis">${esc(f.file)}${f.parts > 1 ? ` · ${f.parts} մաս` : ""}</span><span class="muted">${esc(f.group)} · ${f.size_mb} ՄԲ</span></div>`).join("")}</div>
  <p class="faint" style="margin:8px 0 0;font-size:12.5px">${d.worker.alive ? "Ֆայլը կսկսի մշակվել, երբ ամբողջությամբ պատճենվի (≈1 րոպե կայունությունից հետո)։" : "Մշակումը ժամանակավորապես դադարեցված է․ տեսագրությունը կպահպանվի հերթում։"}</p>`;
const demoLessonCard = j=>`<article class="card job-card"><div class="job-card-body"><div class="row"><span class="pill">${esc(j.group)}</span><span class="chip">Ցուցադրական</span></div><h3>${esc(j.title)}</h3><p>${esc(j.subject||'')}</p><p class="muted">Առանց տեսանյութի</p></div></article>`;
const miniRow = (j) => j.demo?demoLessonCard(j):`<a class="jrow" href="#/jobs/${j.id}" style="grid-template-columns:72px minmax(0,1fr) auto">${thumb(j)}
  <div class="grow"><div class="t ellipsis">${esc(j.title || j.file)}</div><div class="sub">${j.dry ? "փորձնական · " : ""}${esc(j.group)}${j.subject ? " · " + esc(j.subject) : ""} · ${rel(j.updated)}</div></div>${badge(j.status)}</a>`;

const ACADEMY_STATUS = {published:"Հրապարակված",processing:"Մշակվում է",running:"Մշակվում է",queued:"Հերթում",stopped:"Ուշադրության կարիք ունի",done:"Պատրաստ է",demo:"Ցուցադրական"};
function academyLessonCard(item) {
  const demo=Boolean(item.demo), href=demo ? null : item.published && item.playable && item.job_status === "done" && S.role !== "uploader" ? `#/library/${item.job_id}` : item.detail_url;
  const image=item.thumbnail ? `<img src="${esc(item.thumbnail)}" loading="lazy" alt="">` : `<span aria-hidden="true">${ic("book")}</span>`;
  const title=esc(item.title || "Դաս"), content=`<div class="academy-cover">${image}${demo ? '<span class="badge demo">Ցուցադրական</span>' : ''}</div><div class="academy-lesson-body"><div class="row"><span class="pill">${esc(item.group)}</span><span class="lesson-state ${demo ? 'demo' : esc(item.status)}">${esc(ACADEMY_STATUS[item.status] || "Պատրաստ է")}</span></div><h3>${title}</h3><p>${esc(item.subject || "Առարկան նշված չէ")}</p><div class="academy-lesson-date">${esc(formatLessonDate(item.lesson_date))}${demo ? ' · Առանց տեսանյութի' : ''}</div></div>`;
  return `<article class="card academy-lesson${demo ? ' is-demo' : ''}">${href ? `<a class="academy-lesson-link" href="${esc(href)}">${content}</a>` : content}</article>`;
}
async function viewHome() {
  const host=$("#main"),identity=`${S.role}:${S.user?.id ?? ""}`,hash=location.hash;
  const d=await api("/api/academy/overview"),c=d.cards;
  if(host!==$("#main") || hash!==location.hash || identity!==`${S.role}:${S.user?.id ?? ""}`)return;
  set("#main", pageHeading("Վահանակ", "Ձեր ակադեմիայի խմբերն ու դասերը՝ մեկ հայացքով։",canUpload() ? `<a class="btn primary" href="#/upload">${ic("upload")} Վերբեռնել դաս</a>` : "")+`
    <div class="grid stats academy-metrics">
      ${stat("layers","acc","Ակտիվ խմբեր",c.active_groups,d.demo.active_groups ? `Դրանցից ${d.demo.active_groups}՝ ցուցադրական` : "Ընթացիկ ուսումնական խմբեր")}
      ${stat("book","info","Այս շաբաթվա դասեր",c.week_lessons,`${d.actual.week_lessons} իրական${d.demo.week_lessons ? ` · ${d.demo.week_lessons} ցուցադրական` : ""}`)}
      ${stat("clock","acc","Մշակման մեջ",c.processing,`${c.queued} դաս սպասում է հերթում`)}
      ${stat("check","ok","Հրապարակված դասեր",c.published_lessons,"Պատրաստ են դիտելու գրադարանում")}
    </div>
    ${S.role === "admin" && d.warnings?.length ? `<section class="card academy-warnings" aria-label="Ուշադրություն պահանջող դասեր"><h2>${ic("alert")} Ուշադրության կարիք ունի</h2>${d.warnings.map(w=>`<a class="academy-warning" href="${esc(w.href)}"><span>${esc(w.message)}</span><b>${w.count} դաս →</b></a>`).join("")}</section>` : ""}
    <section class="academy-section"><div class="section-heading"><h2>Վերջին հրապարակված դասերը</h2><a class="btn" href="#/library">Գրադարան →</a></div>${d.latest.length ? `<div class="academy-grid">${d.latest.map(academyLessonCard).join("")}</div>` : `<div class="card empty"><b>Առաջին դասը դեռ չի հրապարակվել</b><p>Հրապարակումից հետո դասերն այստեղ կհայտնվեն իրենց շապիկներով։</p><a class="btn" href="${canUpload() ? '#/upload' : '#/library'}">${canUpload() ? 'Վերբեռնել առաջին դասը' : 'Բացել գրադարանը'}</a></div>`}</section>
    <section class="card academy-section"><div class="section-heading"><h2>Մշակվող դասերը</h2><a class="btn" href="#/jobs">Բոլոր դասերը →</a></div>${d.processing.length ? `<div class="academy-queue">${d.processing.map(item=>`<a href="${esc(item.detail_url)}"><span><b>${esc(item.title)}</b><small>${esc(item.group)} · ${esc(item.subject || "Առարկան նշված չէ")}</small></span><span class="pill">${esc(ACADEMY_STATUS[item.status])}</span></a>`).join("")}</div>` : '<p class="muted">Այս պահին մշակվող դաս չկա։ Նոր դասերը կհայտնվեն այստեղ։</p>'}</section>`);
}

const feedRow = (e) => `<a class="ev ${e.status}" href="#/jobs/${e.job}"><span class="ic">${ic({ done: "check", failed: "x", running: "refresh" }[e.status] || "dot")}</span>
  <span class="grow ellipsis"><b>${esc(e.label)}</b> <span class="muted">· ${esc(e.title)}</span></span>
  <span class="muted mono hide-sm">${e.dur != null ? fmtDur(e.dur) : e.status === "running" ? "…" : ""}</span><span class="faint" style="min-width:70px;text-align:right">${rel(e.t)}</span></a>`;

const jobRow = (j) => j.demo?demoLessonCard(j):`<a class="job-card" href="#/jobs/${j.id}">${thumb(j)}
  <div class="job-card-body"><div class="row"><span class="pill">${esc(j.group)}</span><span class="grow"></span>${badge(j.status)}</div>
    <div class="t">${esc(j.title || j.file)}</div><div class="sub">${j.subject ? esc(j.subject) + " · " : ""}${esc(j.file)}</div>
    <div class="bar" role="progressbar" aria-label="Մշակման առաջընթաց" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct(j)}"><i style="width:${pct(j)}%"></i></div><div class="job-card-meta"><span>${j.status === "running" ? esc(j.current_label) : `${j.done_stages}/${j.total_stages} փուլ`}</span><span><span data-progress-job="${j.id}" data-progress-value="${pct(j)}">${pct(j)}%</span> · ${typeof etaText === "function" ? etaText(j) : fmtDur(j.duration_sec)}</span></div>
    <div class="job-card-meta"><span>${j.dry ? "Փորձնական" : ""}</span><span>${rel(j.created)}</span></div></div></a>`;

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
    $("#main").innerHTML=pageHeading("Դասեր","Գտեք դասը խմբով, առարկայով կամ պատրաստ լինելու վիճակով։",canUpload() ? `<a class="btn primary" href="#/upload">${ic("upload")} Վերբեռնել դաս</a>` : "")+`
      <section class="card academy-filters" aria-label="Դասերի զտիչներ"><label class="fld">Որոնել դաս<input class="input" type="search" id="search" placeholder="Անվանում, խումբ կամ առարկա" value="${esc(S.jobSearch)}"></label><label class="fld">Խումբ<select id="job-group" class="input"></select></label><label class="fld">Առարկա<select id="job-subject" class="input"></select></label><label class="fld">Վիճակ<select id="job-status" class="input"><option value="">Բոլոր վիճակները</option>${Object.entries(ACADEMY_STATUS).filter(([key])=>key!=="running").map(([key,label])=>`<option value="${key}"${S.jobFilter===key?' selected':''}>${label}</option>`).join("")}</select></label></section><div class="jobs-heading" id="jobs-count" role="status" aria-live="polite"></div><div id="jobs-list">${pageSkeleton("Բեռնում ենք դասերը")}</div>`;
  }
  const host=$("#jobs-list"),query=new URLSearchParams();
  if(S.jobGroup)query.set("group",S.jobGroup);if(S.jobSubject)query.set("subject",S.jobSubject);if(S.jobFilter)query.set("status",S.jobFilter);
  const d=await api("/api/academy/lessons?"+query);
  if(request!==jobsRequest || host!==$("#jobs-list") || !location.hash.startsWith("#/jobs"))return;
  const option=(item,selected)=>`<option value="${esc(item.name)}"${selected===item.name?' selected':''}>${esc(item.name)} (${item.count})</option>`;
  set("#job-group",'<option value="">Բոլոր խմբերը</option>'+d.filters.groups.map(item=>option(item,S.jobGroup)).join(""));
  set("#job-subject",'<option value="">Բոլոր առարկաները</option>'+d.filters.subjects.map(item=>option(item,S.jobSubject)).join(""));
  const q=S.jobSearch.trim().toLocaleLowerCase("hy"),list=d.items.filter(item=>!q || [item.title,item.group,item.subject].some(value=>String(value||"").toLocaleLowerCase("hy").includes(q)));
  set("#jobs-count",`${list.length} դաս${list.some(item=>item.demo) ? ` · ${list.filter(item=>item.demo).length} ցուցադրական` : ""}`);
  set("#jobs-list",list.length ? `<div class="academy-grid">${list.map(academyLessonCard).join("")}</div>` : `<div class="card empty"><div class="ic acc">${ic("book")}</div><b>Համապատասխան դաս չգտնվեց</b><p>Փորձեք այլ բառ կամ փոխեք ընտրված զտիչները։</p><button class="btn" data-clear-filters>Մաքրել զտիչները</button></div>`);
}

async function viewGroups() {
  return renderGroupsPage();
}

/* ---------- upload helpers (transfer controller is in upload.js) ---------- */
const canUpload = () => ["admin","uploader","lecturer"].includes(S.role);
const fmtSize = (b) => (b >= 1e9 ? (b / 1e9).toFixed(2) + " ԳԲ" : Math.max(0, Math.round(b / 1e6)) + " ՄԲ");
const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });

/* ---------- about (for reviewers) ---------- */
const FLOW = [
  ["folder", "Ընդունում", "Նոր ֆայլը կամ մասերով դասի թղթապանակը խմբի պանակում. մասերը միանում են առանց որակի կորստի, կրկնօրինակների ստուգում (SHA-256)", "FFmpeg"],
  ["mic", "Տրանսկրիպցիա", "Հայերեն խոսք + անգլերեն տերմիններ", "MLX Whisper large-v3"],
  ["scissors", "Կտրում", "Միայն վստահ դասից դուրս զրույց և հայտարարված ընդմիջումներ․ անորոշը պահպանվում է", "Ollama gemma3:12b"],
  ["volume", "Մոնտաժ և ձայն", "Կտրում + ձայնի հավասարեցում EBU R128 (−16 LUFS)", "FFmpeg loudnorm"],
  ["shield", "Գաղտնիություն", "Վստահ ուսանողի դեմքի մշուշում, անորոշի պահպանում և վերջնական ստուգում", "YuNet + SFace"],
  ["sparkles", "Բովանդակություն", "Վերնագիր, նկարագրություն, գլուխներ (ճիշտ ժամերով), շապիկ", "Ollama"],
  ["send", "Հրապարակում", "YouTube (unlisted) + playlist, Slack, Academy Library, Classroom (եթե միացված է խմբի համար)", "API-ներ"],
];
const GUARANTEES = [
  ["check", "Առանց ձեռքի աշխատանքի", "Մեկանգամյա կարգավորումից հետո ամեն ինչ ավտոմատ է՝ ֆայլից մինչև հրապարակում։"],
  ["shield", "Գաղտնիություն", "Չմշուշված տեսանյութը երբեք չի հրապարակվում և չի ցուցադրվում։ Վերջնական ստուգումը կատարվում է արդեն մշուշված տեսանյութի վրա։"],
  ["alert", "Կանգ՝ կարևոր սխալի դեպքում", "Կրիտիկական սխալի դեպքում հրապարակումը կանգնում է։ Անորոշ կտրումը չի կիրառվում, անորոշ դեմքը չի մշուշվում։"],
  ["refresh", "Անվտանգ կրկնություն", "Ամեն փուլ պահվում է. վթարից հետո շարունակում է նույն տեղից, կրկնակի հրապարակում չի լինում։"],
  ["lock", "Տեղային և անվճար", "Ամբողջ AI մշակումը MacBook-ի վրա է՝ առանց վճարովի API-ների։ Տեսանյութը դուրս է գնում միայն հրապարակման համար։"],
  ["clock", "Ճիշտ գլուխներ", "Գլուխների ժամերը հաշվարկվում են կտրումներից հետո, ուստի համընկնում են վերջնական տեսանյութի հետ։"],
];
const MEASURED = [
  ["2ժ16ր դաս", "տրանսկրիպցիա ≈ 25 ր (5.4x արագ իրական ժամանակից)"],
  ["2ժ16ր դաս", "դեմքերի մշուշում ≈ 39 ր (87 կադր/վրկ), ստուգում ≈ 8 ր"],
  ["Գաղտնիության թեստ", "արդյունքը կախված է դեմքի չափից և դիրքից․ անորոշ դեմքը պահվում է"],
  ["50 ր դաս", "ամբողջ մշակումը ≈ 45 ր (MacBook Pro, M5 Pro)"],
  ["Կտրման թեստ", "41 ր «չխմբագրված» դաս՝ 3 կտրում ճիշտ տեղերում, 32 ր արդյունք, գլուխների ժամերը՝ ճշգրիտ"],
];
function viewAbout() {
  const steps=[
    ["upload","Վերբեռնեք դասը","Ընտրեք խումբն ու առարկան և ավելացրեք տեսագրությունը։"],
    ["scissors","Reel-ը պատրաստում է դասը","Համակարգը մշակում է ձայնը, հեռացնում վստահ ավելորդ հատվածները և պահպանում անորոշները։"],
    ["shield","Պաշտպանում է մասնակիցներին","Դեմքերի մշակման և վերջնական ստուգման արդյունքը տեսանելի է դասի էջում։"],
    ["book","Բացում է սովորելու ճանապարհը","Պատրաստ դասը հայտնվում է գրադարանում՝ գլուխներով, տեքստով և դիտման ընթացքով։"]];
  set("#main",pageHeading("Օգնություն","Տեսագրությունից մինչև պատրաստ դաս՝ պարզ քայլերով։")+`
    <section class="card"><h2>Ինչպես է աշխատում Reel-ը</h2><div class="flow">${steps.map(([icon,title,text],index)=>`<div class="fstep"><div class="ic acc">${ic(icon)}</div><b>${index+1}. ${title}</b><span class="muted">${text}</span></div>`).join("")}</div></section>
    <section class="card academy-section"><h2>Եթե դասը դեռ պատրաստ չէ</h2><p>Դասերի էջում կարող եք հետևել պատրաստման ընթացքին։ Անորոշ փոքր դեպքերում Reel-ը շարունակում է աշխատանքը պահպանողական տարբերակով։ Կարևոր խոչընդոտի դեպքում ադմինը ստանում է ծանուցում։</p><a class="btn" href="${S.role==='student'?'#/library':'#/jobs'}">${S.role==='student'?'Բացել գրադարանը':'Բացել դասերը'}</a></section>`);
}

/* ---------- job detail ---------- */
function boardFramingLine(info) {
  if (!info || !Object.prototype.hasOwnProperty.call(info, "board") || typeof info.board !== "string") return "";
  let message;
  if (info.board === "framed") {
    const framing = info.framing || {};
    const number = (value) => Number.isFinite(value) ? String(Number(value.toFixed(2))) : "—";
    message = `Կադր՝ գրատախտակով (մեծացում ${number(framing.zoom)}×, թեձերւթյուն ${number(framing.rotate_deg)}°)`;
  } else {
    const reasons = [
      ["board not found reliably", "գրատախտակը վստահելի չգտնվեց"],
      ["portrait video", "ուղղահայաց վիդեո"],
      ["framing would cut", "գրատախտակի մեծ մաս կկտրվեր"],
      ["board detection failed", "ճանաչումը ձախողվեց"],
      ["framed render failed", "կտրված տարբերակը չստացվեց"]
    ];
    const match = reasons.find(([prefix]) => info.board.startsWith(prefix));
    message = `Կադր՝ ամբողջական — ${match ? match[1] : info.board}`;
  }
  return `<div class="muted" style="font-size:12px;margin-top:4px">${esc(message)}</div>`;
}
function autoDecisionLine(info) {
  if (!info || !Object.prototype.hasOwnProperty.call(info, "auto_note")
      || typeof info.auto_note !== "string" || !info.auto_note.trim()) return "";
  const notes = [
    ["unclear windows", "հատվածների գնահատումն անորոշ էր․ դասը պահպանվել է ամբողջությամբ"],
    ["plan cut", "պլանը չափազանց մեծ հատված էր կտրում․ դասը պահպանվել է ամբողջությամբ"],
    ["planned result too short", "պլանավորված արդյունքը շատ կարճ էր․ դասը պահպանվել է ամբողջությամբ"],
    ["grounded chapters unavailable", "գլուխները վստահելի չէին․ ստեղծվել են հիմնական վերնագիրն ու նկարագրությունը՝ առանց գլուխների"],
    ["LLM metadata unavailable", "ստեղծվել են հիմնական հայերեն վերնագիրն ու նկարագրությունը՝ առանց գլուխների"],
    ["LLM chapter wording unusable", "անվանումների առաջարկը կիրառելի չէր․ պահպանվել են տրանսկրիպտից կազմված գլուխները"]
  ];
  const more = [
    [/^\d+ transcript segments have broken timestamps/, "խոսքի որոշ ժամեր անվստահելի էին․ դասը պահպանվել է ամբողջությամբ"],
    [/^loudness /, "ձայնի մակարդակը շեղվել է թիրախից․ տեսանյութը պահպանվել է"],
    [/^the group's lecturer photos match/, "խմբի դասախոսի նկարները չէին համընկնում․ կիրառվել է ավտոմատ ճանաչումը"],
    [/^lecturer choice file (unreadable|invalid)/, "դասախոսի պահպանված ընտրությունը անվավեր էր․ կիրառվել է ավտոմատ ճանաչումը"],
    [/^the lecturer chosen on the job page/, "ընտրված դասախոսը կրկին չճանաչվեց․ կիրառվել է բոլոր դեմքերի մշուշումը"],
    [/^lecturer unclear/, "դասախոսը վստահ չէր որոշվում․ կիրառվել է բոլոր դեմքերի մշուշումը"],
    [/^only \d+ clear photos/, "դասախոսի հստակ նմուշները քիչ էին․ կիրառվել է բոլոր դեմքերի մշուշումը"],
    [/^the recognised lecturer matches/, "ճանաչված դասախոսի համընկնումները քիչ էին․ կիրառվել է բոլոր դեմքերի մշուշումը"],
    [/^thumbnail skipped/, "շապիկը ստեղծել չհաջողվեց․ հրապարակումը շարունակվել է առանց դրա"],
    [/^thumbnail not set after/, "YouTube-ում շապիկը տեղադրել չհաջողվեց․ տեսանյութը հրապարակված է"],
  ];
  const message = info.auto_note.split(/; (?=(?:the |lecturer |only \d))/).map(part => {
    const known = notes.find(([prefix]) => part.startsWith(prefix));
    const extra = more.find(([pattern]) => pattern.test(part));
    return known ? known[1] : extra ? extra[1] : part;
  }).join(" · ");
  return `<div class="muted" style="font-size:12px;margin-top:4px">🤖 Ավտոմատ որոշում՝ ${esc(message)}</div>`;
}
async function viewJob(id) {
  if (!$("#jd-head")) {
    $("#main").innerHTML = `<a class="back" href="#/jobs">${ic("back")} Բոլոր դասերը</a><div id="jd-head"></div><section id="jd-date" class="card lesson-date-card"></section><div id="jd-alert"></div><div id="jd-progress"></div><div id="jd-lecturer"></div>
      <div class="grid two"><div class="grid"><div class="card" id="jd-video"></div><div class="card" id="jd-tabs"></div></div>
      <div class="card"><h2>${ic("layers")} Մշակման փուլեր <span class="muted" id="jd-total" style="margin-left:auto;font-size:13px;font-weight:500"></span></h2><ul class="steps" id="jd-steps"></ul></div></div>`;
  }
  const d = await api(`/api/jobs/${id}`); S.detail = d;
  renderLessonDate(d);
  const yt = d.publications.youtube;
  set("#jd-head", `<div class="top" style="margin-bottom:16px"><div class="grow"><h1>${esc(d.title || d.file)}</h1>
      <p>${esc(d.group)}${d.subject ? ` · <b>${esc(d.subject)}</b>` : ""} · <span class="mono">${esc(d.file)}</span> · ${rel(d.created)} · ${fmtDur(d.duration_sec)}${d.final_sec != null ? " → " + fmtDur(d.final_sec) : ""}</p></div>
    <div class="row">${d.dry ? `<span class="badge dry" title="Հրապարակումները մոդելավորված են">${ic("alert")} Թեստային մշակում</span>` : ""}${badge(d.status)}
      ${yt && yt.url ? `<a class="btn" href="${esc(yt.url)}" target="_blank" rel="noopener">${ic("yt")} YouTube ${ic("external")}</a>` : ""}
      ${d.publications.slack && d.publications.slack.url ? `<a class="btn" href="${esc(d.publications.slack.url)}" target="_blank" rel="noopener">${ic("hash")} Slack ${ic("external")}</a>` : ""}
      ${S.role === "admin" && d.status === "stopped" ? `<button class="btn primary" data-act="retry" data-id="${d.id}">${ic("refresh")} Կրկնել</button>` : ""}</div></div>`);
  set("#jd-progress", `<div class="lesson-progress"><div class="row"><span class="grow">${d.status === "done" ? "Դասը պատրաստ է" : "Մշակման ընթացք"}</span><span class="mono" data-progress-job="${d.id}" data-progress-value="${pct(d)}">${pct(d)}%</span></div><div class="bar" role="progressbar" aria-label="Դասի մշակման առաջընթաց" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct(d)}"><i style="width:${pct(d)}%"></i></div><p class="muted">${esc(d.current_label || "")} · ${typeof etaText === "function" ? etaText(d) : ""}</p></div>`);
  let alert = "";
  if (d.status === "stopped" && d.error) {
    const m = d.error.match(/'(\w+)' may already be posted/);
    const needSubj = /choose the lesson's subject/.test(d.error) && S.role === "admin" && (d.subject_choices || []).length;
    alert = `<div class="alert err">${ic("alert")}<div class="grow"><b>Մշակումը կանգնեցված է</b>${d.hint ? `<div style="margin:4px 0 6px">${esc(d.hint)}</div>` : ""}<span class="mono faint" style="font-size:12px">${esc(d.error)}</span>
      ${m && S.role === "admin" ? `<div class="row" style="margin-top:10px"><span class="muted">Ստուգեք ${esc(m[1])}-ը.</span>
        <button class="btn" data-act="resolve" data-id="${d.id}" data-target="${esc(m[1])}" data-state="posted">Հրապարակված է</button>
        <button class="btn" data-act="resolve" data-id="${d.id}" data-target="${esc(m[1])}" data-state="not-posted">Հրապարակված չէ</button></div>` : ""}
      ${needSubj ? `<div class="row" style="margin-top:10px;flex-wrap:wrap"><span class="muted">Առարկա.</span><select class="input" id="jd-subject" style="max-width:240px">${d.subject_choices.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</select>
        <button class="btn primary" data-act="subject" data-id="${d.id}">Պահպանել և կրկնել</button></div>` : ""}</div></div>`;
  }
  if (d.upload_media && d.upload_media.low_resolution) alert += `<div class="alert warn">${ic("alert")}<div><b>Վերբեռնված տեսանյութը 1080p-ից ցածր է</b>Պատկերի մանրամասները կարող են պակասել։ Հաջորդ անգամ ընտրեք բնօրինակը Files-ից կամ Photos-ի Options-ով։</div></div>`;
  if (d.upload_media && d.upload_media.resolution_unknown) alert += `<div class="alert warn">${ic("alert")}<div>Որոշ մասերի լուծաչափը դեռ չհաստատվեց․ դրանք կստուգվեն մշակման ընթացձերւմ։</div></div>`;
  set("#jd-alert", alert);
  const tot = d.stages.reduce((a, s) => a + (s.duration_sec || 0), 0);
  set("#jd-total", tot ? `ընդհանուր՝ ${fmtDur(tot)}` : "");
  set("#jd-steps", d.stages.map((s) => {
    const icn = { done: "check", running: "refresh", failed: "x" }[s.status] || "dot";
    return `<li class="step ${s.status}"${s.status === "running" ? ' aria-current="step"' : ""}><span class="ic">${ic(icn)}</span><div><div class="n">${esc(s.label)}${s.attempts > 1 ? ` <span class="badge soft">${s.attempts} փորձ</span>` : ""}</div>
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
  if(S.role!=="admin") {set("#jd-date",`<b>Դասի ամսաթիվ՝ ${esc(formatLessonDate(d.lesson_date))}</b>${d.lesson_date_inferred ? '<p class="muted">Հին դասի ամսաթիվը նշված չէ․ ցուցադրված է գրանցման օրը։</p>' : ''}`);return;}
  if(target.dataset.job===String(d.id))return;
  target.dataset.job=String(d.id);
  target.innerHTML=`<form id="lesson-date-form"><label class="fld"><span>Դասի ամսաթիվ</span><input class="input" id="lesson-date" type="date" max="${lessonToday()}" value="${esc(d.lesson_date||lessonToday())}" required></label><button class="btn" type="submit">Պահպանել ամսաթիվը</button></form><p class="muted" id="lesson-date-message" role="status">${d.lesson_date_inferred ? 'Հին դասի ամսաթիվը նշված չէ․ ցուցադրված է գրանցման օրը։ Կարող եք ճշտել այն։' : 'Ամսաթիվը ցուցադրվում է դասի էջում, գրադարանում և վիճակագրությունում։'}</p>`;
  $("#lesson-date-form").onsubmit=async event=>{
    event.preventDefault();const button=event.target.querySelector('button'),message=$("#lesson-date-message");button.disabled=true;
    try {const r=await api(`/api/jobs/${d.id}/date`,{method:"POST",body:JSON.stringify({lesson_date:$("#lesson-date").value})});d.lesson_date=r.lesson_date;d.lesson_date_inferred=false;message.textContent="Դասի ամսաթիվը պահպանված է։";}
    catch(error){message.textContent=error.message;}finally{button.disabled=false;}
  };
}

function renderVideo(d) {
  const ch = (d.metadata && d.metadata.chapters) || [];
  const q = S.quality === "full" && d.has_full ? "full" : "preview";
  const qsw = d.has_video && d.has_preview && d.has_full ? `<div class="qswitch" role="group" aria-label="Որակ">
      <button data-q="preview" class="${q === "preview" ? "on" : ""}" title="Թեթև տարբերակ՝ սահուն է ինտերնետով">Վեբ</button>
      <button data-q="full" class="${q === "full" ? "on" : ""}" title="Բնօրինակ որակ (մեծ ֆայլ)">HD</button></div>` : "";
  const why = !d.privacy_passed && S.role !== "admin" ? "Տեսանյութը հասանելի կլինի գաղտնիության ստուգումն անցնելուց հետո" : "Տեսանյութը կհայտնվի դեմքերի մշուշումից հետո";
  $("#jd-video").innerHTML = `<div class="row" style="margin-bottom:14px"><h2 class="grow" style="margin:0">${ic("play")} Վերջնական տեսանյութ</h2>${qsw}</div>
    ${d.has_video ? `<video controls preload="metadata" playsinline src="/api/jobs/${d.id}/video${q === "full" ? "?q=full" : ""}"${d.has_thumbnail ? ` poster="/api/jobs/${d.id}/thumbnail"` : ""}></video>`
      : `<div class="video-empty">${d.has_thumbnail ? `<img src="/api/jobs/${d.id}/thumbnail" alt="Դասի շապիկ">` : ""}<div><div class="ic acc" style="margin:0 auto 10px">${ic(d.privacy_passed ? "clock" : "shield")}</div>${why}</div></div>`}
    ${ch.length ? `<div class="muted" style="margin-top:14px;font-weight:600">Գլուխներ <span class="faint">· սեղմեք՝ անցնելու համար</span></div>
      <div class="chapters">${ch.map((c) => `<button class="chap" data-seek="${c.final_t}"><span class="mono">${clock(c.final_t)}</span><span>${esc(c.title)}</span></button>`).join("")}</div>` : ""}`;
  const v = $("#jd-video video");
  if (v && ch.length) v.addEventListener("timeupdate", () => {
    let k = -1; ch.forEach((c, i) => { if (v.currentTime + 0.5 >= c.final_t) k = i; });
    document.querySelectorAll("#jd-video .chap").forEach((b, i) => b.classList.toggle("on", i === k));
  });
}

function renderTabs() {
  const d = S.detail; if (!d || !$("#jd-tabs")) return;
  const tabs = [["meta", "Նկարագրություն"], ...(S.role !== "uploader" ? [["cuts", "Ինչ է կտրվել"]] : []), ["privacy", "Գաղտնիություն"], ["pub", "Հրապարակում"]];
  if (S.role === "uploader" && S.tab === "cuts") S.tab = "meta";
  let body = "";
  if (S.tab === "meta") {
    const m = d.metadata, tr = (d.stages.find((s) => s.name === "transcribe") || {}).info || {}, rd = (d.stages.find((s) => s.name === "render") || {}).info || {};
    body = m ? `<div class="row" style="align-items:flex-start;gap:16px;flex-wrap:wrap">
        ${d.has_thumbnail ? `<img src="/api/jobs/${d.id}/thumbnail" alt="Շապիկ" style="width:220px;max-width:100%;border-radius:10px">` : ""}
        <div class="grow" style="min-width:220px"><div class="row"><div class="muted grow" style="font-weight:600">Վերնագիր</div><button class="btn sm" data-copy="title">${ic("copy")} Պատճենել</button></div><div style="font-size:16px;font-weight:650;margin:2px 0 12px">${esc(m.title)}</div>
        <div class="kv">${tr.speed_x ? `<div><b>${tr.speed_x}x</b><small>տրանսկրիպցիայի արագություն</small></div>` : ""}
          ${rd.lufs != null ? `<div><b>${rd.lufs.toFixed(1)} LUFS</b><small>ձայնի մակարդակ</small></div>` : ""}
          <div><b>${m.chapters.length}</b><small>գլուխ</small></div></div></div></div>
      <div class="row" style="margin:16px 0 6px"><div class="muted grow" style="font-weight:600">YouTube-ի նկարագրություն</div><button class="btn sm" data-copy="desc">${ic("copy")} Պատճենել</button></div><pre class="desc">${esc(m.youtube_description || m.description)}</pre>`
      : `<div class="empty">Վերնագիրն ու նկարագրությունը կստեղծվեն «Վերնագիր և գլուխներ» փուլում</div>`;
  } else if (S.tab === "cuts") {
    body = '<div id="cut-insight" aria-live="polite">Բեռնում ենք կտրման ամփոփումը…</div>';
    if (typeof loadCuts === "function") setTimeout(() => loadCuts(d.id), 0);
  } else if (S.tab === "privacy") {
    const p = d.privacy, b = (d.stages.find((s) => s.name === "blur") || {}).info || {};
    body = p || b.blurred_boxes != null ? `<div class="kv">
        ${b.blurred_boxes != null ? `<div><b>${b.blurred_boxes}</b><small>մշուշված դեմք (կադրերում)</small></div>` : ""}
        ${b.lecturer_boxes != null ? `<div><b>${b.lecturer_boxes}</b><small>դասախոսի դեմք (չմշուշված)</small></div>` : ""}
        ${p ? `<div><b>${p.frames}</b><small>ստուգված կադր</small></div><div><b>${p.other_faces}</b><small>չմշուշված օտար դեմք</small></div>` : ""}
        ${p && p.warnings != null ? `<div><b>${p.warnings}</b><small>մեկուսի նախազգուշացում (1–2 կադր)</small></div>` : ""}</div>
      <p class="muted" style="margin:14px 0 0">Ուսանողների դեմքերը մշուշվում են, իսկ դասախոսը ճանաչվում է ավտոմատ (ով ամենաշատն է երևում կադրում) կամ խմբի նմուշ-նկարներով։ Վերջնական ստուգումը կատարվում է արդեն մշուշված տեսանյութի վրա. եթե օտար դեմքը երևում է անընդմեջ (3+ կադր), դասը չի հրապարակվի։ Մեկուսի 1–2 կադրանոց դեպքերը (կողքից դեմք, ձեռք, հազվադեպ՝ ուսանողի դեմք մի պահ) դասը չեն կանգնեցնում. դրանք ցուցադրվում են ստորև՝ ստուգման համար, և գալիս է ծանուցում։</p>
      ${d.faces.length ? `<div class="muted" style="font-weight:600;margin-top:14px">Ստուգման համար նշված դեմքեր <span class="faint">· սեղմեք՝ տեսանյութում տեսնելու համար</span></div>
        <div class="faces">${d.faces.map((f) => `<figure class="${f.kind}"${f.t != null ? ` data-seek="${f.t}"` : ""} title="${f.kind === "hit" ? "Կանգնեցրած դեպք" : "Մեկուսի նախազգուշացում"}"><img loading="lazy" src="/api/jobs/${d.id}/faces/${encodeURIComponent(f.name)}" alt=""><figcaption class="mono">${f.t != null ? clock(f.t) : ""}</figcaption></figure>`).join("")}</div>`
        : d.faces_hidden && p && (p.other_faces || p.warnings) ? `<p class="faint" style="margin:12px 0 0">Ստուգման դեմքերի նկարները տեսանելի են միայն ադմինին (գաղտնիություն)։</p>` : ""}`
      : `<div class="empty">Գաղտնիության ստուգումը դեռ չի կատարվել</div>`;
  } else {
    const SKIPPED = { classroom: ["classroom_post", "Անջատված"], playlist: ["playlist", "Դասացանկ դեռ չկա"] };
    const off = (k) => SKIPPED[k] && (d.stages || []).some((s) => s.name === SKIPPED[k][0] && s.info && s.info.skipped) ? SKIPPED[k][1] : "";
    body = `<div class="pubs">${Object.entries(PUB).map(([k, [i, l]]) => { const p = d.publications[k];
      if (off(k)) return `<div class="pub">${ic(i)}<span class="grow">${l}</span><span class="badge">${off(k)}</span></div>`;
      return `<div class="pub">${ic(i)}<span class="grow">${l}</span>${p ? `${p.dry ? `<span class="badge dry" title="Թեստային ռեժիմ. իրական հրապարակում չի կատարվել">${ic("alert")} Փորձնական</span>` : `<span class="badge done">${ic("check")} Հրապարակված</span>`}${p.url ? `<a class="icon-btn" href="${esc(p.url)}" target="_blank" rel="noopener" title="Բացել" aria-label="Բացել ${l}">${ic("external")}</a>` : ""}` : `<span class="badge soft">Սպասում է</span>`}</div>`; }).join("")}</div>
      ${d.dry ? `<div class="alert warn" style="margin-top:14px">${ic("alert")}<div>Այս դասը մշակվել է <b>թեստային ռեժիմով</b> ․ հրապարակումները մոդելավորված են, իրականում ոչինչ չի հրապարակվել։</div></div>` : ""}
      <p class="muted" style="margin:14px 0 0">Կրկնակի փորձերը կրկնակի հրապարակում չեն ստեղծում. յուրաքանչյուր հրապարակում գրանցվում է մեկ անգամ։</p>`;
  }
  $("#jd-tabs").innerHTML = `<div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button class="tab ${S.tab === k ? "on" : ""}" data-tab="${k}" role="tab" aria-selected="${S.tab === k}">${l}</button>`).join("")}</div>${body}`;
}

/* ---------- login ---------- */
function renderLogin(msg = "") {
  clearInterval(S.poll);
  app.innerHTML = `<div class="login"><div class="login-wrap"><section class="login-intro" aria-labelledby="login-title"><button class="icon-btn login-theme" id="theme" type="button" aria-label="${isDark() ? "Միացնել բաց թեման" : "Միացնել մուգ թեման"}" aria-pressed="${isDark()}">${ic(isDark() ? "sun" : "moon")}</button><div class="login-brand-pair">${reelBrand()}</div><div class="eyebrow">REEL · ԴԱՍԵՐԻ ՎԱՀԱՆԱԿ</div><h1 id="login-title">Ամեն դաս՝<br>պատրաստ<br><em>կիսվելու։</em></h1><p>Ձեր գիտելիքը հասնում է ուսանողներին։ Մենք հոգում ենք տեսագրության մնացած ճանապարհը։</p><div class="login-flow" aria-hidden="true"><div class="row">${ic("play")} Reel</div><b>Դասից՝ ուսանողին</b><div class="row">Տեսագրություն <span class="grow"></span>Մշակում <span class="grow"></span>Հրապարակում</div></div></section>
    <div class="card"><div class="eyebrow">Ձեր աշխատանքային տարածքը</div><h2>Բարի վերադարձ</h2><p>Մուտք գործեք՝ ձեր դասերը շարունակելու համար։</p>
    <form id="login-form"><label class="fld"><span>Մուտքանուն կամ էլ․ հասցե</span><input class="input" id="login-identity" type="text" autocomplete="username" maxlength="254" placeholder="Մուտքանուն կամ էլ․ հասցե"></label><label class="fld"><span>Գաղտնաբառ</span><div class="password-field"><input class="input" type="password" id="pw" placeholder="Ձեր գաղտնաբառը" autocomplete="current-password" aria-describedby="login-err" required><button type="button" id="show-pw" aria-label="Ցույց տալ գաղտնաբառը" aria-pressed="false">Ցույց տալ</button></div></label>
      <div class="err-text" id="login-err" role="alert">${esc(msg)}</div><button class="btn primary" type="submit">Մուտք գործել ${ic("external")}</button></form><p><a class="account-text-link" href="#/forgot">Մոռացե՞լ եք գաղտնաբառը</a></p><p class="muted">Գրանցումը միայն հրավերով է։ Ընդհանուր գաղտնաբառով մուտքի համար մուտքանունը թողեք դատարկ։</p>
    <p class="login-foot login-policy">${ic("shield")} Հասանելիությունը որոշվում է ձեր դերով։ Ուսանողների տվյալները պաշտպանված են։</p></div></div></div>`;
  $("#show-pw").onclick=()=>{const input=$("#pw"),shown=input.type==="password";input.type=shown?"text":"password";$("#show-pw").textContent=shown?"Թաքցնել":"Ցույց տալ";$("#show-pw").setAttribute("aria-pressed",String(shown));};
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault(); const btn = e.target.querySelector('button[type="submit"]'); btn.disabled = true;
    try { const r = await api("/api/login", { method: "POST", body: JSON.stringify({ login: $("#login-identity").value, password: $("#pw").value }), login: true }); if(r.second_factor){viewSecondFactor();return;} S.role = r.role; S.user = r.user || null; if(r.bootstrap)location.hash="#/setup"; route(); }
    catch (err) { $("#login-err").textContent = err.message === "wrong password" ? "Սխալ գաղտնաբառ" : err.message; btn.disabled = false; }
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
    try { await navigator.clipboard.writeText(txt); toast("Պատճենված է"); } catch (_) { toast("Պատճենել չհաջողվեց"); } return; }
  if (t.dataset.cutbar) { const g = S.detail && S.detail.segments, v = $("#jd-video video"); if (!g || !v) return;
    const r = t.getBoundingClientRect(), x = ((e.clientX - r.left) / r.width) * g.original_sec; let fin = 0;
    for (const [a, b] of g.keep || []) { if (x < a) break; if (x <= b) { fin += x - a; break; } fin += b - a; }
    v.currentTime = fin; v.play().catch(() => {}); return; }
  if (t.dataset.act === "subject") {
    const v = ($("#jd-subject") || {}).value;
    if (!v || !confirm(`Նշե՞լ «${v}» առարկան և վերադարձնել դասը հերթ։`)) return;
    t.disabled = true;
    try { await api(`/api/jobs/${t.dataset.id}/subject`, { method: "POST", body: JSON.stringify({ subject: v }) });
      await api(`/api/jobs/${t.dataset.id}/retry`, { method: "POST" }); toast("Առարկան նշված է, դասը հերթում է"); S.last = {}; }
    catch (err) { toast("Սխալ. " + err.message); t.disabled = false; }
    return;
  }
  if (t.dataset.act) {
    const msg = t.dataset.act === "retry" ? "Վերադարձնե՞լ դասը հերթ։ Ավարտված փուլերը չեն կրկնվի։"
      : t.dataset.state === "posted" ? `Հաստատո՞ւմ եք, որ ${t.dataset.target}-ում արդեն հրապարակված է։ Reel-ը այն նորից չի հրապարակի։`
      : `Հաստատո՞ւմ եք, որ ${t.dataset.target}-ում հրապարակված ՉԷ։ Reel-ը կհրապարակի այն։`;
    if (!confirm(msg)) return;
    t.disabled = true;
    try {
      if (t.dataset.act === "retry") { await api(`/api/jobs/${t.dataset.id}/retry`, { method: "POST" }); toast("Դասը վերադարձվեց հերթ. ավարտված փուլերը չեն կրկնվի"); }
      if (t.dataset.act === "resolve") { await api(`/api/jobs/${t.dataset.id}/resolve`, { method: "POST", body: JSON.stringify({ target: t.dataset.target, state: t.dataset.state }) });
        await api(`/api/jobs/${t.dataset.id}/retry`, { method: "POST" }); toast("Գրանցված է. դասը վերադարձվեց հերթ"); }
      S.last = {};
    } catch (err) { toast("Սխալ. " + err.message); t.disabled = false; }
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
