"use strict";
const ACCOUNT_ROLE = {admin:i18nText("ui.accounts.administrator"),lecturer:i18nText("ui.groups.lecturer"),student:i18nText("ui.accounts.student")};
function accountLink(user) {
  if (!user?.id) return "";
  const initials = user.name.trim().split(/\s+/).slice(0,2).map(x=>x[0]).join("");
  return `<a class="account-avatar" href="#/profile" aria-label="${i18nText("ui.accounts.my_settings")}">${user.avatar ? `<img src="${esc(user.avatar)}" alt="">` : esc(initials)}</a>`;
}
function refreshAccountAvatars() {document.querySelectorAll(".account-avatar").forEach(node=>{node.outerHTML=accountLink(S.user);});}
function accountPublic(title,body) {
  clearInterval(S.poll);
  app.innerHTML=`<main class="account-public"><div class="account-public-head">${languageSelector()}<div class="brand brand-pair">${reelBrand()}</div><button class="icon-btn" id="theme" aria-label="${i18nText("ui.accounts.change_theme")}">${ic(isDark()?"sun":"moon")}</button></div><section class="card account-form"><h1>${esc(title)}</h1>${body}<p id="account-error" role="alert" class="err-text"></p><a class="btn" href="#/login">${i18nText("ui.accounts.back_to_sign_in")}</a></section></main>`;
}
function accountField(name,label,type="text",extra="") {return `<label class="fld"><span>${label}</span><input class="input" name="${name}" type="${type}" ${extra} required></label>`;}
function bindAccountForm(fn) {
  $("#account-form").onsubmit=async event=>{event.preventDefault();const form=event.target,button=form.querySelector('button[type=submit]');button.disabled=true;
    try {await fn(Object.fromEntries(new FormData(form)),form);} catch(error){const target=$("#account-error");if(target)target.textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}};
}
function signedIn(data) {if(data.second_factor){viewSecondFactor();return;}S.role=data.role;S.user=data.user||null;location.hash=data.role==="bootstrap"?"#/setup":data.role==="student"?"#/library":"#/";location.reload();}
function publicAccountRoute() {
  const hash=location.hash;
  if(hash==='#/privacy'){viewPrivacy();return true;}
  if(hash==="#/forgot"){viewForgot();return true;}
  let match=hash.match(/^#\/(invite|reset)\/([A-Za-z0-9_-]{43})$/);
  if(match){if(match[1]==="invite")viewInvite(match[2]);else viewReset(match[2]);return true;}
  return false;
}
function viewForgot() {
  accountPublic(i18nText("ui.accounts.forgot_your_password"),`<p>${i18nText("ui.accounts.enter_your_username_or_email_address")}</p><form id="account-form">${accountField("login",i18nText("ui.accounts.username_or_email"),"text",'autocomplete="username" maxlength="254"')}<button class="btn primary" type="submit">${i18nText("ui.accounts.send_link")}</button></form>`);
  bindAccountForm(async data=>{const result=await api('/api/accounts/forgot',{method:'POST',body:JSON.stringify(data)});$("#account-form").innerHTML=`<p role="status">${esc(result.message)}</p>`;});
}
async function viewInvite(token) {
  accountPublic(i18nText("ui.accounts.invitation_from_reel"),(`<p role="status">${i18nText("ui.accounts.checking_invitation")}</p>`));
  try {
    const data=await api('/api/accounts/invitation',{method:'POST',body:JSON.stringify({token})});
    accountPublic(i18nText("ui.accounts.accept_invitation"),`<dl class="invite-facts"><dt>${i18nText("ui.accounts.name")}</dt><dd>${esc(data.name)}</dd><dt>${i18nText("ui.accounts.email")}</dt><dd>${esc(data.email)}</dd><dt>${i18nText("ui.accounts.role")}</dt><dd>${esc(ACCOUNT_ROLE[data.role])}</dd><dt>${i18nText("ui.accounts.group")}</dt><dd>${esc(data.role==='admin'?i18nText("ui.accounts.all_groups"):data.groups.join(', '))}</dd></dl><p class="muted">${i18nText("ui.accounts.invitation_details_cannot_be_edited_choose_your")}</p><form id="account-form">${accountField('login',i18nText("ui.accounts.username"),'text','autocomplete="username" minlength="3" maxlength="64"')}${accountField('password',i18nText("ui.accounts.password_at_least_10_characters"),'password','autocomplete="new-password" minlength="10" maxlength="1024"')}<label class="fld"><span><input type="checkbox" name="recording_consent" required> ${i18nText("ui.accounts.lessons_are_recorded_and_faces_are_blurred")} <a href="#/privacy" target="_blank" rel="noopener">${i18nText("ui.accounts.privacy_policy")}</a> ${i18nText("ui.accounts.and_agree")}</span></label>${data.guardian_required?`${accountField('guardian_name',i18nText("ui.accounts.parent_or_guardian_name"))}${accountField('guardian_email',i18nText("ui.accounts.parent_or_guardian_email"),'email')}<label class="fld"><span><input type="checkbox" name="guardian_consent" required> ${i18nText("ui.accounts.i_am_the_parent_or_legal_guardian")}</span></label>`:''}<button class="btn primary" type="submit">${i18nText("ui.accounts.create_account")}</button></form>`);
    bindAccountForm(async form=>signedIn(await api('/api/accounts/register',{method:'POST',body:JSON.stringify({...form,recording_consent:form.recording_consent==='on',guardian_consent:form.guardian_consent==='on',token})})));
  }catch(error){$("#account-error").textContent=error.message;}
}
function viewReset(token) {
  accountPublic(i18nText("ui.accounts.new_password"),`<p>${i18nText("ui.accounts.you_will_need_to_sign_in_again")}</p><form id="account-form">${accountField('password',i18nText("ui.accounts.password_at_least_10_characters"),'password','autocomplete="new-password" minlength="10" maxlength="1024"')}<button class="btn primary" type="submit">${i18nText("ui.accounts.save_password")}</button></form>`);
  bindAccountForm(async data=>{await api('/api/accounts/reset',{method:'POST',body:JSON.stringify({...data,token})});S.role=null;S.user=null;location.hash='#/login';renderLogin(i18nText("ui.accounts.password_changed_sign_in_with_your_new"));});
}
function viewSetup() {
  accountPublic(i18nText("ui.accounts.create_administrator_account"),`<p>${i18nText("ui.accounts.this_is_the_initial_setup_create_a")}</p><form id="account-form">${accountField('name',i18nText("ui.accounts.full_name"),'text',(`autocomplete="name" placeholder="${i18nText("ui.accounts.full_name")}" maxlength="120"`))}${accountField('email',i18nText("ui.accounts.email"),'email','autocomplete="email" maxlength="254"')}${accountField('login',i18nText("ui.accounts.username"),'text','autocomplete="username" minlength="3" maxlength="64"')}${accountField('password',i18nText("ui.accounts.new_password"),'password','autocomplete="new-password" minlength="10" maxlength="1024"')}<label class="fld"><span><input type="checkbox" name="recording_consent" required> ${i18nText("ui.accounts.lessons_are_recorded_and_faces_are_blurred_2")} <a href="#/privacy" target="_blank" rel="noopener">${i18nText("ui.accounts.privacy_policy_2")}</a>${i18nText("ui.accounts.punctuation")}</span></label><button class="btn primary" type="submit">${i18nText("ui.accounts.create_administrator_account")}</button></form>`);
  bindAccountForm(async data=>signedIn(await api('/api/accounts/bootstrap',{method:'POST',body:JSON.stringify({...data,recording_consent:data.recording_consent==='on'})})));
  const back=document.querySelector('.account-form>a');back.textContent=i18nText("ui.accounts.sign_out");back.onclick=async event=>{event.preventDefault();await api('/api/logout',{method:'POST'});S.role=null;S.user=null;location.hash='#/login';renderLogin();};
}
async function viewProfile() {
  if($("#profile-name"))return;
  const user=await api('/api/accounts/profile');S.user=user;
  $("#main").innerHTML=`<div class="top"><div><div class="eyebrow">${i18nText("ui.accounts.my_account")}</div><h1>${i18nText("ui.accounts.personal_settings")}</h1><p>${esc(user.email)} · ${esc(ACCOUNT_ROLE[user.role])} · ${esc(user.role==='admin'?i18nText("ui.accounts.all_groups"):user.groups.join(', '))}</p></div></div><div class="grid two"><section class="card"><h2>${i18nText("ui.accounts.name_and_profile_picture")}</h2><div class="profile-avatar">${accountLink(user)}</div><form id="profile-avatar-form"><label class="fld"><span>${i18nText("ui.accounts.jpeg_png_or_webp_up_to_5")}</span><input class="input" id="avatar-file" type="file" accept="image/jpeg,image/png,image/webp" required></label><button class="btn" type="submit">${i18nText("ui.accounts.save_picture")}</button></form><form id="profile-name"><label class="fld"><span>${i18nText("ui.accounts.name")}</span><input class="input" name="name" value="${esc(user.name)}" maxlength="120" required></label><button class="btn primary" type="submit">${i18nText("ui.accounts.save_name")}</button></form><p id="profile-message" role="status"></p></section><section class="card">${S.role==='admin'?(`<button type="button" class="btn" id="forget-devices">${i18nText("ui.accounts.forget_all_remembered_devices")}</button>`):''}<h2>${i18nText("ui.accounts.privacy")}</h2><p><a href="#/privacy">${i18nText("ui.accounts.privacy_policy_3")}</a> · <a href="#/data-request">${i18nText("ui.accounts.data_deletion_request")}</a></p><h2>${i18nText("ui.accounts.change_password")}</h2><p>${i18nText("ui.accounts.all_sessions_will_end")}</p><form id="profile-password">${accountField('current_password',i18nText("ui.accounts.current_password"),'password','autocomplete="current-password"')}${accountField('password',i18nText("ui.accounts.new_password"),'password','autocomplete="new-password" minlength="10" maxlength="1024"')}<button class="btn primary" type="submit">${i18nText("ui.accounts.change_password")}</button></form></section></div>`;
  const report=message=>{const el=$("#profile-message");if(el)el.textContent=message;};
  if($('#forget-devices'))$('#forget-devices').onclick=async()=>{await api('/api/admin-auth/forget-devices',{method:'POST'});toast(i18nText("ui.accounts.remembered_devices_cleared"));};
  for(const id of ['profile-name','profile-password']) $("#"+id).onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{await api('/api/accounts/'+(id==='profile-name'?'profile':'password'),{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});if(id==='profile-password'){S.role=null;S.user=null;renderLogin(i18nText("ui.accounts.password_changed_sign_in_again"));}else{S.user.name=event.target.elements.name.value;refreshAccountAvatars();report(i18nText("ui.accounts.name_saved"));}}catch(error){report(error.message);}finally{button.disabled=false;}};
  $("#profile-avatar-form").onsubmit=async event=>{event.preventDefault();const file=$("#avatar-file").files[0];if(!file)return;if(file.size>5*1024*1024){report(i18nText("account_api.the_maximum_image_size_is_5_mb"));return;}const button=event.target.querySelector('button');button.disabled=true;try{const response=await fetch('/api/accounts/avatar',{method:'POST',credentials:'same-origin',headers:{'X-Requested-With':'reel','Content-Type':file.type},body:file});const data=await response.json();if(!response.ok)throw Error(data.detail);S.user.avatar=data.avatar;refreshAccountAvatars();report(i18nText("ui.accounts.picture_saved"));}catch(error){report(error.message);}finally{button.disabled=false;}};
}
/* scoped account directory, invitations and a separate edit page. */
function accountsStyles() {
  if ($("#accounts-css")) return;
  const style = document.createElement("style");
  style.id = "accounts-css";
  style.textContent = `
    .accounts-page{display:grid;gap:20px;min-width:0}.accounts-page .top{margin-bottom:0;flex-wrap:wrap}.accounts-page .card{margin:0}.accounts-page h2{margin-bottom:14px}.accounts-page [hidden]{display:none!important}
    .accounts-page .users-tabs{display:flex;gap:8px;flex-wrap:wrap}.accounts-page .users-tab{display:inline-flex;gap:8px;align-items:center;min-height:44px;padding:10px 14px;border:1px solid var(--border);border-radius:12px;text-decoration:none;color:var(--muted);background:var(--surface)}.accounts-page .users-tab.on{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}.accounts-page .users-tab strong{font-size:12px;min-width:20px;text-align:center}
    .accounts-page .users-filters{display:grid;grid-template-columns:minmax(220px,1fr) minmax(150px,220px) minmax(140px,190px) auto;gap:12px;align-items:end}.accounts-page .users-filters .fld{margin:0}.accounts-page .users-filters input,.accounts-page .users-filters select{width:100%}
    .accounts-page .users-table-wrap{overflow-x:auto}.accounts-page .users-table{width:100%;border-collapse:collapse;font-size:13px}.accounts-page .users-table th{font-size:12px;font-weight:600;color:var(--muted);text-align:left;padding:12px;border-bottom:1px solid var(--border)}.accounts-page .users-table td{padding:13px 12px;border-bottom:1px solid var(--border);vertical-align:middle}.accounts-page .users-table tr:last-child td{border-bottom:0}
    .accounts-page .users-person{display:flex;gap:12px;align-items:center;min-width:210px;color:inherit;text-decoration:none}.accounts-page .users-person>span:last-child{min-width:0}.accounts-page .users-person b{display:block;font-weight:650;overflow-wrap:anywhere}.accounts-page .users-person small{display:block;color:var(--muted);overflow-wrap:anywhere;font-size:12px;margin-top:3px}.accounts-page .users-avatar{width:40px;height:40px;flex:none;border-radius:50%;display:grid;place-items:center;overflow:hidden;background:var(--accent-soft);color:var(--accent);font-size:14px;font-weight:700}.accounts-page .users-avatar img{width:100%;height:100%;object-fit:cover}.accounts-page .users-groups{display:flex;gap:5px;flex-wrap:wrap;max-width:260px}.accounts-page .users-groups .pill{font-size:12px;overflow-wrap:anywhere;white-space:normal}.accounts-page .users-status{display:inline-flex;gap:6px;align-items:center;font-size:12px;white-space:nowrap}.accounts-page .users-status::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--faint)}.accounts-page .users-status.active::before{background:var(--ok)}.accounts-page .users-status.pending::before{background:var(--warn)}.accounts-page .users-time{font-size:12px;color:var(--muted);white-space:nowrap}.accounts-page .users-row-actions{display:flex;gap:7px;flex-wrap:wrap}
    .accounts-page .users-footer{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;padding-top:16px;border-top:1px solid var(--border);margin-top:12px}.accounts-page .users-pagination{display:flex;gap:8px;align-items:center}.accounts-page .users-pagination .btn[aria-disabled=true]{opacity:.45;pointer-events:none}.accounts-page .users-empty{text-align:center;padding:36px 16px;color:var(--muted)}.accounts-page .users-error{white-space:pre-wrap;overflow-wrap:anywhere}.accounts-page .users-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.accounts-page .users-editor{max-width:960px}.accounts-page .users-edit-form{display:grid;gap:16px}.accounts-page .users-edit-form>.fld{margin:0}.accounts-page .users-edit-form fieldset{min-width:0}.accounts-page .users-toggle{display:flex;gap:10px;align-items:flex-start}.accounts-page .users-toggle input{width:20px;height:20px;flex:none;accent-color:var(--accent);margin-top:3px}.accounts-page .users-help{font-size:12.5px;color:var(--muted);line-height:1.65;margin:5px 0 0}.accounts-page .users-edit-actions{display:flex;gap:10px;flex-wrap:wrap}.accounts-page .users-editor-header{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.accounts-page .users-editor-header .users-avatar{width:56px;height:56px;font-size:19px}.accounts-page .users-editor-header h1{font-size:24px;margin:0}.accounts-page .users-editor-header p{margin:4px 0 0;color:var(--muted);overflow-wrap:anywhere}.accounts-page .users-danger{border-color:var(--err)}
    .accounts-page .users-invite-link{display:grid;gap:12px}.accounts-page .users-invite-link input{width:100%;font-family:var(--mono);font-size:12px}.accounts-page .users-invite-link p{margin:0}.accounts-page .users-link-result{font-size:13px}.accounts-page .users-groups-manager>summary{cursor:pointer;font-weight:600;min-height:28px}.accounts-page .users-group-item{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 0;border-bottom:1px solid var(--border)}.accounts-page .users-group-item:last-child{border-bottom:0}.accounts-page .users-group-item>span:first-child{flex:1;min-width:140px;overflow-wrap:anywhere}
    @media(max-width:1000px){.accounts-page .users-filters{grid-template-columns:minmax(180px,1fr) minmax(150px,1fr)}.accounts-page .users-grid{grid-template-columns:1fr}}
    @media(max-width:700px){.accounts-page .users-table-wrap{overflow:visible}.accounts-page .users-table,.accounts-page .users-table tbody{display:block}.accounts-page .users-table thead{display:none}.accounts-page .users-table tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:16px;border:1px solid var(--border);border-radius:12px;margin-bottom:12px;background:var(--surface)}.accounts-page .users-table td{display:block;border:0;padding:0;min-width:0}.accounts-page .users-table td::before{content:attr(data-label);display:block;font-size:11px;color:var(--muted);margin-bottom:4px}.accounts-page .users-table td.users-person-cell,.accounts-page .users-table td.users-actions-cell{grid-column:1/-1}.accounts-page .users-table td.users-person-cell::before,.accounts-page .users-table td.users-actions-cell::before{display:none}.accounts-page .users-person{min-width:0}.accounts-page .users-time{white-space:normal}.accounts-page .users-groups{max-width:none}.accounts-page .users-footer{align-items:flex-start}.accounts-page .users-row-actions .btn{flex:1}.accounts-page .users-edit-actions .btn{flex:1;justify-content:center}}
    @media(max-width:460px){.accounts-page .users-filters{grid-template-columns:1fr}.accounts-page .users-tabs{gap:6px}.accounts-page .users-tab{padding:9px 11px;font-size:12px}.accounts-page .users-pagination{width:100%;justify-content:space-between}}
  `;
  document.head.append(style);
}

