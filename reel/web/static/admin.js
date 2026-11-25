/* General settings only; account and course management belong to their own pages. */
async function viewSettings() {
  if ($("#settings-view")) {
    const theme=$("#settings-theme"); if (theme) theme.value=isDark()?"dark":"light";
    return;
  }
  if (S.role!=="admin") return set("#main",(`<div class="alert err">${i18nText("system_settings.only_administrators_can_access_settings")}</div>`));
  const heading=typeof pageHeading==="function" ? pageHeading(i18nText("ui.admin.settings"),i18nText("ui.admin.manage_platform_settings_in_one_place")) : (`<div class="top"><div><h1>${i18nText("ui.admin.settings")}</h1><p>${i18nText("ui.admin.manage_platform_settings_in_one_place")}</p></div></div>`);
  set("#main",`<div id="settings-view"><style>#settings-view{display:grid;gap:24px}#settings-view [hidden]{display:none!important}#settings-view fieldset{border:0;margin:0;padding:0;min-width:0;display:grid;gap:16px}#settings-view .settings-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}#settings-view .settings-help{font-size:13px;line-height:1.65;color:var(--muted)}#settings-view details form{margin-top:24px}@media(max-width:640px){#settings-view .settings-fields{grid-template-columns:1fr}}</style>${heading}<div id="settings-loading" class="card muted">${typeof pageSkeleton==="function"?pageSkeleton(i18nText("ui.admin.loading_settings")):i18nText("ui.accounts.loading")}</div><div id="settings-error" role="alert" hidden></div><button class="btn" id="settings-retry" type="button" hidden>${i18nText("ui.admin.reload")}</button><div id="settings-content" class="settings-grid" hidden></div></div>`);
  const view=$("#settings-view"), identity=S.user?.id, find=selector=>$(selector,view);
  const alive=()=>$("#settings-view")===view && S.role==="admin" && S.user?.id===identity;
  let busy=false;
  function message(text,success=false) {
    if (!alive()) return;
    const box=find("#settings-error"); box.textContent=text||"";box.hidden=!text;box.className=`alert ${success?"ok":"err"}`;
  }
  const errorText=error=>error.net ? i18nText("ui.admin.could_not_connect_to_reel_try_again") : error.status >= 400 && error.status < 500 ? String(error.message) : i18nText("ui.admin.could_not_save_check_the_values_and");
  async function save(fields,action,success) {
    if (busy || !alive()) return;
    busy=true;fields.disabled=true;message("");
    try {await action();if (alive()) message(success,true);}
    catch(error) {if (alive() && error.message!=="auth") message(errorText(error));}
    finally {busy=false;if (alive()) fields.disabled=false;}
  }
  function render(data) {
    let selected=data.default_thumbnail_style;
    const advanced=data.advanced;
    find("#settings-content").innerHTML=`
      <section class="card settings-section"><h2>${i18nText("ui.admin.email")}</h2><div class="settings-status"><span class="pill">${data.email.configured?i18nText("ui.admin.delivery_configured"):i18nText("ui.admin.delivery_not_configured")}</span></div><p class="muted">${i18nText("ui.admin.delivery_of_invitations_password_resets_and_lesson")}</p>${data.email.configured?"":(`<p class="muted">${i18nText("ui.admin.contact_your_administrator_to_enable_delivery")}</p>`)}</section>
      <section class="card settings-section"><h2>${i18nText("ui.admin.public_url")}</h2><p class="muted">${i18nText("ui.admin.use_this_url_to_open_reel_and")}</p>${data.public_url?`<label class="fld"><span>${i18nText("ui.admin.platform_link")}</span><input class="input" id="settings-public-url" value="${esc(data.public_url)}" readonly></label><button class="btn" id="settings-copy-url" type="button">${i18nText("ui.accounts.copy_link")}</button>`:(`<p class="settings-status">${i18nText("ui.admin.no_public_url_configured")}</p>`)}</section>
      <section class="card settings-section"><h2>${i18nText("ui.admin.shared_sign_in")}</h2><p class="muted">${i18nText("ui.admin.enable_shared_sign_in_using_credentials_from")}</p><form id="settings-legacy-form"><fieldset id="settings-legacy-fields"><label class="users-toggle"><input type="checkbox" id="settings-legacy"${data.legacy_enabled?" checked":""}><span>${i18nText("ui.admin.allow_shared_sign_in")}</span></label><button class="btn" type="submit">${i18nText("ui.admin.save_sign_in_settings")}</button></fieldset></form></section>
      <section class="card settings-section"><h2>${i18nText("ui.admin.appearance")}</h2><p class="muted">${i18nText("ui.admin.your_choice_is_saved_in_this_browser")}</p><label class="fld"><span>${i18nText("ui.admin.color_theme")}</span><select class="input" id="settings-theme"><option value="dark"${isDark()?" selected":""}>${i18nText("ui.admin.dark")}</option><option value="light"${!isDark()?" selected":""}>${i18nText("ui.admin.light")}</option></select></label></section>
      <section class="card settings-section settings-section-wide"><h2>${i18nText("ui.admin.default_thumbnail")}</h2><p class="muted">${i18nText("ui.admin.choose_the_default_design_for_future_lessons")}</p><form id="settings-style-form"><fieldset id="settings-style-fields"><div class="settings-style-grid">${data.thumbnail_styles.map(style=>`<button class="btn settings-style" type="button" data-default-style="${style.id}" aria-pressed="${style.id===selected}" aria-label="${i18nText("ui.admin.choose_design")} ${style.id+1}"><img src="${esc(style.preview)}" loading="eager" decoding="async" alt=""><span>${i18nText("ui.admin.design")} ${style.id+1}</span></button>`).join("")}</div><button class="btn primary" type="submit">${i18nText("ui.admin.save_thumbnail_design")}</button></fieldset></form></section>
      ${advanced?`<details class="card settings-section settings-section-wide"><summary>${i18nText("ui.admin.advanced_settings")}</summary><form id="admin-general-form" novalidate><fieldset id="admin-general-fields" class="settings-form"><label class="fld"><span>${i18nText("ui.admin.notification_channel")}</span><input class="input" name="operator_slack_channel" value="${esc(advanced.operator_slack_channel)}" autocomplete="off" required><span class="settings-help">${i18nText("ui.admin.the_destination_for_completed_or_interrupted_processing")}</span></label><div class="settings-fields"><label class="fld"><span>${i18nText("ui.admin.long_silence_threshold_in_seconds")}</span><input class="input" type="number" name="min_silence_sec" min="0" step="any" value="${esc(advanced.cut.min_silence_sec)}" required></label><label class="fld"><span>${i18nText("ui.admin.maximum_cut_ratio")}</span><input class="input" type="number" name="max_cut_ratio" min="0" max="1" step="any" value="${esc(advanced.cut.max_cut_ratio)}" required><span class="settings-help">${i18nText("ui.admin.for_example_0_4_40")}</span></label></div><h3>${i18nText("ui.admin.estimated_time_savings")}</h3><p class="settings-help">${i18nText("ui.admin.these_values_are_used_to_estimate_time")}</p><div class="settings-fields"><label class="fld"><span>${i18nText("ui.admin.manual_editing_minutes_per_lesson_hour")}</span><input class="input" type="number" name="editing_min_per_hour" min="0" max="240" step="any" value="${esc(advanced.time_savings.editing_min_per_hour)}" required></label><label class="fld"><span>${i18nText("ui.admin.manual_publishing_minutes_per_lesson")}</span><input class="input" type="number" name="publishing_min_per_lesson" min="0" max="120" step="any" value="${esc(advanced.time_savings.publishing_min_per_lesson)}" required></label></div><button class="btn" type="submit">${i18nText("ui.admin.save_advanced_settings")}</button></fieldset></form></details>`:""}`;
    find("#settings-content").hidden=false;
    find("#settings-theme").onchange=event=>{if (event.target.value!==(isDark()?"dark":"light")) toggleTheme();};
    if (data.public_url) find("#settings-copy-url").onclick=async()=>{
      try {await navigator.clipboard.writeText(data.public_url);message(i18nText("ui.accounts.link_copied"),true);}
      catch (_) {message(i18nText("ui.admin.select_and_copy_the_link"));}
    };
    find("#settings-legacy-form").onsubmit=event=>{
      event.preventDefault();const enabled=find("#settings-legacy").checked;
      save(find("#settings-legacy-fields"),()=>api("/api/accounts/legacy",{method:"POST",body:JSON.stringify({enabled})}),i18nText("ui.admin.sign_in_settings_saved"));
    };
    find("#settings-style-fields").onclick=event=>{
      const button=event.target.closest("[data-default-style]");if (!button || busy) return;
      selected=Number(button.dataset.defaultStyle);
      find("#settings-style-fields").querySelectorAll("[data-default-style]").forEach(item=>item.setAttribute("aria-pressed",String(Number(item.dataset.defaultStyle)===selected)));
    };
    find("#settings-style-form").onsubmit=event=>{
      event.preventDefault();save(find("#settings-style-fields"),()=>api("/api/admin/system-settings",{method:"PUT",body:JSON.stringify({default_thumbnail_style:selected})}),i18nText("ui.admin.default_thumbnail_design_saved"));
    };
    if (advanced) find("#admin-general-form").onsubmit=event=>{
      event.preventDefault();const values=new FormData(event.target), raw=name=>String(values.get(name)||"").trim();
      const silence=Number(raw("min_silence_sec")),ratio=Number(raw("max_cut_ratio")),editing=Number(raw("editing_min_per_hour")),publishing=Number(raw("publishing_min_per_lesson"));
      if (!raw("operator_slack_channel")) return message(i18nText("ui.admin.enter_a_notification_channel"));
      if (!raw("min_silence_sec") || !raw("max_cut_ratio") || !Number.isFinite(silence) || silence<=0 || !Number.isFinite(ratio) || ratio<=0 || ratio>=1) return message(i18nText("ui.admin.the_silence_threshold_must_be_greater_than"));
      if (!raw("editing_min_per_hour") || !raw("publishing_min_per_lesson") || !Number.isFinite(editing) || editing<0 || editing>240 || !Number.isFinite(publishing) || publishing<0 || publishing>120) return message(i18nText("ui.admin.enter_0_240_minutes_for_editing_and"));
      save(find("#admin-general-fields"),()=>api("/api/admin/settings",{method:"PUT",body:JSON.stringify({operator_slack_channel:raw("operator_slack_channel"),cut:{min_silence_sec:silence,max_cut_ratio:ratio},time_savings:{editing_min_per_hour:editing,publishing_min_per_lesson:publishing}})}),i18nText("ui.admin.advanced_settings_saved_changes_apply_within_30"));
    };
  }
  async function load() {
    find("#settings-loading").hidden=false;find("#settings-retry").hidden=true;message("");
    try {const data=await api("/api/admin/system-settings");if (alive()) render(data);}
    catch(error) {if (alive() && error.message!=="auth") {message(errorText(error));find("#settings-retry").hidden=false;}}
    finally {if (alive()) find("#settings-loading").hidden=true;}
  }
  find("#settings-retry").onclick=load;
  await load();
}
