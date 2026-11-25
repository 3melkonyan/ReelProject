/* General settings only; account and course management belong to their own pages. */
async function viewSettings() {
  if ($("#settings-view")) {
    const theme=$("#settings-theme"); if (theme) theme.value=isDark()?"dark":"light";
    return;
  }
  if (S.role!=="admin") return set("#main",'<div class="alert err">Կարգավորումները հասանելի են միայն ադմինիստրատորին։</div>');
  const heading=typeof pageHeading==="function" ? pageHeading("Կարգավորումներ","Հարթակի ընդհանուր կարգավորումները՝ մեկ վայրում։") : '<div class="top"><div><h1>Կարգավորումներ</h1><p>Հարթակի ընդհանուր կարգավորումները՝ մեկ վայրում։</p></div></div>';
  set("#main",`<div id="settings-view"><style>#settings-view{display:grid;gap:24px}#settings-view [hidden]{display:none!important}#settings-view fieldset{border:0;margin:0;padding:0;min-width:0;display:grid;gap:16px}#settings-view .settings-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}#settings-view .settings-help{font-size:13px;line-height:1.65;color:var(--muted)}#settings-view details form{margin-top:24px}@media(max-width:640px){#settings-view .settings-fields{grid-template-columns:1fr}}</style>${heading}<div id="settings-loading" class="card muted">${typeof pageSkeleton==="function"?pageSkeleton("Բեռնում ենք կարգավորումները"):"Տվյալները բեռնվում են…"}</div><div id="settings-error" role="alert" hidden></div><button class="btn" id="settings-retry" type="button" hidden>Կրկին բեռնել</button><div id="settings-content" class="settings-grid" hidden></div></div>`);
  const view=$("#settings-view"), identity=S.user?.id, find=selector=>$(selector,view);
  const alive=()=>$("#settings-view")===view && S.role==="admin" && S.user?.id===identity;
  let busy=false;
  function message(text,success=false) {
    if (!alive()) return;
    const box=find("#settings-error"); box.textContent=text||"";box.hidden=!text;box.className=`alert ${success?"ok":"err"}`;
  }
  const errorText=error=>error.net ? "Կապը հարթակի հետ չհաջողվեց։ Խնդրում ենք կրկին փորձել։" : /[Ա-Ֆա-ֆ]/.test(String(error.message)) ? String(error.message) : "Պահպանել չհաջողվեց։ Խնդրում ենք ստուգել նշված արժեքները և կրկին փորձել։";
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
      <section class="card settings-section"><h2>Էլ․ նամակներ</h2><div class="settings-status"><span class="pill">${data.email.configured?"Առաքումը կարգավորված է":"Առաքումը կարգավորված չէ"}</span></div><p class="muted">Հրավերների, գաղտնաբառի վերականգնման և դասերի մասին նամակների ընդհանուր առաքում։</p>${data.email.configured?"":'<p class="muted">Միացման համար խնդրում ենք դիմել հարթակի պատասխանատուին։</p>'}</section>
      <section class="card settings-section"><h2>Հանրային հասցե</h2><p class="muted">Այս հասցեով կարող եք բացել հարթակը և փոխանցել հրավերները։</p>${data.public_url?`<label class="fld"><span>Հարթակի հղումը</span><input class="input" id="settings-public-url" value="${esc(data.public_url)}" readonly></label><button class="btn" id="settings-copy-url" type="button">Պատճենել հղումը</button>`:'<p class="settings-status">Հանրային հասցե նշված չէ։</p>'}</section>
      <section class="card settings-section"><h2>Ընդհանուր մուտք</h2><p class="muted">Միացնելու դեպքում հնարավոր է օգտվել պատասխանատուի տրամադրած ընդհանուր մուտքից։ Անձնական հաշիվները շարունակում են գործել։</p><form id="settings-legacy-form"><fieldset id="settings-legacy-fields"><label class="users-toggle"><input type="checkbox" id="settings-legacy"${data.legacy_enabled?" checked":""}><span>Թույլատրել ընդհանուր մուտքը</span></label><button class="btn" type="submit">Պահպանել մուտքի կարգավորումը</button></fieldset></form></section>
      <section class="card settings-section"><h2>Տեսք</h2><p class="muted">Ընտրությունը պահպանվում է այս դիտարկչում։</p><label class="fld"><span>Էջերի գունային տեսքը</span><select class="input" id="settings-theme"><option value="dark"${isDark()?" selected":""}>Մուգ</option><option value="light"${!isDark()?" selected":""}>Բաց</option></select></label></section>
      <section class="card settings-section settings-section-wide"><h2>Լռելյայն շապիկ</h2><p class="muted">Ընտրեք հետագա դասերի շապիկների հիմնական ձևավորումը։ Արդեն ընտրված ձևավորումները պահպանվում են։</p><form id="settings-style-form"><fieldset id="settings-style-fields"><div class="settings-style-grid">${data.thumbnail_styles.map(style=>`<button class="btn settings-style" type="button" data-default-style="${style.id}" aria-pressed="${style.id===selected}" aria-label="Ընտրել ձևավորում ${style.id+1}"><img src="${esc(style.preview)}" loading="eager" decoding="async" alt=""><span>Ձևավորում ${style.id+1}</span></button>`).join("")}</div><button class="btn primary" type="submit">Պահպանել շապիկի ձևավորումը</button></fieldset></form></section>
      ${advanced?`<details class="card settings-section settings-section-wide"><summary>Լրացուցիչ կարգավորումներ</summary><form id="admin-general-form" novalidate><fieldset id="admin-general-fields" class="settings-form"><label class="fld"><span>Ծանուցումների ալիքը</span><input class="input" name="operator_slack_channel" value="${esc(advanced.operator_slack_channel)}" autocomplete="off" required><span class="settings-help">Ավարտված կամ ընդհատված մշակումների մասին հաղորդագրությունների հասցեատերը։</span></label><div class="settings-fields"><label class="fld"><span>Երկար լռության շեմը՝ վայրկյաններով</span><input class="input" type="number" name="min_silence_sec" min="0" step="any" value="${esc(advanced.cut.min_silence_sec)}" required></label><label class="fld"><span>Կտրվող մասի առավելագույն հարաբերակցությունը</span><input class="input" type="number" name="max_cut_ratio" min="0" max="1" step="any" value="${esc(advanced.cut.max_cut_ratio)}" required><span class="settings-help">Օրինակ՝ 0.4 = 40%։</span></label></div><h3>Ժամանակի խնայողության գնահատական</h3><p class="settings-help">Այս արժեքներն օգտագործվում են խնայված ժամանակը գնահատելու համար։</p><div class="settings-fields"><label class="fld"><span>Ձեռքով խմբագրում՝ րոպե / դասի ժամ</span><input class="input" type="number" name="editing_min_per_hour" min="0" max="240" step="any" value="${esc(advanced.time_savings.editing_min_per_hour)}" required></label><label class="fld"><span>Ձեռքով հրապարակում՝ րոպե / դաս</span><input class="input" type="number" name="publishing_min_per_lesson" min="0" max="120" step="any" value="${esc(advanced.time_savings.publishing_min_per_lesson)}" required></label></div><button class="btn" type="submit">Պահպանել լրացուցիչ կարգավորումները</button></fieldset></form></details>`:""}`;
    find("#settings-content").hidden=false;
    find("#settings-theme").onchange=event=>{if (event.target.value!==(isDark()?"dark":"light")) toggleTheme();};
    if (data.public_url) find("#settings-copy-url").onclick=async()=>{
      try {await navigator.clipboard.writeText(data.public_url);message("Հղումը պատճենված է։",true);}
      catch (_) {message("Խնդրում ենք ընտրել հղումը և պատճենել այն։");}
    };
    find("#settings-legacy-form").onsubmit=event=>{
      event.preventDefault();const enabled=find("#settings-legacy").checked;
      save(find("#settings-legacy-fields"),()=>api("/api/accounts/legacy",{method:"POST",body:JSON.stringify({enabled})}),"Մուտքի կարգավորումը պահպանված է։");
    };
    find("#settings-style-fields").onclick=event=>{
      const button=event.target.closest("[data-default-style]");if (!button || busy) return;
      selected=Number(button.dataset.defaultStyle);
      find("#settings-style-fields").querySelectorAll("[data-default-style]").forEach(item=>item.setAttribute("aria-pressed",String(Number(item.dataset.defaultStyle)===selected)));
    };
    find("#settings-style-form").onsubmit=event=>{
      event.preventDefault();save(find("#settings-style-fields"),()=>api("/api/admin/system-settings",{method:"PUT",body:JSON.stringify({default_thumbnail_style:selected})}),"Շապիկի լռելյայն ձևավորումը պահպանված է։");
    };
    if (advanced) find("#admin-general-form").onsubmit=event=>{
      event.preventDefault();const values=new FormData(event.target), raw=name=>String(values.get(name)||"").trim();
      const silence=Number(raw("min_silence_sec")),ratio=Number(raw("max_cut_ratio")),editing=Number(raw("editing_min_per_hour")),publishing=Number(raw("publishing_min_per_lesson"));
      if (!raw("operator_slack_channel")) return message("Խնդրում ենք նշել ծանուցումների ալիքը։");
      if (!raw("min_silence_sec") || !raw("max_cut_ratio") || !Number.isFinite(silence) || silence<=0 || !Number.isFinite(ratio) || ratio<=0 || ratio>=1) return message("Լռության շեմը պետք է լինի 0-ից մեծ, իսկ կտրման հարաբերակցությունը՝ 0-ի և 1-ի միջև։");
      if (!raw("editing_min_per_hour") || !raw("publishing_min_per_lesson") || !Number.isFinite(editing) || editing<0 || editing>240 || !Number.isFinite(publishing) || publishing<0 || publishing>120) return message("Խմբագրման գնահատականը նշեք 0–240, հրապարակմանը՝ 0–120 րոպե միջակայքում։");
      save(find("#admin-general-fields"),()=>api("/api/admin/settings",{method:"PUT",body:JSON.stringify({operator_slack_channel:raw("operator_slack_channel"),cut:{min_silence_sec:silence,max_cut_ratio:ratio},time_savings:{editing_min_per_hour:editing,publishing_min_per_lesson:publishing}})}),"Լրացուցիչ կարգավորումները պահպանված են։ Կկիրառվեն մինչև 30 վայրկյանում։");
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