const USERS_TABS = [
  ["student", i18nText("ui.accounts.students")], ["lecturer", i18nText("ui.accounts.lecturers")], ["admin", i18nText("ui.accounts.administrators")],
  ["invitations", i18nText("ui.accounts.invitations")], ["removed", i18nText("ui.accounts.removed_accounts")]
];
function usersQuery() {
  const values = new URLSearchParams(location.hash.split("?")[1] || "");
  const tab = values.get("tab");
  const scope = values.get("group_status");
  return {
    tab: USERS_TABS.some(([key]) => key === tab) ? tab : "student",
    q: (values.get("q") || "").slice(0, 120), group: values.get("group") || "",
    group_status: ["active", "completed", "all"].includes(scope) ? scope : "active",
    page: Math.max(1, Number.parseInt(values.get("page"), 10) || 1), page_size: 20
  };
}
function usersParams(query) {
  const values = new URLSearchParams();
  values.set("tab", query.tab);
  if (query.q) values.set("q", query.q);
  if (query.group) values.set("group", query.group);
  values.set("group_status", query.group_status);
  values.set("page", String(query.page));
  values.set("page_size", String(query.page_size));
  return values;
}
function usersListHash(query) { return "#/users?" + usersParams(query); }
function usersEditHash(id, query) { return `#/users/${encodeURIComponent(id)}?` + usersParams(query); }
function usersDate(value) {
  if (!value) return i18nText("ui.accounts.never_signed_in");
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat((I18N.language === "hy" ? "hy-AM" : "en-US"), {dateStyle:"medium",timeStyle:"short"}).format(date);
}
function usersAvatar(user) {
  const initials = String(user.name || "").trim().split(/\s+/).slice(0, 2).map(part => Array.from(part)[0] || "").join("");
  return `<span class="users-avatar" aria-hidden="true">${user.avatar ? `<img loading="lazy" src="${esc(user.avatar)}" alt="">` : esc(initials || "?")}</span>`;
}
function usersShortGroup(name){
  const suffix=String(name).match(/(\d+\.\d+)$/);if(!suffix)return name;
  if(name.includes(i18nText("courses.atmk")))return (`${i18nText("courses.atmk")} `)+suffix[1];if(name.includes('1991'))return '1991 '+suffix[1];
  const prefix=[['AI Application',i18nText("courses.applications")],['AI Modern','AI'],['AI DevHack',i18nText("courses.security")],['AI Graphic',i18nText("courses.design")],['AI Junior',i18nText("courses.teenagers")],['AI UX',i18nText("courses.interface")]].find(([key])=>name.startsWith(key));
  return prefix?prefix[1]+' '+suffix[1]:name;
}
function usersGroups(user) {
  if (user.role === "admin") return (`<span class="muted">${i18nText("ui.accounts.all_groups")}</span>`);
  return `<div class="users-groups">${(user.groups || []).map(group => `<span class="pill structure-group-chip" title="${esc(group)}">${esc(usersShortGroup(group))}</span>`).join("") || (`<span class="muted">${i18nText("ui.accounts.no_group")}</span>`)}</div>`;
}
function usersError(error) {
  return error.net ? i18nText("ui.accounts.could_not_connect_to_the_server_try") : String(error.message || error);
}
function usersShowMessage(element, text, error = true) {
  if (!element) return;
  element.textContent = text || "";
  element.hidden = !text;
  element.className = text ? `alert ${error ? "err" : "ok"} users-error` : "";
}
function usersRoleOptions(current) {
  return Object.entries(ACCOUNT_ROLE).map(([key, label]) => `<option value="${key}"${current === key ? " selected" : ""}>${esc(label)}</option>`).join("");
}
function usersGroupFields(groups, selected, completed = new Set()) {
  return `<fieldset class="account-group-checks"><legend>${i18nText("ui.accounts.groups")}</legend>${groups.map(group => `<label><input type="checkbox" name="groups" value="${esc(group)}"${selected.includes(group) ? " checked" : ""}><span>${esc(group)}${completed.has(group) ? (` <span class="muted">${i18nText("ui.accounts.completed")}</span>`) : ""}</span></label>`).join("") || (`<p class="muted">${i18nText("ui.accounts.no_active_groups_manage_groups_in_settings")}</p>`)}</fieldset>`;
}
function usersSyncGroups(form) {
  const role = form.elements.role.value, fieldset = form.querySelector(".account-group-checks");
  fieldset.disabled = role === "admin";
  fieldset.querySelector("legend").textContent = role === "admin" ? i18nText("ui.accounts.all_groups") : i18nText("ui.accounts.groups");
}
function usersGroupPayload(form) {
  const values = new FormData(form), role = values.get("role");
  const groups = role === "admin" ? [] : values.getAll("groups");
  if ((role === "student" && groups.length !== 1) || (role === "lecturer" && !groups.length)) {
    throw new Error(i18nText("ui.accounts.choose_one_group_for_a_student_and"));
  }
  return {role, groups};
}

