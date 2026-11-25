"use strict";
/* Group cards and their editing panels live here; Settings is general-only. */
function groupMask(value) {
  const text = String(value || "");
  return text.includes("•••") || text.length <= 6
    ? text
    : text.slice(0, 3) + "•••" + text.slice(-3);
}
function groupDate(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value || "")
    ? value.slice(8, 10) + "." + value.slice(5, 7) + "." + value.slice(0, 4)
    : i18nText("ui.app.not_set");
}
function groupRange(start, end) {
  return start && end
    ? groupDate(start) + " – " + groupDate(end)
    : i18nText("ui.groups.no_dates_set");
}
function groupPerson(person, subjects = []) {
  const initials = String(person.name || "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => Array.from(part)[0])
    .join("");
  return `<div class="gp-person"><span class="gp-avatar">${person.avatar ? `<img loading="lazy" src="${esc(person.avatar)}" alt="">` : esc(initials || "?")}</span><span><b>${esc(person.name)}</b>${subjects.length ? `<small>${esc(subjects.join(", "))}</small>` : ""}</span></div>`;
}
function groupPeople(team) {
  const lecturers = new Map();
  (team?.lecturers || []).forEach((person) => {
    if (!lecturers.has(person.id)) lecturers.set(person.id, { ...person, subjects: [] });
    if (person.subject) lecturers.get(person.id).subjects.push(person.subject);
  });
  return `<h3>${i18nText("ui.accounts.lecturers")}</h3><div class="gp-people">${[...lecturers.values()].map((person) => groupPerson(person, person.subjects)).join("") || `<p class="muted">${i18nText("ui.groups.no_lecturer_assigned_yet")}</p>`}</div>
    <h3>${i18nText("ui.groups.student_assistants")}</h3><p class="gp-help">${i18nText("ui.groups.assistants_can_view_watch_statistics_for_their")}</p><div class="gp-people">${(team?.tas || []).map((person) => groupPerson(person)).join("") || `<p class="muted">${i18nText("ui.groups.no_student_assistant_assigned_yet")}</p>`}</div>`;
}
function groupsStyles() {
  if ($("#groups-css")) return;
  const style = document.createElement("style");
  style.id = "groups-css";
  style.textContent = `
    #groups-page{display:grid;gap:20px}#groups-page .top{margin:0;flex-wrap:wrap}#groups-page [hidden]{display:none!important}#groups-page .gp-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}#groups-page .gp-toolbar .chips{margin:0}#groups-page .gp-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:20px;align-items:stretch}#groups-page .gp-card{display:flex;flex-direction:column;gap:8px;min-width:0;position:relative;margin:0;padding:16px}#groups-page .gp-card-head{display:flex;gap:12px;align-items:flex-start}#groups-page .gp-card h2{font-size:20px;line-height:1.4;margin:0;overflow-wrap:anywhere}#groups-page .gp-card-title{flex:1;min-width:0}#groups-page .gp-counts{display:flex;flex-wrap:wrap;gap:5px 10px;font-size:12px;color:var(--muted);margin-top:0}#groups-page .gp-thumbnail{width:80px;height:45px;flex:none;object-fit:cover;border-radius:8px;border:1px solid var(--border);background:var(--surface-2)}#groups-page .gp-thumbnail-empty{display:grid;place-items:center;font-size:10px;color:var(--muted)}#groups-page .gp-status{display:flex;gap:6px;flex-wrap:wrap}#groups-page .gp-state{font-size:12px;padding:4px 8px;border-radius:var(--radius-pill);background:var(--ok-soft);color:var(--ok)}#groups-page .gp-state.completed{background:var(--surface-2);color:var(--muted)}#groups-page .gp-state.soon{background:var(--warn-soft);color:var(--warn)}#groups-page .gp-values{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 14px;margin:0}#groups-page .gp-values>.gp-value:first-child{grid-column:1/-1}#groups-page .gp-value{display:flex;align-items:baseline;justify-content:space-between;gap:6px;font-size:12px}#groups-page .gp-value dt{color:var(--muted);flex:none}#groups-page .gp-value dd{margin:0;text-align:right;min-width:0;overflow-wrap:anywhere}#groups-page .gp-value dd.gp-disabled{color:var(--faint)}#groups-page .gp-subjects{display:grid;gap:0;flex:1;align-content:start}#groups-page .gp-subject{display:grid;gap:0;border-top:1px solid var(--border);padding-top:0;font-size:13px}#groups-page .gp-subject-head{display:flex;justify-content:space-between;align-items:center;gap:8px;min-height:44px}#groups-page .gp-subject-name{font-weight:650;overflow-wrap:anywhere}#groups-page .gp-playlist{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12px;color:var(--muted)}#groups-page .gp-playlist.missing{color:var(--warn)}#groups-page .gp-playlist .btn{min-height:44px;min-width:44px;font-size:12px;padding:6px 9px}#groups-page .gp-period{font-size:12px;color:var(--muted)}#groups-page .gp-card-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:auto}#groups-page .gp-menu{position:relative;flex:none}#groups-page .gp-menu summary{list-style:none;display:grid;place-items:center;width:44px;height:44px;border:1px solid var(--border);border-radius:12px;cursor:pointer;font-size:24px;line-height:1}#groups-page .gp-menu summary::-webkit-details-marker{display:none}#groups-page .gp-menu[open]{z-index:5}#groups-page .gp-menu-items{position:absolute;right:0;top:48px;z-index:6;width:240px;padding:8px;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-raised);display:grid;gap:3px}#groups-page .gp-menu-items button{justify-content:flex-start;text-align:left;width:100%;font-weight:500;background:transparent;box-shadow:none}#groups-page .gp-menu-items button:hover{background:var(--surface-2)}
    #groups-page .gp-panel{scroll-margin-top:96px;margin:0;min-width:0}#groups-page .gp-panel-head{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;margin-bottom:16px}#groups-page .gp-panel-head h2{margin:0;overflow-wrap:anywhere}#groups-page .gp-form{display:grid;gap:18px}#groups-page .gp-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}#groups-page .fld{margin:0}#groups-page fieldset{min-width:0}#groups-page fieldset.gp-fields-root{border:0;margin:0;padding:0;display:grid;gap:18px}#groups-page .gp-help{font-size:12.5px;line-height:1.65;color:var(--muted);margin:5px 0}#groups-page .gp-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}#groups-page .gp-error{white-space:pre-wrap;overflow-wrap:anywhere}#groups-page .gp-subject-edit{display:grid;gap:12px;padding:16px;border:1px solid var(--border);border-radius:12px;background:var(--surface-2)}#groups-page .gp-subject-edit-head{display:flex;gap:10px;align-items:flex-end}#groups-page .gp-subject-edit-head .fld{flex:1}#groups-page .gp-check{display:flex;gap:10px;align-items:flex-start;min-height:44px}#groups-page .gp-check input{width:20px;height:20px;flex:none;accent-color:var(--accent);margin-top:3px}#groups-page .gp-team-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.35fr);gap:24px}#groups-page .gp-team-section h3{font-size:15px;margin:20px 0 10px}#groups-page .gp-team-section h3:first-child{margin-top:0}#groups-page .gp-people{display:grid;gap:12px}#groups-page .gp-person{display:flex;gap:10px;align-items:center;font-size:14px;min-width:0}#groups-page .gp-person>span:last-child{min-width:0;overflow-wrap:anywhere}#groups-page .gp-person b{display:block;font-weight:600}#groups-page .gp-person small{display:block;font-size:12px;color:var(--muted);margin-top:3px}#groups-page .gp-avatar{width:44px;height:44px;flex:none;border-radius:50%;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent);overflow:hidden;font-size:13px;font-weight:700}#groups-page .gp-avatar img{width:100%;height:100%;object-fit:cover}#groups-page .gp-team-subject{display:grid;gap:12px;padding:14px;border:1px solid var(--border);border-radius:12px}#groups-page .gp-team-subject h4{margin:0;font-size:14px}#groups-page .gp-team-form-subjects{display:grid;gap:12px}#groups-page .gp-ta-options{display:grid;gap:8px}#groups-page .gp-style-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}#groups-page .gp-style{padding:6px;min-width:0;border:2px solid var(--border);border-radius:12px;background:var(--surface);cursor:pointer;color:var(--text)}#groups-page .gp-style.on{border-color:var(--accent);background:var(--accent-soft)}#groups-page .gp-style img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:8px}#groups-page .gp-style span{display:block;padding:8px;font-size:13px}
    @media(max-width:860px){#groups-page .gp-team-grid{grid-template-columns:1fr}#groups-page .gp-style-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:540px){#groups-page .gp-fields{grid-template-columns:1fr}#groups-page .gp-card{padding:16px}#groups-page .gp-thumbnail{width:72px;height:41px}#groups-page .gp-actions>.btn{flex:1;justify-content:center}#groups-page .gp-panel-head .btn{flex:none}#groups-page .gp-panel-head h2{font-size:19px}}
  `;
  document.head.append(style);
}

async function renderLegacyGroupsPage(initialAction = null) {
  const existing = $("#groups-page");
  if (existing) return existing.refreshGroups?.();
  groupsStyles();
  const admin = S.role === "admin";
  const initialStatus = new URLSearchParams(location.hash.split("?")[1] || "").get("status");
  const status = ["active", "completed", "all"].includes(initialStatus) ? initialStatus : "active";
  $("#main").innerHTML =
    `<div id="groups-page"><div class="top"><div><h1>${i18nText("ui.accounts.groups")}</h1><p>${i18nText("ui.groups.courses_subjects_and_teams_in_one_place")}</p></div>${admin ? `<div class="gp-actions"><a class="btn" href="#/audit">${i18nText("ui.audit.activity_log")}</a><button class="btn primary" type="button" data-group-action="new">${i18nText("ui.groups.new_group")}</button></div>` : ""}</div>
    <div class="gp-toolbar"><div class="chips" id="groups-status-filter" aria-label="${i18nText("ui.groups.group_status")}"></div><span class="grow"></span><button class="btn sm" type="button" data-group-action="refresh">${ic("refresh")} ${i18nText("ui.groups.refresh")}</button></div>
    <div id="groups-message" role="status" hidden></div><div id="groups-cards" class="gp-cards"><div class="card muted" role="status">${i18nText("ui.groups.loading_groups")}</div></div>
    <section id="group-editor" class="card gp-panel" hidden></section><section id="group-team" class="card gp-panel" hidden></section><section id="group-style" class="card gp-panel" hidden></section></div>`;
  const view = $("#groups-page"),
    find = (selector) => $(selector, view);
  const alive = () => $("#groups-page") === view && (S.role === "admin") === admin;
  const state = {
    status,
    groups: [],
    config: null,
    refs: {},
    playlists: null,
    styles: null,
    request: 0,
    panel: 0,
    panelKind: null,
    panelName: null,
    dirty: false,
    busy: false,
    loadingCards: false,
    lastFetch: 0,
    serial: 0,
  };
  if (initialAction) {
    find("#groups-cards").hidden = true;
    find(".gp-toolbar").hidden = true;
  }
  const errorText = (error) =>
    error.net
      ? i18nText("ui.accounts.could_not_connect_to_the_server_try")
      : String(error.message || error);
  function message(target, text, ok = false) {
    if (!target) return;
    target.textContent = text || "";
    target.hidden = !text;
    target.className = `alert ${ok ? "ok" : "err"} gp-error`;
  }
  function controls() {
    view.querySelectorAll("[data-group-action]").forEach((button) => {
      button.disabled = state.busy;
    });
    view.querySelectorAll(".gp-fields-root").forEach((fieldset) => {
      fieldset.disabled = state.busy;
    });
    view.querySelectorAll(".gp-style").forEach((button) => {
      button.disabled = state.busy;
    });
    const subjects = [...view.querySelectorAll(".gp-subject-edit")];
    subjects.forEach((row) => {
      $('[data-group-action="remove-subject"]', row).disabled = state.busy || subjects.length <= 1;
    });
    const add = find("#group-add-subject");
    if (add) add.disabled = state.busy || subjects.length >= 5;
  }
  function cards() {
    find("#groups-status-filter").innerHTML = [
      ["active", i18nText("ui.courses.active")],
      ["completed", i18nText("ui.courses.completed")],
      ["all", i18nText("ui.accounts.all")],
    ]
      .map(
        ([value, label]) =>
          `<button type="button" class="chip${state.status === value ? " on" : ""}" data-group-action="filter" data-status="${value}" aria-pressed="${state.status === value}">${label}</button>`,
      )
      .join("");
    find("#groups-cards").innerHTML = state.groups.length
      ? state.groups
          .map((group) => {
            const count = group.counts || {},
              done = Boolean(group.completed),
              subjects = group.subjects || [];
            const days = Number(group.days_left);
            const menu = admin
              ? `<details class="gp-menu"><summary aria-label="${esc(group.name)} ${i18nText("ui.groups.group_actions")}">⋯</summary><div class="gp-menu-items"><button class="btn" type="button" data-group-action="edit" data-name="${esc(group.name)}">${i18nText("ui.groups.edit")}</button><button class="btn" type="button" data-group-action="style" data-name="${esc(group.name)}">${i18nText("ui.groups.thumbnail_style")}</button><button class="btn" type="button" data-group-action="dates" data-name="${esc(group.name)}">${i18nText("ui.groups.dates_and_team")}</button><button class="btn" type="button" data-group-action="complete" data-name="${esc(group.name)}" data-completed="${done ? "false" : "true"}">${done ? i18nText("ui.groups.reactivate") : i18nText("ui.groups.mark_completed")}</button><button class="btn danger" type="button" data-group-action="delete" data-name="${esc(group.name)}">${i18nText("ui.groups.delete_group")}</button></div></details>`
              : "";
            return `<article class="card gp-card"><div class="gp-card-head"><div class="gp-card-title"><h2>${esc(group.name)}</h2></div>${group.thumbnail_url ? `<img class="gp-thumbnail" loading="lazy" src="${esc(group.thumbnail_url)}" alt="${esc(group.name)} ${i18nText("ui.groups.group_thumbnail_style")}">` : `<div class="gp-thumbnail gp-thumbnail-empty">${i18nText("stage.thumbnail")}</div>`}${menu}</div>
        <div class="gp-counts"><span>${Number(group.subject_count ?? count.classes ?? subjects.length) || 0} ${i18nText("ui.courses.subjects_3")}</span><span>${Number(group.student_count ?? count.students) || 0} ${i18nText("ui.courses.students_3")}</span><span>${Number(group.lesson_count ?? count.lessons) || 0} ${i18nText("ui.app.lessons_3")}</span></div>
        <div class="gp-status"><span class="gp-state${done ? " completed" : ""}">${done ? i18nText("ui.courses.completed") : i18nText("ui.courses.active")}</span>${group.end_soon ? `<span class="gp-state soon">${days === 0 ? i18nText("ui.groups.ends_today") : `${i18nText("ui.groups.ends_in")} ${Math.max(0, days)} ${i18nText("ui.groups.days")}`}</span>` : ""}${group.manual_active ? `<span class="pill">${i18nText("ui.groups.reactivated")}</span>` : ""}</div>
        <dl class="gp-values"><div class="gp-value"><dt>${i18nText("ui.groups.course")}</dt><dd${group.course_start && group.course_end ? "" : ' class="gp-disabled"'}>${esc(groupRange(group.course_start, group.course_end))}</dd></div><div class="gp-value"><dt>Slack</dt><dd class="${group.slack ? "mono" : "gp-disabled"}">${group.slack ? esc(groupMask(group.slack)) : i18nText("ui.accounts.disabled")}</dd></div><div class="gp-value"><dt>Classroom</dt><dd class="${group.classroom ? "mono" : "gp-disabled"}">${group.classroom ? esc(groupMask(group.classroom)) : i18nText("ui.accounts.disabled")}</dd></div></dl>
        <div class="gp-subjects">${subjects.map((subject) => `<div class="gp-subject"><div class="gp-subject-head"><span class="gp-subject-name">${esc(subject.name)}</span><span class="gp-playlist${subject.playlist ? "" : " missing"}">${subject.playlist ? `Playlist · <span class="mono">${esc(groupMask(subject.playlist))}</span>` : `${i18nText("ui.groups.no_playlist")}${admin ? ` <button class="btn sm" type="button" data-group-action="playlist" data-name="${esc(group.name)}" data-subject="${esc(subject.name)}">${i18nText("ui.groups.add")}</button>` : ""}`}</span></div></div>`).join("")}</div>
        <div class="gp-card-footer"><button class="btn" type="button" data-group-action="team" data-name="${esc(group.name)}">${ic("users")} ${i18nText("ui.courses.team")}</button>${admin && done ? `<button class="btn sm" type="button" data-group-action="dates" data-name="${esc(group.name)}">${i18nText("ui.groups.extend_dates")}</button>` : ""}</div></article>`;
          })
          .join("")
      : `<div class="card empty">${i18nText("ui.groups.no_groups_with_this_status_yet")}</div>`;
    controls();
  }
  async function loadCards() {
    const request = ++state.request;
    state.loadingCards = true;
    state.lastFetch = Date.now();
    find("#groups-cards").setAttribute("aria-busy", "true");
    message(find("#groups-message"), "");
    try {
      const data = await api("/api/group-cards?status=" + state.status);
      if (!alive() || request !== state.request) return;
      if (data.error) throw Error(data.error);
      state.groups = data.groups || [];
      state.lastFetch = Date.now();
      cards();
    } catch (error) {
      if (alive() && request === state.request && error.message !== "auth")
        message(find("#groups-message"), errorText(error));
    } finally {
      if (alive() && request === state.request) {
        state.loadingCards = false;
        find("#groups-cards").setAttribute("aria-busy", "false");
      }
    }
  }
  view.refreshGroups = () => {
    if (
      Date.now() - state.lastFetch > 30000 &&
      !state.panelKind &&
      !state.busy &&
      !state.loadingCards
    )
      return loadCards();
  };
  function panel(kind, name) {
    if (state.busy) return null;
    if (state.dirty && !confirm(i18nText("ui.groups.unsaved_changes_will_be_lost_continue")))
      return null;
    state.dirty = false;
    state.panelKind = kind;
    state.panelName = name;
    const revision = ++state.panel;
    ["editor", "team", "style"].forEach((key) => {
      find("#group-" + key).hidden = true;
    });
    const box = find("#group-" + kind);
    box.innerHTML = `<p class="muted" role="status">${i18nText("ui.accounts.loading")}</p>`;
    box.hidden = false;
    box.scrollIntoView({ block: "start" });
    return { revision, box, valid: () => alive() && state.panel === revision };
  }
  function closePanel() {
    if (state.busy) return;
    if (state.dirty && !confirm(i18nText("ui.groups.unsaved_changes_will_be_lost_close_the")))
      return;
    state.panel++;
    state.panelKind = null;
    state.panelName = null;
    state.dirty = false;
    if (initialAction)
      location.hash = initialAction.name
        ? "#/groups/" + encodeURIComponent(initialAction.name)
        : "#/groups";
    ["editor", "team", "style"].forEach((key) => {
      find("#group-" + key).hidden = true;
    });
  }
  function reveal(slot) {
    const scroll = () => {
      if (slot.valid()) slot.box.scrollIntoView({ block: "start", behavior: "auto" });
    };
    scroll();
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(scroll);
  }
  const panelHead = (title) =>
    `<div class="gp-panel-head"><h2>${esc(title)}</h2><button class="btn sm" type="button" data-group-action="close">${i18nText("ui.accounts.close")}</button></div>`;
  async function openEditor(name = null, focusSubject = null) {
    const slot = panel("editor", name);
    if (!slot) return;
    try {
      const config = await api("/api/admin/config");
      if (!slot.valid()) return;
      state.config = config.config;
      state.refs = config.refs || {};
      const courseData = await api("/api/courses");
      if (!slot.valid()) return;
      const linked =
        courseData.courses.find((c) => c.groups.some((g) => g.name === name))?.id || "";
      const group =
        name === null
          ? {
              subjects: [{ name: "", playlist: "" }],
              slack_channel: state.config.operator_slack_channel || "",
              lecturers: [],
              framing: "off",
            }
          : state.config.groups[name];
      if (!group) throw Error(i18nText("ui.groups.group_not_found_refresh_the_list"));
      try {
        if (state.playlists === null) state.playlists = await api("/api/admin/youtube/playlists");
      } catch (error) {
        if (error.message === "auth") throw error;
        state.playlists = [];
      }
      if (!slot.valid()) return;
      slot.box.innerHTML =
        panelHead(
          name === null
            ? i18nText("ui.groups.new_group_2")
            : name + ` ${i18nText("ui.groups.editing")}`,
        ) +
        `<form class="gp-form" id="group-core-form" novalidate><fieldset class="gp-fields-root"><label class="fld"><span>${i18nText("ui.groups.course")}</span><select class="input" name="course_id" required><option value="">${i18nText("ui.groups.choose_course")}</option>${courseData.courses.map((c) => `<option value="${esc(c.id)}"${c.id === linked ? " selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label><label class="fld"><span>${i18nText("ui.groups.group_name")}</span><input class="input" name="name" value="${esc(name || "")}" maxlength="60" placeholder="${i18nText("ui.groups.for_example_python_01")}" required><span class="gp-help">${i18nText("ui.groups.up_to_60_letters_digits_spaces_hyphens")}</span></label><div><h3>${i18nText("ui.courses.subjects_2")}</h3><p class="gp-help">${i18nText("ui.groups.1_5_subjects_playlists_can_be_added")}</p><div id="group-subject-fields" class="gp-form"></div><button class="btn sm" type="button" data-group-action="add-subject" id="group-add-subject" style="margin-top:12px">${i18nText("ui.groups.subject")}</button></div>
        <div class="gp-fields"><label class="fld"><span>${i18nText("ui.groups.notification_channel_id")}</span><input class="input" name="slack_channel" value="${esc(group.slack_channel)}" placeholder="C0123456789" required><span class="gp-help">${i18nText("ui.groups.copy_the_id_from_the_channel_settings")}</span></label><label class="fld"><span>${i18nText("ui.groups.classroom_course_id_optional")}</span><input class="input" name="classroom_course_id" value="${esc(group.classroom_course_id || "")}" placeholder="${i18nText("ui.groups.leave_blank_to_disable")}"></label></div>
        <label class="fld"><span>${i18nText("ui.groups.framing")}</span><select class="input" name="framing"><option value="auto"${group.framing === "auto" ? " selected" : ""}>${i18nText("ui.groups.automatic")}</option><option value="projector"${group.framing === "projector" ? " selected" : ""}>${i18nText("ui.groups.screen_framing")}</option><option value="off"${group.framing === "off" || !group.framing ? " selected" : ""}>${i18nText("ui.groups.full_frame")}</option><option value="board"${group.framing === "board" ? " selected" : ""}>${i18nText("ui.groups.board_framing")}</option></select><span class="gp-help">${i18nText("ui.groups.applies_to_future_processing_for_this_group")}</span></label>
        <p class="gp-help">${i18nText("ui.groups.set_course_dates_and_team_members_in")}</p><div id="group-core-error" role="alert" hidden></div><div class="gp-actions"><button class="btn primary" type="submit">${i18nText("ui.groups.save_group")}</button><button class="btn" type="button" data-group-action="close">${i18nText("ui.accounts.cancel")}</button></div></fieldset></form>`;
      group.subjects.forEach(addSubject);
      controls();
      const form = find("#group-core-form");
      form.onsubmit = async (event) => {
        event.preventDefault();
        if (state.busy) return;
        message(find("#group-core-error"), "");
        const values = new FormData(form),
          target = String(values.get("name") || "").trim();
        const subjects = [...view.querySelectorAll(".gp-subject-edit")].map((row) => ({
          name: $(".gp-subject-name-input", row).value.trim(),
          playlist:
            $(".gp-playlist-select", row).value === "__manual__"
              ? $(".gp-playlist-input", row).value.trim()
              : $(".gp-playlist-select", row).value,
        }));
        if (!values.get("course_id"))
          return message(find("#group-core-error"), i18nText("courses.choose_a_course"));
        if (!target || subjects.some((subject) => !subject.name))
          return message(
            find("#group-core-error"),
            i18nText("ui.groups.enter_the_group_name_and_all_subject"),
          );
        if (target !== name && Object.prototype.hasOwnProperty.call(state.config.groups, target))
          return message(
            find("#group-core-error"),
            i18nText("ui.groups.a_group_with_this_name_already_exists"),
          );
        if (
          new Set(subjects.map((subject) => subject.name.toLocaleLowerCase())).size !==
          subjects.length
        )
          return message(
            find("#group-core-error"),
            i18nText("ui.groups.subject_names_must_be_unique"),
          );
        if (!String(values.get("slack_channel") || "").trim())
          return message(
            find("#group-core-error"),
            i18nText("ui.groups.enter_a_notification_channel_id"),
          );
        const payload = {
          ...group,
          subjects,
          slack_channel: String(values.get("slack_channel")).trim(),
          lecturers: group.lecturers || [],
          framing: values.get("framing"),
        };
        const classroom = String(values.get("classroom_course_id") || "").trim();
        if (classroom) payload.classroom_course_id = classroom;
        else delete payload.classroom_course_id;
        if (name !== null && target !== name) payload.rename_to = target;
        state.busy = true;
        controls();
        try {
          await api("/api/admin/groups/" + encodeURIComponent(name === null ? target : name), {
            method: "PUT",
            body: JSON.stringify(payload),
          });
          await api("/api/admin/group-course/" + encodeURIComponent(target), {
            method: "PUT",
            body: JSON.stringify({ course_id: values.get("course_id") }),
          });
          if (!slot.valid()) return;
          state.dirty = false;
          state.panelKind = null;
          slot.box.hidden = true;
          state.config = null;
          await loadCards();
          toast(i18nText("ui.groups.group_saved"));
          if (initialAction)
            location.hash = "#/groups/" + encodeURIComponent(target) + "?tab=subjects";
        } catch (error) {
          if (slot.valid() && error.message !== "auth")
            message(find("#group-core-error"), errorText(error));
        } finally {
          state.busy = false;
          if (alive()) controls();
        }
      };
      const row = focusSubject
        ? [...view.querySelectorAll(".gp-subject-edit")].find(
            (item) => $(".gp-subject-name-input", item).value === focusSubject,
          )
        : null;
      if (row) {
        $(".gp-playlist-select", row).value = "__manual__";
        syncPlaylist(row);
        $(".gp-playlist-input", row).focus({ preventScroll: true });
      } else form.elements.name.focus({ preventScroll: true });
      reveal(slot);
    } catch (error) {
      if (slot.valid() && error.message !== "auth")
        slot.box.innerHTML =
          panelHead(i18nText("ui.groups.edit_group")) +
          `<div class="alert err" role="alert">${esc(errorText(error))}</div>`;
    }
  }
  function addSubject(subject = { name: "", playlist: "" }) {
    if (view.querySelectorAll(".gp-subject-edit").length >= 5) return;
    const row = document.createElement("div");
    row.className = "gp-subject-edit";
    const playlist = subject.playlist || "",
      selected =
        playlist === ""
          ? ""
          : (state.playlists || []).some((item) => item.id === playlist)
            ? playlist
            : "__manual__";
    row.innerHTML = `<div class="gp-subject-edit-head"><label class="fld"><span>${i18nText("ui.app.subject")}</span><input class="input gp-subject-name-input" value="${esc(subject.name)}" maxlength="40" placeholder="${i18nText("ui.groups.for_example_python")}" required></label><button class="btn danger sm" type="button" data-group-action="remove-subject" aria-label="${i18nText("ui.groups.remove_subject")}">✕</button></div><label class="fld"><span>${i18nText("ui.groups.playlist")}</span><select class="input gp-playlist-select"><option value=""${selected === "" ? " selected" : ""}>${i18nText("ui.groups.none_yet")}</option><option value="__manual__"${selected === "__manual__" ? " selected" : ""}>${i18nText("ui.groups.use_a_link")}</option>${(state.playlists || []).map((item) => `<option value="${esc(item.id)}"${item.id === selected ? " selected" : ""}>${esc(item.title || item.id)}</option>`).join("")}</select></label><label class="fld gp-playlist-manual"><span>${i18nText("ui.groups.playlist_link")}</span><input class="input gp-playlist-input" value="${esc(playlist)}" placeholder="https://www.youtube.com/playlist?list=PL…"></label>`;
    find("#group-subject-fields").append(row);
    syncPlaylist(row);
    controls();
  }
  function syncPlaylist(row) {
    $(".gp-playlist-manual", row).hidden = $(".gp-playlist-select", row).value !== "__manual__";
  }

  async function openTeam(name, focusEnd = false) {
    const slot = panel("team", name);
    if (!slot) return;
    try {
      const data = await api(
        (admin ? "/api/admin/group-details/" : "/api/groups/") +
          encodeURIComponent(name) +
          (admin ? "" : "/team"),
      );
      if (!slot.valid()) return;
      const team = data.team || { lecturers: [], tas: [] };
      slot.box.innerHTML =
        panelHead(name + ` ${i18nText("ui.groups.team")}`) +
        `<div class="${admin ? "gp-team-grid" : ""}"><div class="gp-team-section" id="group-team-people">${groupPeople(team)}</div>${admin ? '<div id="group-team-editor"></div>' : ""}</div>`;
      if (!admin) {
        reveal(slot);
        return;
      }
      const subjectData = data.subjects || [],
        options = data.options || { lecturers: [], tas: [] };
      find("#group-team-editor").innerHTML =
        `<h3>${i18nText("ui.groups.course_dates_and_assignments")}</h3><form id="group-team-form" class="gp-form" novalidate><fieldset class="gp-fields-root"><div class="gp-fields"><label class="fld"><span>${i18nText("ui.groups.course_start")}</span><input class="input" type="date" name="course_start" value="${esc(data.course_start || "")}"></label><label class="fld"><span>${i18nText("ui.groups.course_end")}</span><input class="input" type="date" name="course_end" value="${esc(data.course_end || "")}"></label></div><p class="gp-help">${i18nText("ui.groups.the_group_is_marked_completed_after_the")}</p>
        <div class="gp-team-form-subjects">${subjectData.map((subject, index) => `<div class="gp-team-subject"><h4>${esc(subject.name)}</h4><label class="fld"><span>${i18nText("ui.groups.lecturer")}</span><select class="input" name="lecturer_${index}"><option value="">${i18nText("ui.groups.not_assigned")}</option>${options.lecturers.map((person) => `<option value="${person.id}"${person.id === subject.lecturer_id ? " selected" : ""}>${esc(person.name)}</option>`).join("")}</select></label><div class="gp-fields"><label class="fld"><span>${i18nText("ui.groups.subject_start")}</span><input class="input gp-subject-start" type="date" name="subject_start_${index}" value="${esc(subject.start_date || "")}"></label><label class="fld"><span>${i18nText("ui.groups.subject_end")}</span><input class="input gp-subject-end" type="date" name="subject_end_${index}" value="${esc(subject.end_date || "")}"></label></div></div>`).join("")}</div>
        <fieldset class="account-group-checks" hidden><legend>${i18nText("ui.groups.student_assistants")}</legend><p class="gp-help">${i18nText("ui.groups.choose_students_from_this_group_an_assistant")}</p><div class="gp-ta-options">${options.tas.map((person) => `<label class="gp-check"><input type="checkbox" name="ta_ids" value="${person.id}"${(data.ta_ids || []).includes(person.id) ? " checked" : ""}><span>${esc(person.name)}</span></label>`).join("") || `<p class="muted">${i18nText("ui.groups.no_eligible_students_in_this_group_yet")}</p>`}</div></fieldset>
        <div id="group-team-error" role="alert" hidden></div><div class="gp-actions"><button class="btn primary" type="submit">${i18nText("ui.groups.save_dates_and_team")}</button></div></fieldset></form>`;
      const form = find("#group-team-form");
      const limits = () => {
        form.querySelectorAll(".gp-subject-start,.gp-subject-end").forEach((input) => {
          input.min = form.elements.course_start.value;
          input.max = form.elements.course_end.value;
        });
      };
      form.elements.course_start.onchange = limits;
      form.elements.course_end.onchange = limits;
      limits();
      form.onsubmit = async (event) => {
        event.preventDefault();
        if (state.busy) return;
        message(find("#group-team-error"), "");
        const values = new FormData(form),
          start = values.get("course_start") || null,
          end = values.get("course_end") || null;
        if (Boolean(start) !== Boolean(end) || (start && start > end))
          return message(
            find("#group-team-error"),
            i18nText("ui.groups.enter_course_start_and_end_dates_in"),
          );
        const subjects = subjectData.map((subject, index) => ({
          name: subject.name,
          lecturer_id: values.get("lecturer_" + index)
            ? Number(values.get("lecturer_" + index))
            : null,
          start_date: values.get("subject_start_" + index) || null,
          end_date: values.get("subject_end_" + index) || null,
        }));
        if (
          subjects.some(
            (subject) =>
              Boolean(subject.start_date) !== Boolean(subject.end_date) ||
              (subject.start_date &&
                (!start ||
                  subject.start_date < start ||
                  subject.end_date > end ||
                  subject.start_date > subject.end_date)),
          )
        )
          return message(
            find("#group-team-error"),
            i18nText("ui.groups.subject_start_and_end_dates_must_fall"),
          );
        state.busy = true;
        controls();
        try {
          const updated = await api("/api/admin/group-details/" + encodeURIComponent(name), {
            method: "PUT",
            body: JSON.stringify({
              course_start: start,
              course_end: end,
              subjects,
              ta_ids: values.getAll("ta_ids").map(Number),
            }),
          });
          if (!slot.valid()) return;
          state.dirty = false;
          find("#group-team-people").innerHTML = groupPeople(updated.team || team);
          await loadCards();
          toast(i18nText("ui.groups.dates_and_team_saved"));
          if (initialAction) location.hash = "#/groups/" + encodeURIComponent(name) + "?tab=team";
        } catch (error) {
          if (slot.valid() && error.message !== "auth")
            message(find("#group-team-error"), errorText(error));
        } finally {
          state.busy = false;
          if (alive()) controls();
        }
      };
      controls();
      if (focusEnd) form.elements.course_end.focus({ preventScroll: true });
      reveal(slot);
    } catch (error) {
      if (slot.valid() && error.message !== "auth")
        slot.box.innerHTML =
          panelHead(name + ` ${i18nText("ui.groups.team")}`) +
          `<div class="alert err" role="alert">${esc(errorText(error))}</div>`;
    }
  }
  async function openStyle(name) {
    const slot = panel("style", name);
    if (!slot) return;
    try {
      state.styles = await api("/api/admin/thumbnail-styles");
      if (!slot.valid()) return;
      let selected = state.styles.groups[name]?.style ?? 0;
      function render() {
        slot.box.innerHTML =
          panelHead(name + ` ${i18nText("ui.groups.thumbnail_style_2")}`) +
          `<p class="gp-help">${i18nText("ui.groups.applies_to_future_thumbnails_existing_lesson_thumbnails")}</p><div class="gp-style-grid">${state.styles.styles.map((style) => `<button class="gp-style${style.id === selected ? " on" : ""}" type="button" data-style-id="${style.id}" aria-pressed="${style.id === selected}"><img src="${esc(style.preview)}" loading="lazy" alt="${i18nText("ui.groups.style")} ${style.id + 1}"><span>${i18nText("ui.groups.style")} ${style.id + 1}</span></button>`).join("")}</div><div id="group-style-error" role="alert" hidden></div><div class="gp-actions" style="margin-top:16px"><button class="btn primary" id="group-style-save" type="button">${i18nText("ui.groups.save_style")}</button></div>`;
        slot.box.querySelectorAll("[data-style-id]").forEach(
          (button) =>
            (button.onclick = () => {
              if (state.busy) return;
              selected = Number(button.dataset.styleId);
              state.dirty = true;
              render();
            }),
        );
        find("#group-style-save").onclick = async () => {
          if (state.busy) return;
          state.busy = true;
          controls();
          find("#group-style-save").disabled = true;
          try {
            await api("/api/admin/thumbnail-styles/" + encodeURIComponent(name), {
              method: "PUT",
              body: JSON.stringify({ style: selected }),
            });
            if (!slot.valid()) return;
            state.dirty = false;
            state.panelKind = null;
            slot.box.hidden = true;
            await loadCards();
            toast(i18nText("ui.groups.thumbnail_style_saved"));
          } catch (error) {
            if (slot.valid() && error.message !== "auth")
              message(find("#group-style-error"), errorText(error));
          } finally {
            state.busy = false;
            if (slot.valid()) {
              controls();
              const save = find("#group-style-save");
              if (save) save.disabled = false;
            }
          }
        };
      }
      render();
      reveal(slot);
    } catch (error) {
      if (slot.valid() && error.message !== "auth")
        slot.box.innerHTML =
          panelHead(name + ` ${i18nText("ui.groups.thumbnail_style_2")}`) +
          `<div class="alert err" role="alert">${esc(errorText(error))}</div>`;
    }
  }
  view.addEventListener("input", (event) => {
    if (event.target.closest(".gp-form")) state.dirty = true;
  });
  view.addEventListener("change", (event) => {
    if (event.target.closest(".gp-form")) state.dirty = true;
    if (event.target.classList.contains("gp-playlist-select"))
      syncPlaylist(event.target.closest(".gp-subject-edit"));
  });
  view.onclick = async (event) => {
    const button = event.target.closest("[data-group-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.groupAction,
      name = button.dataset.name;
    button.closest(".gp-menu")?.removeAttribute("open");
    if (action === "refresh") {
      await loadCards();
      return;
    }
    if (action === "filter") {
      state.status = button.dataset.status;
      history.replaceState(null, "", "#/groups?status=" + state.status);
      await loadCards();
      return;
    }
    if (action === "close") {
      closePanel();
      return;
    }
    if (action === "team") {
      await openTeam(name);
      return;
    }
    if (!admin) return;
    if (action === "new") {
      await openEditor();
      return;
    }
    if (action === "edit" || action === "playlist") {
      await openEditor(name, button.dataset.subject);
      return;
    }
    if (action === "dates") {
      await openTeam(name, true);
      return;
    }
    if (action === "style") {
      await openStyle(name);
      return;
    }
    if (action === "add-subject") {
      addSubject();
      state.dirty = true;
      return;
    }
    if (action === "remove-subject" && view.querySelectorAll(".gp-subject-edit").length > 1) {
      button.closest(".gp-subject-edit").remove();
      state.dirty = true;
      controls();
      return;
    }
    if (
      action === "delete" &&
      !confirm(
        `${i18nText("ui.groups.delete_settings_for")}${name}${i18nText("ui.groups.punctuation")}`,
      )
    )
      return;
    if (
      action === "complete" &&
      !confirm(
        button.dataset.completed === "true"
          ? `${i18nText("ui.app.set")}${name}${i18nText("ui.groups.as_completed_its_history_will_be_retained")}`
          : `${i18nText("ui.groups.reactivate_2")}${name}${i18nText("ui.groups.you_can_also_extend_the_course_end")}`,
      )
    )
      return;
    if (!["delete", "complete"].includes(action) || state.busy) return;
    state.busy = true;
    controls();
    message(find("#groups-message"), "");
    try {
      await api(
        action === "delete"
          ? "/api/admin/groups/" + encodeURIComponent(name)
          : "/api/admin/group-status/" + encodeURIComponent(name),
        {
          method: action === "delete" ? "DELETE" : "POST",
          ...(action === "complete"
            ? { body: JSON.stringify({ completed: button.dataset.completed === "true" }) }
            : {}),
        },
      );
      if (!alive()) return;
      await loadCards();
      toast(
        action === "delete"
          ? i18nText("ui.groups.group_deleted")
          : i18nText("ui.groups.group_status_saved"),
      );
    } catch (error) {
      if (alive() && error.message !== "auth") message(find("#groups-message"), errorText(error));
    } finally {
      state.busy = false;
      if (alive()) controls();
    }
  };
  await loadCards();
  if (initialAction) {
    find("#groups-cards").hidden = true;
    find(".gp-toolbar").hidden = true;
    find(".top").innerHTML =
      `<div><a class="btn" href="#/groups/${encodeURIComponent(initialAction.name || "")}">${i18nText("ui.groups.back_to_group")}</a><h1>${initialAction.kind === "team" ? i18nText("ui.groups.dates_and_lecturers") : i18nText("ui.groups.edit_subjects")}</h1></div>`;
    if (initialAction.kind === "team") await openTeam(initialAction.name);
    else await openEditor(initialAction.name ?? null, initialAction.subject);
  }
}
