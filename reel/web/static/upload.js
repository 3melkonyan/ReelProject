"use strict";
/* File objects remain in memory across SPA navigation. Reloads need file re-selection;
   only the manifest is persisted, never media, passwords or session credentials. */
const UP_KEY = "reel-upload-v2";
let uploadBusy = false, uploadXHR = null, uploadRetry = null, uploadWake = null, uploadWakePending = null, uploadSequence = 0;
let uploadIntent = null;
try { uploadIntent = JSON.parse(localStorage.getItem(UP_KEY) || "null"); } catch (_) {}
function rememberUpload() {
  try { if (S.up) localStorage.setItem(UP_KEY, JSON.stringify({body:S.up.body,id:S.up.id})); else localStorage.removeItem(UP_KEY); } catch (_) {}
}
async function uploadAwake() {
  if (!S.up || document.hidden || uploadWake || uploadWakePending || !navigator.wakeLock) return;
  try {
    uploadWakePending = navigator.wakeLock.request("screen");
    const lock = await uploadWakePending;
    if (!S.up || document.hidden) { await lock.release(); return; }
    uploadWake = lock;
    lock.addEventListener("release", () => { if (uploadWake === lock) uploadWake = null; renderUpProgress(); });
  } catch (_) {} finally { uploadWakePending = null; renderUpProgress(); }
}
function releaseUploadWake() { const lock=uploadWake; uploadWake=null; if(lock)lock.release().catch(()=>{}); }
async function viewUpload() {
  if (!canUpload()) { set("#main", (`<div class="alert err">${i18nText("ui.upload.you_do_not_have_permission_to_upload")}</div>`)); return; }
  if ($("#upload-view")) { renderUpProgress(); return; }
  set("#main", `<div id="upload-view"><div class="top"><div><span class="eyebrow">REEL</span><h1>${i18nText("ui.app.upload_lesson")}</h1><p>${i18nText("ui.upload.from_your_phone_or_computer_in_one")}</p></div><a class="account-text-link" href="#/record">${i18nText("ui.upload.record_on_phone")}</a></div>
    <section class="card upload-card"><form id="upload-form"><div class="grid upload-fields">
    <label class="fld"><span>${i18nText("ui.groups.course")}</span><select class="input" id="up-course" aria-label="${i18nText("ui.groups.choose_course")}"></select></label><label class="fld"><span>${i18nText("ui.accounts.group")}</span><select class="input" id="up-group" required></select></label>
    <label class="fld"><span>${i18nText("ui.app.subject")}</span><select class="input" id="up-subject" required></select></label>
    <label class="fld"><span>${i18nText("ui.record.lesson_number")}</span><input class="input" id="up-number" type="number" inputmode="numeric" min="1" max="999" required aria-describedby="up-number-hint"><small id="up-number-hint" class="muted">${i18nText("ui.upload.named_lesson_n_you_can_change_the")}</small></label></div>
    <label class="fld upload-date"><span>${i18nText("ui.app.lesson_date_2")}</span><input class="input" id="up-date" type="date" value="${lessonToday()}" max="${lessonToday()}" required aria-describedby="up-date-hint"><small class="muted" id="up-date-hint">${i18nText("ui.upload.unless_you_change_the_date_the_video")}</small></label>
    <div class="upload-tip"><b>${i18nText("ui.upload.choose_the_original_video")}</b><p>${i18nText("ui.upload.photos_on_iphone_may_compress_video_choose")}</p></div>
    <label class="upload-drop" id="up-drop"><span class="upload-glyph" aria-hidden="true">${ic("upload")}</span><b>${i18nText("ui.upload.select_videos_or_drag_them_here")}</b><span>${i18nText("ui.upload.videos_up_to_8_parts")}</span><input aria-label="${i18nText("ui.upload.choose_videos")}" id="up-files" type="file" accept="video/*,.mp4,.mov,.m4v,.mkv,.avi,.webm,.mts,.m2ts" multiple required><small class="muted">${i18nText("ui.upload.from_photos_or_files_parts_are_sorted")}</small></label>
    <div id="up-files-list" class="muted"></div><p class="muted">${i18nText("ui.upload.use_wi_fi_uploads_resume_automatically_when")}</p>
    <div id="up-form-error" class="alert err" role="alert" hidden></div><button class="btn primary" id="up-start" type="submit">${ic("upload")} ${i18nText("ui.upload.upload_lesson")}</button></form>
    <section id="up-progress" aria-live="polite" hidden></section></section></div>`);
  const groups = await api("/api/groups");
  if (!$("#upload-form")) return;
  if (!Array.isArray(groups.groups)) {
    set("#main", `<div class="alert err" role="alert">${i18nText("ui.record.could_not_load_groups")} ${esc(groups.error || i18nText("ui.upload.try_again"))}</div><button class="btn" id="up-reload">${i18nText("ui.admin.reload")}</button>`);
    $("#up-reload").onclick=viewUpload; return;
  }
  const items = groups.groups;
  const entries = Array.isArray(items) ? items.map(g => [g.name,g]) : Object.entries(items);
  const groupMap = Object.fromEntries(entries);
  const g = $("#up-group"), subject = $("#up-subject"), number = $("#up-number");
  g.innerHTML = entries.map(([name]) => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
  const requested=new URLSearchParams(location.hash.split('?')[1]||'');
  const intent = S.up ? {body:S.up.body} : requested.get('group')?{body:{group:requested.get('group'),subject:requested.get('subject')}}:uploadIntent;
  if (intent && intent.body && groupMap[intent.body.group]) g.value = intent.body.group;
  const date=$("#up-date");
  date.dataset.changed=String(Boolean(intent?.body?.lesson_date_changed));
  if(intent?.body?.lesson_date)date.value=intent.body.lesson_date;
  date.addEventListener("input",()=>{date.dataset.changed="true";});
  async function subjects(restore = false) {
    const row = groupMap[g.value] || {}, values = row.subjects || [];
    subject.innerHTML = values.map(s => `<option value="${esc(typeof s === "string" ? s : s.name)}">${esc(typeof s === "string" ? s : s.name)}</option>`).join("");
    if (restore && intent?.body?.subject && [...subject.options].some(o=>o.value===intent.body.subject)) subject.value = intent.body.subject;
    await nextNumber(restore);
  }
  async function nextNumber(restore = false) {
    const seq = ++uploadSequence; number.disabled = true;
    try {
      const d = await api(`/api/uploads/next-number?group=${encodeURIComponent(g.value)}&subject=${encodeURIComponent(subject.value)}`);
      if (seq === uploadSequence && $("#up-number") === number) number.value = restore && intent?.body?.lesson_number ? intent.body.lesson_number : d.number;
    } catch (e) { if (seq === uploadSequence && $("#up-number") === number && e.message !== "auth") { number.value = 1; toast(i18nText("ui.upload.could_not_get_the_next_lesson_number")); } }
    finally { if (seq === uploadSequence) number.disabled = false; }
  }
  const courseSelect=$('#up-course');
  if(courseSelect){
    let courses=[];
    try{const data=await api('/api/courses');courses=(data?.courses||[]).filter(c=>c.groups.length);if(data?.unassigned?.length)courses.push({id:'other',name:i18nText("ui.upload.other_groups"),groups:data.unassigned});}catch(_){}
    if(!courses.length)courses=[{id:'all',name:i18nText("ui.upload.all_courses"),groups:entries.map(([name])=>({name}))}];
    courseSelect.innerHTML=courses.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
    const initial=courses.find(c=>c.groups.some(item=>item.name===g.value));if(initial)courseSelect.value=initial.id;
    const filterGroups=()=>{const previous=g.value,c=courses.find(x=>x.id===courseSelect.value);g.innerHTML=(c?.groups||[]).map(item=>`<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('');if((c?.groups||[]).some(item=>item.name===previous))g.value=previous;};
    filterGroups();courseSelect.onchange=async()=>{filterGroups();await subjects();};
  }
  g.onchange = () => subjects(); subject.onchange = () => nextNumber();
  $("#up-files").onchange = e => pickUploadFiles(e.target.files);
  const drop=$('#up-drop');
  if(drop){
    ['dragenter','dragover'].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.add('dragging');}));
    ['dragleave','drop'].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.remove('dragging');}));
    drop.addEventListener('drop',async e=>{const files=[...(e.dataTransfer?.files||[])];if(files.length){await pickUploadFiles(files);$('#up-files').required=false;}});
  }
  $("#upload-form").onsubmit = e => { e.preventDefault(); startUpload(); };
  await subjects(true);
  if (uploadIntent && !S.up) { $("#up-files-list").textContent = i18nText("ui.upload.an_upload_is_unfinished_select_the_same"); }
  renderUpProgress();
  if (S.upFiles && S.up && S.up.state === "paused") resumeUpload();
}
async function startUpload() {
  if (uploadBusy || S.up) return;
  const ordered = Array.isArray(S.upOrder) && S.upOrder.length > 0;
  const files = ordered ? S.upOrder.map(item => item.file) : [...$("#up-files").files].sort(byName);
  const body = {group:$("#up-group").value, subject:$("#up-subject").value, lesson_number:Number($("#up-number").value), lesson_date:$("#up-date").value, lesson_date_changed:$("#up-date").dataset.changed==="true", files:files.map(f => ({name:f.name,size:f.size})), ...(ordered ? {ordered:true} : {})};
  if (!files.length || files.length > 8) return toast(i18nText("ui.upload.choose_1_8_videos"));
  S.upFiles = files;
  S.up = {body,id:null,files:files.map(f=>({name:f.name,size:f.size,got:0})),total:files.reduce((a,f)=>a+f.size,0),state:"paused",message:"",inflight:0,started:Date.now(),base:0};
  rememberUpload(); await resumeUpload();
}
function uploadChunk(uid, index, file, offset, length) {
  return new Promise((resolve,reject) => {
    const xhr = new XMLHttpRequest(); uploadXHR = xhr;
    xhr.open("PUT", `/api/uploads/${uid}/${index}?offset=${offset}`);
    xhr.withCredentials = true; xhr.timeout = 120000;
    xhr.setRequestHeader("X-Requested-With", "reel"); xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = e => { if(S.up) {S.up.inflight=e.loaded;renderUpProgress();} };
    const fail = () => { const e = new Error(i18nText("ui.upload.connection_lost_uploading_will_resume_automatically")); e.net = true; reject(e); };
    xhr.onerror = fail; xhr.ontimeout = fail; xhr.onabort = fail;
    xhr.onload = () => {
      let data = {}; try {data=JSON.parse(xhr.responseText);} catch (_) {}
      if (xhr.status === 409) return resolve({conflict:true});
      if (xhr.status >= 500 || !xhr.status) return fail();
      if (xhr.status !== 200) return reject(new Error(xhr.status === 401 ? i18nText("ui.upload.sign_in_to_continue") : String(data.detail || i18nText("ui.upload.upload_failed"))));
      resolve(data);
    };
    xhr.send(file.slice(offset,offset+length));
  });
}
async function resumeUpload() {
  if (uploadBusy || !S.up || S.up.state === "cancelling" || !S.upFiles || !canUpload()) return;
  clearTimeout(uploadRetry); uploadRetry = null;
  if (navigator.onLine === false) {S.up.state="paused";S.up.message=i18nText("ui.upload.waiting_for_wi_fi");renderUpProgress();return;}
  const up = S.up; uploadBusy = true; up.state="sending";up.message="";up.started=Date.now();
  try {
    await uploadAwake(); renderUpProgress();
    if (!up.id) {
      const d = await api("/api/uploads",{method:"POST",body:JSON.stringify(up.body)});
      up.id=d.id; up.chunk=d.chunk; rememberUpload();
      if (d.completed) return completeUpload(d.result);
    }
    let status = await api(`/api/uploads/${up.id}`);
    if (status.completed) return completeUpload(status.result);
    up.chunk=status.chunk || up.chunk;
    status.received.forEach((n,i)=>{up.files[i].got=n;});
    up.base=up.files.reduce((n,f)=>n+f.got,0);
    for(let i=0;i<up.files.length;i++) {
      while(up.files[i].got<up.files[i].size) {
        if(S.up!==up) return;
        const f=up.files[i], offset=f.got;
        const d=await uploadChunk(up.id,i,S.upFiles[i],offset,Math.min(up.chunk,f.size-offset));
        up.inflight=0;
        if(d.conflict) {
          status=await api(`/api/uploads/${up.id}`);
          if(status.completed) return completeUpload(status.result);
          status.received.forEach((n,j)=>{up.files[j].got=n;});
        } else f.got=d.received;
        renderUpProgress();
      }
    }
    up.state="finishing";renderUpProgress();
    completeUpload(await api(`/api/uploads/${up.id}/finish`,{method:"POST"}));
  } catch(e) {
    if(S.up!==up || up.state === "cancelling") return;
    up.inflight=0;up.state=e.net?"paused":"error";up.message=e.net?i18nText("ui.upload.connection_lost_we_will_resume_automatically"):e.message;
    if(e.net) uploadRetry=setTimeout(resumeUpload,5000);
  } finally { uploadBusy=false;uploadXHR=null; if(!S.up || S.up.state!=="sending")releaseUploadWake();renderUpProgress(); }
}
function completeUpload(result) {
  const job=result.job_id || result.duplicate_of;
  S.up=null; S.upFiles=null; S.upOrder=null; uploadIntent=null; rememberUpload();releaseUploadWake();
  if($("#upload-view")) set("#up-progress", `<div class="alert ok">${ic("check")}<div>${i18nText("ui.upload.lesson_received")} ${result.duplicate_of?i18nText("ui.record.this_video_already_exists"):i18nText("ui.upload.processing_will_start_in_queue_order")} <a href="#/jobs/${job}">${i18nText("ui.record.open_lesson")}</a></div></div>`);
  const p=$("#up-progress");if(p)p.hidden=false;
  const form=$("#upload-form");if(form){form.hidden=false;form.reset();const group=$("#up-group"),date=$("#up-date");if(date){date.value=lessonToday();date.max=lessonToday();date.dataset.changed="false";}if(group && group.onchange)group.onchange();}
  toast(i18nText("ui.upload.lesson_received_2")); S.last={}; S.summary=null;
}
function renderUpProgress() {
  const up=S.up, dock=$("#up-dock"), target=$("#up-progress"), form=$("#upload-form");
  if(dock){dock.hidden=!up;if(up)dock.textContent=`${i18nText("ui.upload.lesson")} ${up.body.lesson_number} · ${Math.floor(100*(up.files.reduce((s,f)=>s+f.got,0)+(up.inflight||0))/up.total)}%`;}
  if(!up) return;
  if(form)form.hidden=true;
  if(!target)return;
  target.hidden=false;
  const got=Math.min(up.total,up.files.reduce((s,f)=>s+f.got,0)+(up.inflight||0)), percent=Math.min(100,100*got/up.total);
  const rate=Math.max(0,(got-(up.base||0))/Math.max(1,(Date.now()-up.started)/1000));
  const active=up.state==="sending" || up.state==="finishing";
  const status=up.state==="finishing"?i18nText("ui.upload.checking_video_and_registering_the_lesson"):up.message || i18nText("ui.upload.uploading");
  /* Progress ticks (~20/s) only update the numbers: rebuilding the buttons on every tick
     lost mouse clicks on Cancel (mousedown and mouseup landed on different elements). */
  const key=JSON.stringify([up.body,active,status,!!uploadWake]);
  if(target.dataset.key!==key || !target.querySelector('[data-up="pct"]')) {
    target.dataset.key=key;
    target.innerHTML=`<h2>${i18nText("ui.app.lesson")} ${esc(up.body.lesson_number)}</h2><p>${esc(up.body.group)} · ${esc(up.body.subject)}</p><div class="row"><b data-up="pct"></b><span class="muted" data-up="size"></span></div><progress class="upload-progress" max="100" aria-label="${i18nText("ui.upload.upload_percentage")}"></progress>
    <p class="muted" data-up="rate"></p>
    <p role="status">${esc(status)}</p>
    <ul class="upload-parts">${up.files.map(f=>`<li><span>${esc(f.name)}</span><span data-up="part"></span></li>`).join("")}</ul>
    <div class="upload-tip">${uploadWake?i18nText("ui.upload.keeping_the_screen_awake"):i18nText("ui.upload.keep_the_screen_on_and_the_browser")} ${i18nText("ui.upload.your_phone_may_pause_background_activity_reopen")}</div>
    <div class="actions">${!active?(`<button class="btn primary" id="up-resume" type="button">${i18nText("ui.upload.continue")}</button>`):""}<button class="btn danger" id="up-cancel" type="button">${i18nText("ui.upload.cancel_upload")}</button></div>`;
    const resume=target.querySelector("#up-resume");if(resume)resume.onclick=resumeUpload;
    target.querySelector("#up-cancel").onclick=cancelUpload;
  }
  target.querySelector('[data-up="pct"]').textContent=`${percent.toFixed(1)}%`;
  target.querySelector('[data-up="size"]').textContent=`${fmtSize(got)} / ${fmtSize(up.total)}`;
  target.querySelector("progress").value=percent;
  target.querySelector('[data-up="rate"]').textContent=active&&rate?`${fmtSize(rate)}${i18nText("ui.upload.s_about")} ${fmtDur((up.total-got)/rate)}`:"";
  target.querySelectorAll('[data-up="part"]').forEach((el,i)=>{el.textContent=`${Math.floor(100*up.files[i].got/up.files[i].size)}%`;});
}
async function cancelUpload() {
  if(!S.up || !confirm(i18nText("ui.upload.cancel_the_unfinished_upload")))return;
  const up=S.up; up.state="cancelling";up.message=i18nText("ui.upload.cancelling");clearTimeout(uploadRetry);if(uploadXHR)uploadXHR.abort();renderUpProgress();
  try {if(up.id)await api(`/api/uploads/${up.id}`,{method:"DELETE"});}
  catch(e){up.state="error";up.message=e.message;renderUpProgress();return;}
  S.up=null;S.upFiles=null;S.upOrder=null;uploadIntent=null;rememberUpload();releaseUploadWake();
  S.last={};route();
}
window.addEventListener("beforeunload",e=>{if(S.up){e.preventDefault();e.returnValue="";}});
window.addEventListener("online",resumeUpload);
document.addEventListener("visibilitychange",()=>{if(!document.hidden){resumeUpload();if(S.up&&uploadBusy)uploadAwake();}});

/* several parts are put in recording order (the MP4/MOV 'mvhd' creation time, else the file
   time); the user can move them up or down before sending. Only small header slices are read. */
async function videoCreated(file) {
  try {
    const word = (view, at) => String.fromCharCode(view.getUint8(at), view.getUint8(at+1), view.getUint8(at+2), view.getUint8(at+3));
    let pos = 0;
    for (let guard = 0; guard < 64 && pos + 8 <= file.size; guard++) {
      const head = new DataView(await file.slice(pos, pos + 16).arrayBuffer());
      let size = head.getUint32(0), skip = 8;
      if (size === 1) { size = head.getUint32(8) * 4294967296 + head.getUint32(12); skip = 16; }
      else if (size === 0) size = file.size - pos;
      if (size < skip) return null;
      if (word(head, 4) === "moov") {
        const body = new DataView(await file.slice(pos + skip, pos + Math.min(size, skip + 65536)).arrayBuffer());
        for (let at = 0; at + 20 <= body.byteLength;) {
          const length = body.getUint32(at);
          if (word(body, at + 4) === "mvhd") {
            const seconds = body.getUint8(at + 8) === 1 ? body.getUint32(at + 12) * 4294967296 + body.getUint32(at + 16) : body.getUint32(at + 12);
            const ms = (seconds - 2082844800) * 1000;   // QuickTime epoch is 1904-01-01 UTC
            return seconds && ms > Date.UTC(2000, 0, 1) && ms < Date.now() + 86400000 ? ms : null;
          }
          if (length < 8) return null;
          at += length;
        }
        return null;
      }
      pos += size;
    }
  } catch (_) {}
  return null;
}
async function pickUploadFiles(list) {
  const chosen = [...(list || [])], seq = ++uploadSequence;
  const items = await Promise.all(chosen.map(async file => ({file, at: await videoCreated(file), mod: file.lastModified || 0})));
  if (seq !== uploadSequence) return;
  items.sort((a, b) => ((a.at ?? a.mod) - (b.at ?? b.mod)) || byName(a.file, b.file));
  const saved = uploadIntent && uploadIntent.body && uploadIntent.body.ordered ? uploadIntent.body.files : null;
  const key = f => `${f.name}\u0000${f.size}`;
  if (saved && saved.length === items.length && items.every(i => saved.some(f => key(f) === key(i.file)))) {
    items.sort((a, b) => saved.findIndex(f => key(f) === key(a.file)) - saved.findIndex(f => key(f) === key(b.file)));
  }
  S.upOrder = items; renderUploadOrder();
}
function renderUploadOrder() {
  const box = $("#up-files-list"), items = S.upOrder || [];
  if (!box) return;
  const when = item => item.at ? `${i18nText("ui.upload.recorded")} ${new Date(item.at).toLocaleString((I18N.language === "hy" ? "hy-AM" : "en-US"), {day:"numeric", month:"short", hour:"2-digit", minute:"2-digit"})}`
    : item.mod ? `${i18nText("ui.upload.file_time")} ${new Date(item.mod).toLocaleString((I18N.language === "hy" ? "hy-AM" : "en-US"), {day:"numeric", month:"short", hour:"2-digit", minute:"2-digit"})}` : "";
  box.innerHTML = items.length ? `${items.length > 1 ? (`<p class="muted">${i18nText("ui.upload.sorted_by_recording_time_use_to_change")}</p>`) : ""}<ol class="up-order">${items.map((item, i) => `<li><span class="up-order-n">${i + 1}</span><span class="grow"><b>${esc(item.file.name)}</b><small class="muted">${fmtSize(item.file.size)}${when(item) ? " · " + when(item) : ""}</small></span>${items.length > 1 ? `<button type="button" class="icon-btn" data-up-move="${i}" data-dir="-1" aria-label="${i18nText("ui.upload.move_up")}" ${i === 0 ? "disabled" : ""}>↑</button><button type="button" class="icon-btn" data-up-move="${i}" data-dir="1" aria-label="${i18nText("ui.upload.move_down")}" ${i === items.length - 1 ? "disabled" : ""}>↓</button>` : ""}</li>`).join("")}</ol>` : "";
}
document.addEventListener("click", e => {
  const button = e.target.closest && e.target.closest("[data-up-move]");
  if (!button || !S.upOrder || S.up) return;
  const from = Number(button.dataset.upMove), to = from + Number(button.dataset.dir);
  if (to < 0 || to >= S.upOrder.length) return;
  const items = S.upOrder.slice(); [items[from], items[to]] = [items[to], items[from]];
  S.upOrder = items; renderUploadOrder();
  const again = $(`[data-up-move="${to}"][data-dir="${button.dataset.dir}"]`) || $(`[data-up-move="${to}"]`); if (again) again.focus();
});