/* Keep bearer links only in the current page, never in browser storage. */
function shareAccountLink(url, options = {}) {
  const target = $("#account-link");
  if (!target) return;
  const absolute = new URL(url, location.href).href;
  const invitation = options.kind !== "reset";
  const available = options.public_available === true;
  const hint = invitation
    ? i18nText("ui.accounts.share_this_link_via_slack_whatsapp_or")
    : i18nText("ui.accounts.share_this_link_with_the_recipient_it");
  target.innerHTML = `<h2>${invitation ? i18nText("ui.accounts.invitation_link") : i18nText("ui.accounts.password_reset_link")}</h2><div class="users-invite-link">
    <p>${hint}</p><label class="fld"><span>${i18nText("ui.accounts.full_link")}</span><input class="input" id="one-time-link" readonly value="${esc(absolute)}" spellcheck="false"></label>
    <div class="row"><button class="btn primary" id="copy-account-link" type="button">${ic("copy")} ${i18nText("ui.accounts.copy_link")}</button><span id="copy-result" class="users-link-result" role="status"></span></div>
    ${!available ? (`<div class="alert warn">${i18nText("ui.accounts.this_site_is_only_available_on_this")}</div>`) : ""}
    <p class="muted">${options.mail_sent ? i18nText("ui.accounts.the_email_was_sent_automatically_you_can") : i18nText("ui.accounts.the_email_was_not_sent_automatically_copy")}</p>
    ${(options.warnings || []).map(warning => `<p class="muted">${esc(warning)}</p>`).join("")}</div>`;
  target.hidden = false;
  $("#copy-account-link").onclick = async () => {
    const result = $("#copy-result");
    try {
      await navigator.clipboard.writeText(absolute);
      if (result?.isConnected) result.textContent = i18nText("ui.accounts.link_copied");
    } catch (_) {
      const input = $("#one-time-link");
      if (input) { input.focus(); input.select(); }
      if (result?.isConnected) result.textContent = i18nText("ui.accounts.link_selected_please_copy_it");
    }
  };
  target.scrollIntoView({block:"nearest"});
}

