"use strict";
/* presentation only. Source media is never requested. */
function etaText(j) {
  if (j.status === "done") return "Ավարտված է";
  if (j.status === "stopped") return "Կանգնած է";
  const seconds = j.progress?.eta_seconds;
  if (seconds == null) return "հաշվարկվում է…";
  const minutes = seconds > 900 ? Math.max(5, Math.round(seconds / 300) * 5) : Math.max(1, Math.round(seconds / 60));
  const finish = new Date(Date.now() + minutes * 60000).toLocaleTimeString("hy-AM", {hour:"2-digit",minute:"2-digit"});
  return `≈ ${minutes} րոպե (~${finish})`;
}
const CUT_LABELS = {before:"Դասից առաջ",break:"Ընդմիջում",silence:"Լռություն",after:"Դասից հետո",other:"Այլ"};
function cutReason(why) {
  const names = {before_class:"Դասից առաջ",after_class:"Դասից հետո",break:"Ընդմիջում",offtopic:"Դասին չառնչվող երկար զրույց"};
  return String(why || "Այլ").split("+").map(part=>names[part] || (part.startsWith("silence") ? "Երկար լռություն" : part)).join(" · ");
}
async function loadCuts(id) {
  const target = $("#cut-insight"); if (!target || S.role === "uploader") return;
  try {
    const d = await api(`/api/jobs/${id}/cuts`); if (!target.isConnected) return;
    if (!d.available) {target.textContent = "Այս դասի կտրման տվյալները պահպանված չեն։"; return;}
    const cuts = d.cuts || [], total = d.original_sec || 1;
    target.innerHTML = `<div class="kv"><div><b>${fmtDur(d.original_sec)}</b><small>Բնօրինակ</small></div><div><b>${fmtDur(d.final_sec)}</b><small>Վերջնական</small></div><div><b>${d.cut_seconds == null ? "—" : (d.cut_seconds / 60).toFixed(1) + " ր"}</b><small>Կտրված · ${d.cut_ratio == null ? "—" : (d.cut_ratio * 100).toFixed(1) + "%"}</small></div><div><b>${d.details_available === false ? "Տվյալ չկա" : cuts.length}</b><small>Կտրում${d.parts?.parts > 1 ? ` · ${d.parts.parts} մասից` : ""}</small></div></div>
      <p class="muted">Բնօրինակի ժամագիծ · սեղմեք կտրված հատվածը։ Նախադիտումը՝ միայն վերջնական մշուշված տեսանյութից։</p>
      <div class="cut-track" role="group" aria-label="Բնօրինակի պահված և կտրված հատվածները">${cuts.map((c,i)=>`<span class="cut-marker ${c.kind}" style="left:${c.start/total*100}%;width:${c.duration/total*100}%" aria-hidden="true"></span><button class="cut-hit" data-cut-jump="${i}" style="left:clamp(0px,calc(${(c.start+c.end)/2/total*100}% - 22px),calc(100% - 44px))" aria-label="${esc(CUT_LABELS[c.kind] || "Այլ")}՝ ${clock(c.start)}–${clock(c.end)}"></button>`).join("")}</div>
      <div class="cut-legend">${Object.entries(CUT_LABELS).map(([k,v])=>`<span class="${k}">${v}</span>`).join("")}</div>
      ${cuts.length ? `<div class="cut-list">${cuts.map((c,i)=>`<details class="cut-item"><summary><span class="badge ${c.kind}">${esc(CUT_LABELS[c.kind] || "Այլ")}</span> <span class="mono">${clock(c.start)}–${clock(c.end)}</span> · ${fmtDur(c.duration)}</summary><p>${esc(cutReason(c.why))}</p><dl><dt>Առաջին բառերը</dt><dd>${esc(c.first_words || "Տվյալ չկա")}</dd><dt>Վերջին բառերը</dt><dd>${esc(c.last_words || "Տվյալ չկա")}</dd></dl><button class="btn" data-transition="${i}">Դիտել անցումը · 10վ</button><div class="transition-result" aria-live="polite"></div></details>`).join("")}</div>` : `<p>${esc(d.no_cuts_reason || "Մանրամասն տվյալ չկա։")}</p>${d.min_silence_sec != null ? `<p class="muted">Լռության ստուգման շեմը՝ ${esc(d.min_silence_sec)}վ։ Միայն լռությունը բավարար չէ դասի մասը կտրելու համար։</p>` : ""}`}`;
    target.querySelectorAll("[data-cut-jump]").forEach(button=>button.addEventListener("click",()=>{const detail=target.querySelectorAll(".cut-item")[Number(button.dataset.cutJump)];detail.open=true;detail.querySelector("summary").focus();detail.scrollIntoView({block:"nearest"});}));
    target.querySelectorAll("[data-transition]").forEach(button=>button.addEventListener("click",async()=>{
      const result = button.nextElementSibling; button.disabled = true; result.textContent = "Պատրաստում ենք անվտանգ preview-ն…";
      try {const p = await api(`/api/jobs/${id}/cuts/${button.dataset.transition}/preview`,{method:"POST"}); result.innerHTML = `<video controls playsinline preload="metadata" src="${esc(p.url)}" aria-label="Կտրման անցումը վերջնական մշուշված տեսանյութում"></video>`;}
      catch(error) {result.textContent = error.message === "auth" ? "Մուտք գործեք կրկին։" : error.message;}
      finally {button.disabled = false;}
    }));
  } catch(error) {target.textContent = error.message;}
}
async function viewLibrary() {
  if (/^#\/library\/settings(?:\?|$)/.test(location.hash)) return viewLearningSettings();
  if (/^#\/library\/watch-stats(?:\?|$)/.test(location.hash)) return viewWatchStats();
  const lessonMatch = location.hash.match(/^#\/library\/(\d+)(?:\?|$)/);
  if (lessonMatch) return viewLesson(Number(lessonMatch[1]));
  installLearningLifecycle();
  if (!$("#library-grid")) {
    S.libraryFacets = null;
    $("#main").innerHTML = `<section class="library-page">${pageHeading("Գրադարան", "Ձեր խմբերի դասերը՝ մեկ վայրում։", '<div id="library-tools"></div>')}<div class="library-filters"><label class="fld"><span>Որոնել դասի անունը կամ տեքստը</span><input class="input" id="library-search" type="search" placeholder="Անուն, թեմա կամ բառ…" maxlength="200"></label><label class="fld"><span>Խումբ</span><select class="input" id="library-group"><option value="">Բոլոր խմբերը</option></select></label><label class="fld"><span>Առարկա</span><select class="input" id="library-subject"><option value="">Բոլոր առարկաները</option></select></label></div><div id="library-continue"></div><div id="library-text-results" aria-live="polite"></div><p id="library-count" role="status"></p><div class="job-grid" id="library-grid">${pageSkeleton("Բեռնում ենք դասերը")}</div></section>`;
    $("#library-search").addEventListener("input", ()=>{renderLibrary(); scheduleLibraryTextSearch();});
    $("#library-group").addEventListener("change", ()=>{fillLibrarySubjects(); renderLibrary(); renderLibraryText();});
    $("#library-subject").addEventListener("change", ()=>{renderLibrary(); renderLibraryText();});
    $("#library-text-results").addEventListener("click",event => {
      const link=event.target.closest("[data-library-lesson]");
      if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button>0) return;
      event.preventDefault(); location.hash=lessonRoute({job_id:link.dataset.libraryLesson},link.dataset.lessonTime);
    });
    $("#library-grid").addEventListener("click",event=>{if (event.target.closest("[data-library-clear]")) clearLibraryFilters();});
    $("#library-grid").addEventListener("toggle", loadLessonNotes, true);   // notes load when opened
  }
  // options come from every permitted group/subject (with counts), not only from published lessons.
  const stale = !S.libraryFacets || Date.now() - S.libraryFacets._t > 30000;
  const grid=$("#library-grid"), identity=learningIdentity();
  const [lessons, facets, progress] = await Promise.all([api("/api/library"), stale ? api("/api/library/facets") : S.libraryFacets, api("/api/library/progress")]);
  if (!grid.isConnected || $("#library-grid")!==grid || learningIdentity()!==identity) return;
  S.library = lessons; S.libraryProgress=progress;
  if (stale) {facets._t = Date.now(); S.libraryFacets = facets;}
  fillLibraryGroups(); fillLibrarySubjects(); renderLibrary(); renderContinueWatching(); renderLearningLinks();
}
const libraryOption = (value, label, count) => `<option value="${esc(value)}">${esc(label)} (${Number(count) || 0} դաս)</option>`;
function fillLibraryOptions(select, options, keep) {
  if (select.dataset.sig === options) return;
  const previous = select.value; select.innerHTML = options; select.dataset.sig = options;
  select.value = keep(previous) ? previous : (select.options[0]?.value ?? "");
}
function fillLibraryGroups() {
  const select = $("#library-group"), groups = S.libraryFacets?.groups || [];
  const locked = S.role === "student" && groups.length === 1;   // a student sees only their own group
  const total = groups.reduce((sum, g) => sum + g.lessons, 0);
  fillLibraryOptions(select, (locked ? "" : `<option value="">Բոլոր խմբերը (${total} դաս)</option>`) +
    groups.map(g => libraryOption(g.name, g.archived ? `${g.name} · ավարտված` : g.name, g.lessons)).join(""),
    value => locked ? value === groups[0].name : !value || groups.some(g => g.name === value));
  select.disabled = locked;
}
function fillLibrarySubjects() {
  const group = $("#library-group").value, counts = new Map();
  const groups = (S.libraryFacets?.groups || []).filter(g => !group || g.name === group);
  for (const g of groups) for (const s of g.subjects) counts.set(s.name, (counts.get(s.name) || 0) + s.lessons);
  const names = [...counts.keys()];
  if (!group) names.sort((a, b) => a.localeCompare(b, "hy"));
  fillLibraryOptions($("#library-subject"), `<option value="">Բոլոր առարկաները (${groups.reduce((sum, g) => sum + g.lessons, 0)} դաս)</option>` +
    names.map(name => libraryOption(name, name, counts.get(name))).join(""), value => !value || counts.has(value));
}
function clearLibraryFilters() {
  const search=$("#library-search"), group=$("#library-group"), subject=$("#library-subject");
  if (!search || !group || !subject) return;
  search.value="";if (!group.disabled) group.value="";
  fillLibrarySubjects();subject.value="";S.libraryText=null;
  const results=$("#library-text-results");if (results) results.innerHTML="";
  renderLibrary();renderLibraryText();
}
function renderLibrary() {
  const query = $("#library-search").value.trim().toLocaleLowerCase(), group = $("#library-group").value, subject = $("#library-subject").value;
  const list = (S.library || []).filter(x=>(!group || x.group === group) && (!subject || x.subject === subject) && (!query || [x.title,x.subject,...(x.chapters || []).map(c=>c.title)].join(" ").toLocaleLowerCase().includes(query)));
  const demoCount=list.filter(x=>x.demo || x.playable===false).length;
  set("#library-count", `${list.length} դաս${demoCount ? ` · ${demoCount} ցուցադրական` : ""}`);
  set("#library-grid",list.length ? list.map(libraryCardHtml).join("") : '<div class="card empty library-empty"><div class="ic acc">'+ic("book")+'</div><b>Համապատասխան դաս չգտնվեց</b><p>Փորձեք այլ բառ կամ մաքրեք ընտրված ֆիլտրերը։</p><button class="btn" data-library-clear>Մաքրել ֆիլտրերը</button></div>');
}
/* measured publications and explicitly separate demonstration data. */
function academyStatNumber(value, digits = 1) {
  const number=Number(value);
  return (Number.isFinite(number) ? Math.max(0,number) : 0).toLocaleString("hy-AM",{maximumFractionDigits:digits});
}
function academyStatCount(value) {const number=Number(value);return Number.isFinite(number) ? Math.max(0,Math.floor(number)) : 0;}
function academyStatsChart(data) {
  const days=data.daily || [], total=days.reduce((sum,x)=>sum+academyStatCount(x.actual_lessons)+academyStatCount(x.demo_lessons),0);
  if (!total) return '<p class="academy-chart-empty muted">Վերջին 30 օրում այս ընտրությամբ դասեր չկան։</p>';
  const max=Math.max(1,...days.map(x=>academyStatCount(x.actual_lessons)+academyStatCount(x.demo_lessons))), slot=640/Math.max(1,days.length), width=Math.min(16,slot*.65), base=144;
  const bars=days.map((day,i)=>{
    const actual=academyStatCount(day.actual_lessons), demo=academyStatCount(day.demo_lessons), height=actual/max*112, demoHeight=demo/max*112, x=48+i*slot+(slot-width)/2;
    return `<g><title>${esc(day.date)}՝ ${actual} հրապարակված, ${demo} ցուցադրական դաս</title><rect class="academy-chart-actual" x="${x}" y="${base-height}" width="${width}" height="${height}" rx="2"></rect><rect class="academy-chart-demo" x="${x}" y="${base-height-demoHeight}" width="${width}" height="${demoHeight}" rx="2"></rect></g>`;
  }).join("");
  return `<svg class="stats-chart academy-stats-chart" viewBox="0 0 720 180" role="img" aria-labelledby="academy-chart-title academy-chart-desc"><title id="academy-chart-title">Դասերը՝ վերջին 30 օրում</title><desc id="academy-chart-desc">Ըստ դասի ամսաթվի․ հրապարակված և ցուցադրական դասերը նշված են առանձին գույներով։ Մանրամասն թվերը հասանելի են ստորև։</desc><path class="academy-chart-axis" d="M48 24V144H688"></path><text x="36" y="36" text-anchor="end">${max}</text><text x="36" y="148" text-anchor="end">0</text>${bars}<text x="48" y="170">${esc(days[0]?.date || "")}</text><text x="688" y="170" text-anchor="end">${esc(days[days.length-1]?.date || "")}</text></svg>`;
}
function academyStatsBreakdown(rows, bySubject = false) {
  if (!rows?.length) return '<p class="muted">Այս ընտրության համար տվյալներ չկան։</p>';
  return rows.map(row=>{
    const actual=row.actual || {}, demo=row.demo || {};
    return `<article class="academy-stat-row"><div class="academy-stat-name"><h3>${esc(bySubject ? row.subject : row.group)}</h3>${bySubject ? `<span>${esc(row.group)}</span>` : ""}</div><dl><div><dt>Հրապարակված</dt><dd>${academyStatCount(actual.published_lessons)} դաս</dd></div><div><dt>Տեսանյութ</dt><dd>${academyStatNumber(actual.video_hours)} ժ</dd></div><div><dt>Խնայված</dt><dd>${academyStatNumber(actual.saved_minutes)} ր</dd></div></dl>${demo.lessons ? `<p class="academy-stat-demo"><span class="badge soft">Ցուցադրական</span> ${academyStatCount(demo.lessons)} դաս${Number(demo.video_hours)>0 ? ` · ${academyStatNumber(demo.video_hours)} ժ՝ նշված տևողությամբ` : ""}</p>` : ""}</article>`;
  }).join("");
}
function academyTechnicalStats(data) {
  if (S.role!=="admin" || !data.technical) return "";
  const d=data.technical, assumptions=data.assumptions || {}, duration=value=>value==null ? "Տվյալ չկա" : fmtDur(value);
  const phases=typeof PHASES==="undefined" ? [] : PHASES;
  return `<details class="card academy-technical" id="academy-technical"><summary>Տեխնիկական տվյալներ</summary><div class="academy-technical-body"><h2>Մշակման մանրամասներ</h2><dl class="metric-list"><dt>Մշակման միջին տևողություն</dt><dd>${duration(d.avg_processing_sec)}</dd><dt>Մշակման և տեսագրության տևողությունների հարաբերություն</dt><dd>${d.processing_ratio==null ? "Տվյալ չկա" : academyStatNumber(d.processing_ratio,2)+"×"}</dd><dt>Ավարտի կանխատեսման միջին շեղում</dt><dd>${duration(d.eta_mean_error_sec)} · ${academyStatCount(d.eta_samples)} չափում</dd><dt>Մշուշի կիրառումներ կադրերում</dt><dd>${d.blurred_boxes==null ? "Տվյալ չկա" : academyStatNumber(d.blurred_boxes,0)}</dd><dt>Դեմքերի հետագծեր</dt><dd>${d.student_tracks==null ? "Տվյալ չկա" : academyStatNumber(d.student_tracks,0)}</dd></dl><p class="muted">Միևնույն դեմքը կարող է հաշվվել մի քանի կադրերում։ Բացակայող չափումները չեն լրացվում ենթադրություններով։</p><h2>Ամենաերկար մշակվող փուլերը</h2><ol class="stage-ranks">${(d.slow_stages || []).slice(0,6).map(x=>`<li><span>${esc(x.label || phases.find(p=>p.stages.includes(x.stage))?.label || "Այլ փուլ")}</span><b>${duration(x.seconds)}</b></li>`).join("") || '<li class="muted">Տվյալ չկա</li>'}</ol><h2>Խնայված ժամանակի գնահատումը</h2><p>${academyStatNumber(assumptions.editing_min_per_hour)} րոպե խմբագրում՝ տեսագրության յուրաքանչյուր ժամի համար, և ${academyStatNumber(assumptions.publishing_min_per_lesson)} րոպե՝ յուրաքանչյուր իրական դասի հրապարակման համար։ Սա աշխատանքի գնահատական է։</p><a class="btn" href="#/settings">Փոխել հաշվարկի կարգավորումները</a></div></details>`;
}
function academyStatsHtml(data) {
  const cards=data.cards || data.actual || {}, demo=data.demo || {}, days=data.daily || [];
  return `<div class="academy-stats-metrics">${stat("check","acc","Հրապարակված դասեր",academyStatNumber(cards.published_lessons,0),"Իրական դասերի հրապարակումներ")}${stat("clock","acc","Տեսանյութի ժամեր",academyStatNumber(cards.video_hours),"Հրապարակված տեսանյութերի տևողությունը")}${stat("scissors","acc","Խնայված ժամանակ",academyStatNumber(cards.saved_minutes)+" ր","Գնահատական՝ խմբագրման և հրապարակման համար")}</div>${demo.lessons ? `<aside class="academy-demo-summary"><span class="badge soft">Ցուցադրական տվյալներ</span><strong>${academyStatCount(demo.lessons)} դաս${Number(demo.video_hours)>0 ? ` · ${academyStatNumber(demo.video_hours)} ժ՝ նշված տևողությամբ` : ""}</strong><p>Այս օրինակները չեն ավելացվում իրական հրապարակումների կամ խնայված ժամանակի թվերին։</p></aside>` : ""}<section class="card academy-stats-trend"><h2>Դասերը՝ վերջին 30 օրում</h2><p class="muted">Համեմատությունը՝ ըստ դասի ամսաթվի։</p><div class="academy-chart-legend"><span><i class="actual" aria-hidden="true"></i>Հրապարակված</span><span><i class="demo" aria-hidden="true"></i>Ցուցադրական</span></div>${academyStatsChart(data)}<details class="academy-chart-data"><summary>Դիտել օրերի տվյալները</summary><table><thead><tr><th scope="col">Օր</th><th scope="col">Հրապարակված</th><th scope="col">Ցուցադրական</th></tr></thead><tbody>${days.map(x=>`<tr><th scope="row">${esc(x.date)}</th><td>${academyStatCount(x.actual_lessons)}</td><td>${academyStatCount(x.demo_lessons)}</td></tr>`).join("")}</tbody></table></details></section><div class="academy-stats-breakdowns"><section class="card"><h2>Ըստ խմբերի</h2>${academyStatsBreakdown(data.by_group)}</section><section class="card"><h2>Ըստ առարկաների</h2>${academyStatsBreakdown(data.by_subject,true)}</section></div>${academyTechnicalStats(data)}`;
}
function academyStatsFilterOptions(select, options, value) {
  if (select.dataset.options!==options) {select.innerHTML=options;select.dataset.options=options;}
  select.value=value;select.disabled=false;
}
async function viewStats() {
  const hash=location.hash, identity=`${S.role}:${S.user?.id || "shared"}`, query=new URLSearchParams(hash.split("?")[1] || "");
  const group=query.get("group") || "", subject=query.get("subject") || "";
  let root=$("#academy-stats");
  if (root && root.dataset.hash===hash && root.dataset.identity===identity && (root.academyLoading || Date.now()-Number(root.dataset.loadedAt)<30000)) return;
  if (!root || root.dataset.hash!==hash || root.dataset.identity!==identity) {
    $("#main").innerHTML=`<section id="academy-stats" class="academy-stats">${pageHeading("Վիճակագրություն","Ձեր խմբերի դասերն ու աշխատանքի արդյունքները՝ թվերով։")}<div class="academy-stats-filters"><label class="fld"><span>Խումբ</span><select class="input" id="stats-group" disabled><option value="">Բոլոր խմբերը</option></select></label><label class="fld"><span>Առարկա</span><select class="input" id="stats-subject" disabled><option value="">Բոլոր առարկաները</option></select></label></div><div id="academy-stats-body">${pageSkeleton("Բեռնում ենք վիճակագրությունը")}</div></section>`;
    root=$("#academy-stats");root.dataset.hash=hash;root.dataset.identity=identity;
    const change=event=>{
      const nextGroup=root.querySelector("#stats-group").value;let nextSubject=root.querySelector("#stats-subject").value;
      if (event.target.id==="stats-group" && nextSubject && nextGroup) {
        const subjects=root.academyData?.filters?.groups?.find(x=>x.name===nextGroup)?.subjects || [];
        if (!subjects.some(x=>x.name===nextSubject)) nextSubject="";
      }
      const next=new URLSearchParams();if (nextGroup) next.set("group",nextGroup);if (nextSubject) next.set("subject",nextSubject);
      location.hash="#/stats"+(next.size ? "?"+next.toString() : "");
    };
    root.querySelector("#stats-group").addEventListener("change",change);root.querySelector("#stats-subject").addEventListener("change",change);
  }
  const same=()=>root.isConnected && $("#academy-stats")===root && location.hash===hash && `${S.role}:${S.user?.id || "shared"}`===identity;
  root.academyLoading=true;
  try {
    const params=new URLSearchParams();if (group) params.set("group",group);if (subject) params.set("subject",subject);
    const data=await api("/api/academy/stats"+(params.size ? "?"+params.toString() : ""));
    if (!same()) return;
    root.academyData=data;
    const groups=data.filters?.groups || [], subjects=data.filters?.subjects || [], option=x=>`<option value="${esc(x.name)}">${esc(x.name)} (${academyStatCount(x.count)} դաս)</option>`;
    academyStatsFilterOptions(root.querySelector("#stats-group"),'<option value="">Բոլոր խմբերը</option>'+groups.map(option).join(""),group);
    academyStatsFilterOptions(root.querySelector("#stats-subject"),'<option value="">Բոլոր առարկաները</option>'+subjects.map(option).join("")+(subject && !subjects.some(x=>x.name===subject) ? option({name:subject,count:0}) : ""),subject);
    const body=root.querySelector("#academy-stats-body"), html=academyStatsHtml(data), technicalOpen=body.querySelector("#academy-technical")?.open;
    if (body.innerHTML!==html) {body.innerHTML=html;if (technicalOpen && body.querySelector("#academy-technical")) body.querySelector("#academy-technical").open=true;}
    root.dataset.loadedAt=String(Date.now());
  } catch(error) {
    if (!same() || error.message==="auth") return;
    root.querySelector("#academy-stats-body").innerHTML='<div class="alert err" role="alert"><div>Վիճակագրությունը բեռնել չհաջողվեց։ Փորձեք կրկին։</div><button class="btn" id="academy-stats-retry">Կրկին բեռնել</button></div>';
    root.querySelector("#academy-stats-retry").addEventListener("click",()=>{root.dataset.loadedAt="0";viewStats();});
    root.dataset.loadedAt=String(Date.now());
  } finally {root.academyLoading=false;}
}

// Only animate toward already observed values; never manufacture progress between polls.
const shownProgress = new Map();
new MutationObserver(()=>{
  document.querySelectorAll('[data-progress-job]').forEach(el=>{
    if (el.dataset.animating) return;
    const id = el.dataset.progressJob, target = Number(el.dataset.progressValue);
    let shown = Math.min(target, shownProgress.get(id) ?? target);
    el.dataset.animating = "true";
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const step = ()=>{if (!el.isConnected) return; shown = reduced ? target : Math.min(target, shown+1); shownProgress.set(id,shown); el.textContent=shown+'%'; if(shown<target)setTimeout(step,45);};
    step();
  });
}).observe(document.body,{childList:true,subtree:true});

window.addEventListener("resize",()=>{document.querySelectorAll(".nav-more").forEach(menu=>{if(window.innerWidth>=1100)menu.open=true;else if(menu.dataset.wasWide==="true")menu.open=false;menu.dataset.wasWide=String(window.innerWidth>=1100);});});