async function viewUsers() {
  const detail = location.hash.match(/^#\/users\/(\d+)(?:\?|$)/);
  if (detail) return viewUser(Number(detail[1]));
  if ($("#account-users-page")) return;
  if (S.role !== "admin" || !S.user) return;
  accountsStyles();
  const query = usersQuery();
  $("#main").innerHTML = `<div class="accounts-page" id="account-users-page">
    <div class="top"><div><div class="eyebrow">${i18nText("ui.accounts.administration")}</div><h1>${i18nText("ui.accounts.users")}</h1><p>${i18nText("ui.accounts.manage_accounts_access_and_invitations")}</p></div><button class="btn primary" type="button" id="show-invite">${ic("send")} ${i18nText("ui.accounts.invite_user")}</button></div>
    <div class="users-tabs" id="users-tabs" aria-label="${i18nText("ui.accounts.user_categories")}"></div>
    <section class="card"><form id="users-filter-form" class="users-filters" role="search">
      <label class="fld"><span>${i18nText("ui.accounts.full_name_or_email")}</span><input class="input" id="users-search" name="q" value="${esc(query.q)}" placeholder="${i18nText("ui.accounts.name_or_email")}" autocomplete="off" maxlength="120"></label>
      <label class="fld"><span>${i18nText("ui.accounts.group")}</span><select class="input" id="users-group" name="group"><option value="">${i18nText("ui.accounts.all_groups")}</option></select></label>
      <label class="fld"><span>${i18nText("ui.accounts.group_type")}</span><select class="input" id="users-group-scope" name="group_status"><option value="active">${i18nText("ui.accounts.active_groups")}</option><option value="completed">${i18nText("ui.accounts.completed_groups")}</option><option value="all">${i18nText("ui.accounts.all")}</option></select></label>
      </form></section>
    <section class="card" id="invite-panel" hidden aria-labelledby="users-invite-title"></section>
    <section class="card" id="account-link" aria-live="polite" hidden></section>
    <section class="card"><div class="row" style="justify-content:space-between;flex-wrap:wrap;margin-bottom:12px"><h2 id="users-list-title" style="margin:0">${i18nText("ui.accounts.user_list")}</h2></div>
      <div id="users-list-error" role="alert" hidden></div><div id="users-directory" aria-live="polite"><div class="users-empty">${i18nText("ui.accounts.loading")}</div></div>
    </section>
    <details class="card"><summary>${i18nText("ui.accounts.legacy_shared_sign_in_settings")}</summary><div id="users-legacy-panel"><button class="btn" type="button" id="users-legacy-load" style="margin-top:12px">${i18nText("ui.accounts.show_setting")}</button></div></details>
  </div>`;
  const view = $("#account-users-page"), find = selector => $(selector, view);
  const alive = () => $("#account-users-page") === view && S.role === "admin";
  const state = {query, data:null, request:0, inviteBusy:false, activeGroups:null};
  find("#users-group-scope").value = query.group_status;
  let searchTimer;

  function syncHash() { history.replaceState(null, "", usersListHash(state.query)); }
  function renderTabs(counts) {
    find("#users-tabs").innerHTML = USERS_TABS.map(([key,label]) => `<a class="users-tab${state.query.tab === key ? " on" : ""}" href="${esc(usersListHash({...state.query,tab:key,page:1}))}"${state.query.tab === key ? ' aria-current="page"' : ""}>${label}<strong>${Number(counts[key]) || 0}</strong></a>`).join("");
    find("#users-list-title").textContent = USERS_TABS.find(([key]) => key === state.query.tab)[1];
  }
  function groupOptions(groups) {
    const selected = state.query.group;
    const values = [...groups];
    if (selected && !values.includes(selected)) values.unshift(selected);
    find("#users-group").innerHTML = (`<option value="">${i18nText("ui.accounts.all_groups")}</option>`) + values.map(group => `<option value="${esc(group)}"${group === selected ? " selected" : ""}>${esc(group)}</option>`).join("");
  }
  function userRows(items) {
    return items.map(user => {
      const removed = state.query.tab === "removed";
      const edit = usersEditHash(user.id, state.query);
      return `<tr class="account-clickable" tabindex="0" data-users-edit="${esc(edit)}" aria-label="${esc(user.name)}${i18nText("ui.accounts.open_account")}"><td class="users-person-cell"><a class="users-person" href="${esc(edit)}">${usersAvatar(user)}<span><b>${esc(user.name)}</b><small>${esc(user.email)}</small></span></a></td>
        <td data-label="${i18nText("ui.accounts.groups")}">${usersGroups(user)}</td><td data-label="${i18nText("ui.accounts.status")}"><span class="users-status${removed ? "" : user.active ? " active" : ""}">${removed ? i18nText("ui.accounts.removed") : user.active ? i18nText("ui.accounts.active") : i18nText("ui.accounts.disabled")}</span></td>
        <td class="users-time" data-label="${i18nText("ui.accounts.last_sign_in")}">${esc(usersDate(user.last_login_at))}</td>
        <td class="users-actions-cell"><div class="users-row-actions">${removed ? `<button class="btn sm" type="button" data-users-action="restore" data-id="${user.id}" data-name="${esc(user.name)}">${i18nText("ui.accounts.restore")}</button><button class="btn danger sm" type="button" data-users-action="delete" data-id="${user.id}" data-name="${esc(user.name)}">${i18nText("ui.accounts.delete_permanently")}</button>` : `<a class="btn sm" href="${esc(edit)}">${i18nText("ui.accounts.edit")}</a>`}</div></td></tr>`;
    }).join("");
  }
  function inviteRows(items) {
    const statusNames = {pending:i18nText("ui.accounts.awaiting_acceptance"),used:i18nText("ui.accounts.accepted"),expired:i18nText("ui.accounts.expired"),revoked:i18nText("ui.accounts.cancelled")};
    return items.map(invite => `<tr><td class="users-person-cell"><div class="users-person">${usersAvatar(invite)}<span><b>${esc(invite.name)}</b><small>${esc(invite.email)}</small></span></div></td>
      <td data-label="${i18nText("ui.accounts.role_and_groups")}"><span class="muted">${esc(ACCOUNT_ROLE[invite.role] || "—")}</span>${usersGroups(invite)}</td>
      <td data-label="${i18nText("ui.accounts.status")}"><span class="users-status${invite.status === "pending" ? " pending" : ""}">${esc(statusNames[invite.status] || invite.status)}</span>${invite.registered_login ? `<small class="muted">${esc(invite.registered_login)}</small>` : ""}</td>
      <td class="users-time" data-label="${i18nText("ui.accounts.sent")}">${esc(usersDate(invite.created_at))}</td>
      <td class="users-actions-cell"><div class="users-row-actions">${invite.status !== "used" ? `<button class="btn sm" type="button" data-users-action="resend" data-id="${invite.id}">${i18nText("ui.accounts.resend")}</button>` : ""}${invite.status === "pending" ? `<button class="btn danger sm" type="button" data-users-action="revoke" data-id="${invite.id}">${i18nText("ui.accounts.cancel")}</button>` : ""}</div></td></tr>`).join("");
  }
  function renderDirectory(data) {
    const invitations = state.query.tab === "invitations";
    const rows = data.items || [];
    const page = Number(data.page) || state.query.page, pages = Math.max(1, Number(data.pages) || 1), total = Number(data.total) || 0;
    find("#users-directory").innerHTML = `${rows.length ? `<div class="users-table-wrap"><table class="users-table"><thead><tr><th>${i18nText("ui.accounts.full_name_and_email")}</th><th>${invitations ? i18nText("ui.accounts.role_and_groups") : i18nText("ui.accounts.groups")}</th><th>${i18nText("ui.accounts.status")}</th><th>${invitations ? i18nText("ui.accounts.sent") : i18nText("ui.accounts.last_sign_in")}</th><th>${i18nText("ui.accounts.action")}</th></tr></thead><tbody>${invitations ? inviteRows(rows) : userRows(rows)}</tbody></table></div>` : `<div class="users-empty">${state.query.q || state.query.group ? i18nText("ui.accounts.no_matching_records") : invitations ? i18nText("ui.accounts.no_invitations_yet") : state.query.tab === "removed" ? i18nText("ui.accounts.no_removed_accounts") : i18nText("ui.accounts.no_users_in_this_category_yet")}</div>`}
      <footer class="users-footer"><span class="muted">${i18nText("ui.accounts.total")} ${total} ${i18nText("ui.accounts.records")}</span><nav class="users-pagination" aria-label="${i18nText("ui.accounts.user_pages")}">${usersPageNumbers(page,pages).map(n=>n===null?'<span class="muted" aria-hidden="true">…</span>':`<a class="chip${n===page?' on':''}" ${n===page?'aria-current="page"':''} href="${esc(usersListHash({...state.query,page:n}))}">${n}</a>`).join('')}</nav></footer>`;
  }
  async function loadDirectory() {
    const request = ++state.request;
    find("#users-directory").setAttribute("aria-busy", "true");
    usersShowMessage(find("#users-list-error"), "");
    try {
      const data = await api("/api/accounts/directory?" + usersParams(state.query));
      if (!alive() || request !== state.request) return;
      state.data = data;
      if (state.query.group_status === "active") state.activeGroups = data.groups || [];
      renderTabs(data.counts || {});
      groupOptions(data.groups || []);
      renderDirectory(data);
    } catch (error) {
      if (alive() && request === state.request && error.message !== "auth") usersShowMessage(find("#users-list-error"), usersError(error));
    } finally {
      if (alive() && request === state.request) find("#users-directory").setAttribute("aria-busy", "false");
    }
  }
  function updateFilters(scopeChanged = false) {
    state.query.q = find("#users-search").value.trim();
    state.query.group = scopeChanged ? "" : find("#users-group").value;
    state.query.group_status = find("#users-group-scope").value;
    state.query.page = 1;
    syncHash();
    loadDirectory();
  }
  async function openInvite() {
    if (state.inviteBusy) return;
    state.inviteBusy = true;
    const button = find("#show-invite");
    button.disabled = true;
    try {
      if (!state.activeGroups) {
        const data = await api("/api/accounts/directory?tab=student&group_status=active&page=1&page_size=1");
        if (!alive()) return;
        state.activeGroups = data.groups || [];
      }
      const selectedRole = ["lecturer","admin"].includes(state.query.tab) ? state.query.tab : "student";
      find("#invite-panel").innerHTML = `<div class="row" style="justify-content:space-between;flex-wrap:wrap"><h2 id="users-invite-title">${i18nText("ui.accounts.invite_user")}</h2><button class="btn sm" type="button" data-users-action="close-invite">${i18nText("ui.accounts.close")}</button></div>
        <form id="invite-form" class="users-edit-form" novalidate><div class="users-grid">${accountField("name",i18nText("ui.accounts.full_name"),"text",(`maxlength="120" placeholder="${i18nText("ui.accounts.full_name")}" autocomplete="name"`))}${accountField("email",i18nText("ui.accounts.email"),"email",'maxlength="254" autocomplete="email"')}</div>
          <label class="fld"><span>${i18nText("ui.accounts.role")}</span><select class="input" name="role">${usersRoleOptions(selectedRole)}</select></label>${usersGroupFields(state.activeGroups, [])}
          <p class="users-help">${i18nText("ui.accounts.choose_one_group_for_a_student_and_2")}</p>
          <div id="users-invite-error" role="alert" hidden></div><div class="users-edit-actions"><button class="btn primary" type="submit">${i18nText("ui.accounts.create_and_send_invitation")}</button></div></form>`;
      find("#invite-panel").hidden = false;
      const form = find("#invite-form");
      form.elements.role.onchange = () => usersSyncGroups(form);
      usersSyncGroups(form);
      form.onsubmit = async event => {
        event.preventDefault();
        const submit = form.querySelector('button[type="submit"]');
        if (submit.disabled) return;
        usersShowMessage(find("#users-invite-error"), "");
        try {
          const values = new FormData(form), name = String(values.get("name") || "").trim(), email = String(values.get("email") || "").trim();
          if (!name) throw new Error(i18nText("ui.accounts.enter_a_full_name"));
          if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error(i18nText("ui.accounts.enter_a_valid_email_address"));
          const payload = {...usersGroupPayload(form),name,email};
          submit.disabled = true;
          const result = await api("/api/accounts/invitations", {method:"POST",body:JSON.stringify(payload)});
          if (!alive()) return;
          find("#invite-panel").hidden = true;
          shareAccountLink(result.link, {...result,kind:"invite"});
          await loadDirectory();
        } catch (error) {
          if (alive() && error.message !== "auth") usersShowMessage(find("#users-invite-error"), usersError(error));
        } finally { if (submit.isConnected) submit.disabled = false; }
      };
      find('#invite-form [name="name"]').focus();
      find("#invite-panel").scrollIntoView({block:"start"});
    } catch (error) {
      if (alive() && error.message !== "auth") usersShowMessage(find("#users-list-error"), usersError(error));
    } finally { state.inviteBusy = false; if (button.isConnected) button.disabled = false; }
  }


  find("#users-filter-form").onsubmit = event => {event.preventDefault();clearTimeout(searchTimer);updateFilters();};
  find("#users-search").oninput = () => {clearTimeout(searchTimer);searchTimer=setTimeout(()=>{if(alive())updateFilters();},350);};
  find("#users-group").onchange = () => {clearTimeout(searchTimer);updateFilters();};
  find("#users-group-scope").onchange = () => {clearTimeout(searchTimer);updateFilters(true);};
  find("#show-invite").onclick = openInvite;
  find("#users-legacy-load").onclick = async event => {
    const button = event.currentTarget;button.disabled=true;
    try {
      const data = await api("/api/accounts/legacy");if(!alive())return;
      find("#users-legacy-panel").innerHTML=`<label class="users-toggle" style="margin-top:14px"><input type="checkbox" id="legacy-access"${data.enabled?' checked':''}><span>${i18nText("ui.accounts.allow_legacy_shared_passwords")}<span class="users-help" style="display:block">${i18nText("ui.accounts.disabling_this_ends_shared_sessions_personal_accounts")}</span></span></label><p id="users-legacy-message" role="status" hidden></p>`;
      find("#legacy-access").onchange=async change=>{
        const input=change.target;input.disabled=true;
        try{await api('/api/accounts/legacy',{method:'POST',body:JSON.stringify({enabled:input.checked})});if(alive())usersShowMessage(find('#users-legacy-message'),i18nText("ui.accounts.shared_sign_in_setting_saved"),false);}
        catch(error){if(alive()){input.checked=!input.checked;usersShowMessage(find('#users-legacy-message'),usersError(error));}}
        finally{if(input.isConnected)input.disabled=false;}
      };
    }catch(error){if(alive())usersShowMessage(find('#users-list-error'),usersError(error));}
    finally{if(button.isConnected)button.disabled=false;}
  };
  view.onkeydown=event=>{const row=event.target.closest('[data-users-edit]');if(row && event.target===row && ['Enter',' '].includes(event.key)){event.preventDefault();location.hash=row.dataset.usersEdit;}};
  view.onclick = async event => {
    const row=event.target.closest('[data-users-edit]');
    if(row&&!event.target.closest('a,button,input,select')){location.hash=row.dataset.usersEdit;return;}
    const button = event.target.closest("[data-users-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.usersAction;
    if (action === "close-invite") {find("#invite-panel").hidden=true;return;}

    const id = button.dataset.id;
    if (action === "delete" && !confirm(`${i18nText("ui.accounts.permanently_delete")}${button.dataset.name}${i18nText("ui.accounts.this_cannot_be_undone_past_records_will")}`)) return;
    if (action === "restore" && !confirm(`${i18nText("ui.accounts.restore_access_for")}${button.dataset.name}${i18nText("ui.accounts.punctuation_2")}`)) return;
    if (action === "revoke" && !confirm(i18nText("ui.accounts.cancel_this_invitation_the_old_link_will"))) return;
    if (!["delete","restore","resend","revoke"].includes(action)) return;
    button.disabled = true;
    try {
      const path = ["resend","revoke"].includes(action) ? `/api/accounts/invitations/${id}/${action}` : `/api/accounts/users/${id}/${action}`;
      const result = await api(path, {method:"POST"});
      if (!alive()) return;
      if (action === "resend") shareAccountLink(result.link, {...result,kind:"invite"});
      else toast(action === "restore" ? i18nText("ui.accounts.user_restored") : action === "delete" ? i18nText("ui.accounts.the_user_s_personal_information_has_been") : i18nText("ui.accounts.invitation_cancelled"));
      await loadDirectory();
    } catch (error) {
      if (alive() && error.message !== "auth") usersShowMessage(find("#users-list-error"), usersError(error));
    } finally { if (button.isConnected) button.disabled = false; }
  };
  await loadDirectory();
}

async function viewUser(id) {
  const existing = $("#account-user-edit");
  if (existing && existing.dataset.userId === String(id)) return;
  if (S.role !== "admin" || !S.user) return;
  accountsStyles();
  const query = usersQuery(), back = usersListHash(query);
  $("#main").innerHTML = `<div class="accounts-page users-editor" id="account-user-edit" data-user-id="${id}"><a class="back" href="${esc(back)}">${ic("back")} ${i18nText("ui.accounts.back_to_users")}</a><div id="user-edit-loading" class="card muted" role="status">${i18nText("ui.accounts.loading_account")}</div><div id="user-edit-error" role="alert" hidden></div><div id="user-edit-content" hidden></div><section class="card" id="account-link" aria-live="polite" hidden></section></div>`;
  const view = $("#account-user-edit"), find = selector => $(selector, view);
  const alive = () => $("#account-user-edit") === view && S.role === "admin";
  let user, availableGroups = [], completed = new Set(), busy = false;
  function message(text, error=true) {usersShowMessage(find("#user-edit-error"),text,error);}
  function groupList(data) {
    completed=new Set(data.groups.filter(group=>group.completed).map(group=>group.name));
    availableGroups=[...new Set([...data.groups.filter(group=>!group.completed).map(group=>group.name),...(user.groups||[])])];
  }
  function content() {
    const removed=Boolean(user.removed||user.removed_at);
    find('#user-edit-content').innerHTML=`<section class="card"><div class="users-editor-header">${usersAvatar(user)}<div><h1>${esc(user.name)}</h1><p>${esc(user.email)}</p><p class="users-help">${i18nText("ui.accounts.username_2")} ${esc(user.login)} ${i18nText("ui.accounts.last_sign_in_2")} ${esc(usersDate(user.last_login_at))}</p></div></div></section>
      ${removed ? `<section class="card"><h2>${i18nText("ui.accounts.account_removed")}</h2><p class="muted">${i18nText("ui.accounts.sign_in_is_blocked_restore_the_account")}</p><div class="users-edit-actions"><button class="btn primary" type="button" data-user-edit-action="restore">${i18nText("ui.accounts.restore_account")}</button><button class="btn danger" type="button" data-user-edit-action="delete">${i18nText("ui.accounts.delete_permanently")}</button></div></section>` : `<section class="card"><h2>${i18nText("ui.accounts.access_settings")}</h2><form id="user-edit-form" class="users-edit-form" novalidate>
        <label class="fld"><span>${i18nText("ui.accounts.role")}</span><select class="input" name="role">${usersRoleOptions(user.role)}</select></label>${usersGroupFields(availableGroups,user.groups||[],completed)}
        <label class="users-toggle"><input type="checkbox" name="active"${user.active?' checked':''}><span><b>${i18nText("ui.accounts.account_is_active")}</b><span class="users-help" style="display:block">${i18nText("ui.accounts.disabled_accounts_cannot_sign_in_their_data")}</span></span></label>
        <div class="users-edit-actions"><button class="btn primary" type="submit">${i18nText("ui.accounts.save_changes")}</button><a class="btn" href="${esc(back)}">${i18nText("ui.accounts.cancel")}</a></div></form></section>
        <section class="card"><h2>${i18nText("ui.accounts.password_reset")}</h2><p class="users-help">${i18nText("ui.accounts.create_a_single_use_reset_link_to")}</p><button class="btn" type="button" data-user-edit-action="reset" style="margin-top:12px">${i18nText("ui.accounts.create_reset_link")}</button></section>
        <section class="card users-danger"><h2>${i18nText("ui.accounts.remove_user")}</h2><p class="users-help">${i18nText("ui.accounts.sign_in_will_be_blocked_history_and")}</p><button class="btn danger" type="button" data-user-edit-action="remove" style="margin-top:12px">${i18nText("ui.accounts.remove_user")}</button></section>`}`;
    find('#user-edit-content').hidden=false;
    const form=find('#user-edit-form');
    if(form){
      form.elements.role.onchange=()=>usersSyncGroups(form);usersSyncGroups(form);
      form.onsubmit=async event=>{
        event.preventDefault();if(busy)return;
        message('');
        try{
          const payload={...usersGroupPayload(form),active:form.elements.active.checked};
          busy=true;lock();
          await api('/api/accounts/users/'+id,{method:'POST',body:JSON.stringify(payload)});
          if(!alive())return;
          user=await api('/api/accounts/users/'+id);
          if(!alive())return;
          content();message(i18nText("ui.accounts.access_settings_saved"),false);
        }catch(error){if(alive()&&error.message!=='auth')message(usersError(error));}
        finally{busy=false;if(alive())lock();}
      };
    }
    lock();
  }
  function lock(){
    view.querySelectorAll('#user-edit-form input,#user-edit-form select,#user-edit-form button,[data-user-edit-action]').forEach(node=>{node.disabled=busy;});
    const form=find('#user-edit-form');if(form&&!busy)usersSyncGroups(form);
  }
  async function load(){
    try{
      const result=await Promise.all([api('/api/accounts/users/'+id),api('/api/admin/group-status')]);
      if(!alive())return;
      user=result[0];groupList(result[1]);find('#user-edit-loading').hidden=true;content();
    }catch(error){if(alive()&&error.message!=='auth'){find('#user-edit-loading').hidden=true;message(usersError(error));}}
  }
  view.onclick=async event=>{
    const button=event.target.closest('[data-user-edit-action]');if(!button||button.disabled||busy)return;
    const action=button.dataset.userEditAction;
    if(action==='remove'&&!confirm(`${i18nText("ui.accounts.remove")}${user.name}${i18nText("ui.accounts.sign_in_will_be_blocked_and_data")}`))return;
    if(action==='delete'&&!confirm(i18nText("accounts.delete_personal_data", {name:user.name})))return;
    if(action==='restore'&&!confirm(`${i18nText("ui.accounts.restore_access_for")}${user.name}${i18nText("ui.accounts.punctuation_2")}`))return;
    busy=true;lock();message('');
    try{
      const result=await api(`/api/accounts/users/${id}/${action}`,{method:'POST'});if(!alive())return;
      if(action==='reset'){shareAccountLink(result.link,{...result,kind:'reset'});}
      else if(action==='delete'){toast(i18nText("ui.accounts.the_user_s_personal_information_has_been"));location.hash=back;}
      else{user=await api('/api/accounts/users/'+id);if(!alive())return;content();toast(action==='remove'?i18nText("ui.accounts.user_removed"):i18nText("ui.accounts.user_restored"));}
    }catch(error){if(alive()&&error.message!=='auth')message(usersError(error));}
    finally{busy=false;if(alive())lock();}
  };
  await load();
}


async function viewPrivacy(){
  const p=await api('/api/privacy/policy');
  accountPublic(i18nText("ui.accounts.privacy_policy_3"),`<p class="muted">${i18nText("ui.accounts.version")} ${esc(p.version)}</p>${p.paragraphs.map(x=>`<p>${esc(x)}</p>`).join('')}`);
  if(typeof renderLecturerReferences==='function')await renderLecturerReferences();
}
async function viewDataRequest(){
  const rows=await api('/api/privacy/requests');
  $('#main').innerHTML=`<h1>${i18nText("ui.accounts.data_requests")}</h1><p>${i18nText("ui.accounts.describe_the_data_you_want_deleted_an")}</p><form id="data-request"><label class="fld">${i18nText("ui.accounts.request")}<textarea class="input" name="message" minlength="5" maxlength="2000" required></textarea></label><button class="btn primary">${i18nText("ui.accounts.send_request")}</button></form><p id="data-status" role="status"></p><section class="card data-request-list">${rows.map(r=>`<p>#${r.id} ${S.role==='admin'?`· <a href="#/users/${r.user_id}">${i18nText("ui.accounts.account")}${r.user_id}</a>`:''} · ${esc(r.message)} · ${r.status==='open'?i18nText("ui.accounts.pending"):i18nText("ui.accounts.closed")} ${S.role==='admin'&&r.status==='open'?`<button class="btn" data-resolve="${r.id}">${i18nText("ui.accounts.mark_reviewed")}</button>`:''}</p>`).join('')}</section>`;
  $('#data-request').onsubmit=async e=>{e.preventDefault();try{await api('/api/privacy/requests',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});viewDataRequest();}catch(err){$('#data-status').textContent=err.message;}};
  document.querySelectorAll('[data-resolve]').forEach(b=>b.onclick=async()=>{await api(`/api/privacy/requests/${b.dataset.resolve}/resolve`,{method:'POST'});viewDataRequest();});
}


function viewSecondFactor(cooldown=60){
  S.role=null;S.user=null;
  accountPublic(i18nText("ui.accounts.confirm_sign_in"),`<p>${i18nText("ui.accounts.a_6_digit_code_was_sent_to")}</p><form id="account-form">${accountField('code',i18nText("ui.accounts.verification_code"),'text','inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" minlength="6" maxlength="6"')}<label class="fld"><span><input type="checkbox" name="remember"> ${i18nText("ui.accounts.remember_this_device_for_30_days")}</span></label><button class="btn primary" type="submit">${i18nText("ui.accounts.confirm")}</button></form><button class="btn" id="otp-resend" disabled>${i18nText("ui.accounts.resend_in_60_s")}</button><p id="otp-message" role="status"></p>`);
  let remaining=cooldown;const b=$('#otp-resend');const tick=setInterval(()=>{if(!b.isConnected){clearInterval(tick);return;}remaining--;b.textContent=remaining>0?`${i18nText("ui.accounts.resend_in")} ${remaining} ${i18nText("ui.accounts.s")}`:i18nText("ui.accounts.resend_2");b.disabled=remaining>0;},1000);
  bindAccountForm(async data=>signedIn(await api('/api/admin-auth/verify',{method:'POST',body:JSON.stringify({code:data.code,remember:data.remember==='on'}),login:true})));
  b.onclick=async()=>{b.disabled=true;try{await api('/api/admin-auth/resend',{method:'POST',body:'{}',login:true});remaining=60;$('#otp-message').textContent=i18nText("ui.accounts.new_code_sent");}catch(e){$('#otp-message').textContent=e.message;b.disabled=false;}};
}


function usersPageNumbers(page,pages){
  const list=[...new Set([1,pages,page-1,page,page+1].filter(n=>n>=1&&n<=pages))].sort((a,b)=>a-b),result=[];
  for(const n of list){if(result.length&&n-result[result.length-1]>1)result.push(null);result.push(n);}return result;
}
