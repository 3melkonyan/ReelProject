"use strict";
/* Lesson recording in the phone's browser.
   Each part streams while it is recorded: MediaRecorder timeslice chunks are stored in IndexedDB,
   then sent in order through the existing resumable upload (PUT /api/uploads/<id>/<part>?offset=N).
   A dropped connection only delays sending. The session, its parts and the first server calls are
   all created lazily, so recording can start or continue even while the network is down.
   An interruption (screen lock, app switch, call, camera loss) closes the part; the lecturer
   continues with a new part, may delete any part, then confirms and the lesson goes to processing. */
const REC_KEY = "reel-record-v1", REC_IDB = "reel-record", REC_SLICE = 2000, REC_PIECE = 4 * 1024 * 1024;
const REC_TYPES = ["video/mp4;codecs=avc1.640028,mp4a.40.2", "video/mp4;codecs=avc1,mp4a", "video/mp4",
  "video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"];
const R = { s: null, stream: null, rec: null, part: null, watch: null, wake: null, pumping: false, retry: null,
  chain: Promise.resolve(), mem: new Map(), idb: null, offline: false, error: "", done: null, defaults: null,
  groups: null, busy: false, recovered: false, seq: 0 };

/* ---------- local persistence: session in localStorage, media chunks in IndexedDB ---------- */
function recSave() { try { if (R.s) localStorage.setItem(REC_KEY, JSON.stringify(R.s)); else localStorage.removeItem(REC_KEY); } catch (_) {} }
function recLoad() { try { return JSON.parse(localStorage.getItem(REC_KEY) || "null"); } catch (_) { return null; } }
const recKey = (local, n, offset) => `${local}:${String(n).padStart(3, "0")}:${String(offset).padStart(13, "0")}`;
function recIdb() {
  if (!R.idb) R.idb = new Promise((resolve) => {
    try {
      const req = indexedDB.open(REC_IDB, 1);
      req.onupgradeneeded = () => req.result.createObjectStore("chunks", { keyPath: "key" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = req.onblocked = () => resolve(null);
    } catch (_) { resolve(null); }
  });
  return R.idb;
}
function recTx(mode, work) {
  return recIdb().then((db) => db && new Promise((resolve) => {
    try {
      const tx = db.transaction("chunks", mode), out = work(tx.objectStore("chunks"));
      tx.oncomplete = () => resolve(out && "result" in out ? out.result : true);
      tx.onerror = tx.onabort = () => resolve(null);
    } catch (_) { resolve(null); }
  }));
}
async function recPut(chunk) {   // quota/private mode: bounded memory fallback, never promise persistence
  if (!(await recTx("readwrite", (store) => { store.put(chunk); }))) {
    R.mem.set(chunk.key, chunk);
    const warning=i18nText("ui.record.local_storage_is_unavailable_in_this_browser");
    R.notice=warning;
    const bytes=[...R.mem.values()].reduce((sum,c)=>sum+(c.data.byteLength||0),0);
    if(bytes>=64*1024*1024 && R.rec) setTimeout(()=>{
      recStop(true);R.notice=warning+(` ${i18nText("ui.record.recording_stopped_to_avoid_filling_your_phone")}`);recRender();
    },0);
  }
}
async function recDel(key) { R.mem.delete(key); await recTx("readwrite", (store) => { store.delete(key); }); }
function recRange(local) { try { return IDBKeyRange.bound(local + ":", local + ":￿"); } catch (_) { return null; } }
async function recScan(local, keep) {
  const found = [...R.mem.values()].filter((c) => c.local === local && keep(c));
  const range = recRange(local);
  if (range) await recTx("readonly", (store) => {
    const req = store.openCursor(range);
    req.onsuccess = () => { const cur = req.result; if (cur) { if (keep(cur.value)) found.push(cur.value); cur.continue(); } };
  });
  return found;
}
async function recFirst(local) {
  let best = null;
  for (const c of R.mem.values()) if (c.local === local && (!best || c.key < best.key)) best = c;
  const range = recRange(local);
  if (range) {
    const first = await recTx("readonly", (store) => store.openCursor(range));
    const value = first && first.value;
    if (value && (!best || value.key < best.key)) best = value;
  }
  return best;
}
async function recDrop(local, n) { for (const c of await recScan(local, (c) => n == null || c.n === n)) await recDel(c.key); }
async function recHas(local, n) {
  const prefix = `${local}:${String(n).padStart(3, "0")}:`;
  if ([...R.mem.keys()].some((k) => k.startsWith(prefix))) return true;
  let range = null; try { range = IDBKeyRange.bound(prefix, prefix + "\uffff"); } catch (_) {}
  return Boolean(range && await recTx("readonly", (store) => store.openCursor(range)));
}

/* ---------- server calls ---------- */
async function recCall(method, path, body, raw) {
  const ctl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), 120000) : null;
  let r;
  try {
    r = await fetch(path, { method, credentials: "same-origin", signal: ctl ? ctl.signal : undefined,
      headers: { "X-Requested-With": "reel", "Content-Type": raw ? "application/octet-stream" : "application/json" },
      body: raw || (body === undefined ? undefined : JSON.stringify(body)) });
  } catch (_) { const e = new Error(i18nText("ui.record.no_connection")); e.net = true; throw e; }
  finally { if (timer) clearTimeout(timer); }
  if (r.status >= 500 && r.status !== 507) { const e = new Error(i18nText("ui.record.server_unavailable")); e.net = true; throw e; }
  let data = {}; try { data = await r.json(); } catch (_) {}
  return { status: r.status, data };
}
function recFail(r) {
  const e = new Error(r.status === 401 ? i18nText("ui.record.your_session_has_expired_sign_in_again")
    : r.status === 404 ? i18nText("ui.record.recording_not_found_on_the_server_you")
    : String(r.data.detail || `${i18nText("ui.record.the_server_rejected_the_request")}${r.status}${i18nText("ui.lecturer.punctuation")}`));
  e.status = r.status; return e;
}

/* ---------- the sender: create session/parts, send chunks in order, close and delete parts ---------- */
async function recPump() {
  const s = R.s;
  if (R.pumping || !s || R.done) return;
  R.pumping = true; clearTimeout(R.retry); R.retry = null;
  try {
    while (R.s === s) {
      if (!s.id) {
        const r = await recCall("POST", "/api/recordings", { group: s.group, subject: s.subject, lesson_number: s.lesson_number, mime: s.mime });
        if (r.status !== 200) throw recFail(r);
        s.id = r.data.id; s.lesson_date = r.data.lesson_date; recSave(); continue;
      }
      const fresh = s.parts.find((p) => p.index == null && !["deleted", "deleting"].includes(p.state));
      if (fresh) {
        const r = await recCall("POST", `/api/recordings/${s.id}/parts`);
        if (r.status !== 200) throw recFail(r);
        fresh.index = r.data.index; recSave(); continue;
      }
      const gone = s.parts.find((p) => p.state === "deleting");
      if (gone) {
        if (gone.index != null) {
          const r = await recCall("DELETE", `/api/recordings/${s.id}/parts/${gone.index}`);
          if (r.status !== 200 && r.status !== 404) throw recFail(r);
        }
        gone.state = "deleted"; await recDrop(s.local, gone.n); recSave(); continue;
      }
      let closable = null;   // close finished parts right away, even while a later part records
      for (const p of s.parts) if (p.state === "closing" && p.index != null && !(await recHas(s.local, p.n))) { closable = p; break; }
      if (closable) {
        const r = await recCall("POST", `/api/recordings/${s.id}/parts/${closable.index}/close`,
          { size: closable.bytes, duration: closable.duration, interrupted: Boolean(closable.interrupted) });
        if (r.status === 409 && typeof r.data.received === "number" && r.data.received < closable.bytes) {
          closable.bytes = r.data.received; closable.lost = true; recSave(); continue;
        }
        if (r.status !== 200) throw recFail(r);
        closable.state = closable.bytes ? "closed" : "deleted"; closable.sent = closable.bytes; recSave(); continue;
      }
      const chunk = await recFirst(s.local);
      if (chunk) {
        const part = s.parts.find((p) => p.n === chunk.n);
        if (!part || part.index == null || ["deleted", "deleting"].includes(part.state)) { await recDel(chunk.key); continue; }
        const r = await recCall("PUT", `/api/uploads/${s.id}/${part.index}?offset=${chunk.offset}`, undefined, chunk.data);
        if (r.status === 410) { part.state = "deleted"; await recDrop(s.local, part.n); recSave(); continue; }
        if ((r.status !== 200 && r.status !== 409) || typeof r.data.received !== "number") throw recFail(r);
        const got = r.data.received, end = chunk.offset + chunk.data.byteLength;
        if (got === chunk.offset) { const e = new Error(i18nText("ui.record.no_connection")); e.net = true; throw e; }   // nothing written: retry
        await recDel(chunk.key);
        if (got < end && got > chunk.offset) {          // the server kept the head of this chunk
          await recPut({ ...chunk, key: recKey(s.local, chunk.n, got), offset: got, data: chunk.data.slice(got - chunk.offset) });
        } else if (got < chunk.offset) {                 // earlier bytes never arrived: keep what the server has
          part.lost = true; await recDrop(s.local, part.n);
          if (R.part === part) recStop(true);
          part.bytes = got;
        }
        part.sent = Math.max(part.sent || 0, Math.min(got, part.bytes)); R.offline = false; recSave(); recRender(); continue;
      }
      break;
    }
    R.offline = false; R.error = "";
  } catch (e) {
    if (e.net) { R.offline = true; R.retry = setTimeout(recPump, 5000); }
    else R.error = e.message;
  } finally { R.pumping = false; recWakeSync(); recRender(); }
}

/* ---------- camera and recorder ---------- */
function recMime() {
  if (typeof MediaRecorder === "undefined") return "";
  for (const type of REC_TYPES) { try { if (MediaRecorder.isTypeSupported(type)) return type; } catch (_) {} }
  return "";
}
function recCameraError(e) {
  const name = e && e.name;
  if (name === "NotAllowedError" || name === "SecurityError") return i18nText("ui.record.allow_camera_and_microphone_access_in_your");
  if (name === "NotFoundError" || name === "OverconstrainedError") return i18nText("ui.record.no_camera_or_microphone_found");
  if (name === "NotReadableError") return i18nText("ui.record.another_app_is_using_the_camera_close");
  return i18nText("ui.record.could_not_turn_on_the_camera");
}
async function recCamera() {
  if (R.stream && R.stream.getVideoTracks().some((t) => t.readyState === "live")) return R.stream;
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 30, max: 30 } },
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  stream.getTracks().forEach((t) => t.addEventListener("ended", () => { if (R.rec && R.stream === stream) recStop(true); }));
  R.stream = stream;
  const video = $("#rec-preview"); if (video) { video.srcObject = stream; video.play().catch(() => {}); }
  return stream;
}
function recCameraOff() {
  if (R.stream) R.stream.getTracks().forEach((t) => t.stop());
  R.stream = null; const video = $("#rec-preview"); if (video) video.srcObject = null;
}
function recBlobBytes(blob) {
  if (blob.arrayBuffer) return blob.arrayBuffer();
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(reader.error); reader.readAsArrayBuffer(blob); });
}
async function recStore(part, blob) {
  const s = R.s; if (!s) return;
  for (let at = 0; at < blob.size; at += REC_PIECE) {
    const data = await recBlobBytes(blob.slice(at, Math.min(blob.size, at + REC_PIECE)));
    if (R.s !== s || part.lost || ["deleted", "deleting"].includes(part.state)) return;
    await recPut({ key: recKey(s.local, part.n, part.bytes), local: s.local, n: part.n, offset: part.bytes, data });
    part.bytes += data.byteLength;
  }
  part.last = Date.now(); recSave(); recPump(); recRender();
}
function recClosePart(part) {
  if (part.state !== "recording") return;
  part.state = "closing"; recSave(); recPump(); recRender();
}
async function recStartPart() {
  if (R.rec || R.busy || R.done) return;
  R.error = "";
  try { await recCamera(); } catch (e) { R.error = recCameraError(e); recRender(); return; }
  if (!R.s) {
    const group = $("#rec-group").value, subject = $("#rec-subject").value, number = Number($("#rec-number").value);
    const mime = recMime();
    if (!group || !subject) { toast(i18nText("ui.record.choose_a_group_and_subject")); return; }
    if (!Number.isInteger(number) || number < 1 || number > 999) { toast(i18nText("recording.the_lesson_number_must_be_between_1")); return; }
    if (!mime) { R.error = i18nText("ui.record.this_browser_cannot_record_in_mp4_or"); recRender(); return; }
    R.s = { local: "L" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8), id: null, group, subject,
      lesson_number: number, lesson_date: R.defaults ? R.defaults.date : null, mime, parts: [], created: Date.now() };
  }
  if (R.s.parts.filter((p) => p.state !== "deleted").length >= 30) { toast(i18nText("ui.record.a_lesson_can_contain_up_to_30")); return; }
  let rec;
  try { rec = new MediaRecorder(R.stream, { mimeType: R.s.mime, videoBitsPerSecond: 4000000, audioBitsPerSecond: 128000 }); }
  catch (_) { try { rec = new MediaRecorder(R.stream); } catch (e) { R.error = i18nText("ui.record.could_not_start_recording"); recRender(); return; } }
  const part = { n: R.s.parts.reduce((m, p) => Math.max(m, p.n), 0) + 1, index: null, bytes: 0, sent: 0,
    state: "recording", started: Date.now(), last: Date.now(), duration: 0, interrupted: false };
  rec.ondataavailable = (e) => { if (e.data && e.data.size) R.chain = R.chain.then(() => recStore(part, e.data)).catch(() => { R.error = i18nText("ui.record.could_not_save_the_video"); }); };
  rec.onstop = () => { R.chain = R.chain.then(() => recClosePart(part)); };
  rec.onerror = () => { if (R.rec === rec) recStop(true); };
  try { rec.start(REC_SLICE); } catch (_) { R.error = i18nText("ui.record.could_not_start_recording"); recRender(); return; }
  R.s.parts.push(part); R.rec = rec; R.part = part; recSave();
  R.watch = setInterval(() => {   // some browsers ignore the timeslice: ask for the data ourselves
    if (R.rec === rec && Date.now() - part.last > 3 * REC_SLICE) { try { rec.requestData(); } catch (_) {} }
    const clockEl = $("#rec-clock"); if (clockEl) clockEl.textContent = clock((Date.now() - part.started) / 1000);
  }, 1000);
  recWakeSync(); recPump(); recRender();
}
function recStop(interrupted) {
  const rec = R.rec, part = R.part;
  if (!rec) return;
  R.rec = null; R.part = null; clearInterval(R.watch);
  part.duration = Math.max(0, (Date.now() - part.started) / 1000); part.interrupted = Boolean(interrupted); recSave();
  let stopping = false;
  try { if (rec.state !== "inactive") { rec.stop(); stopping = true; } } catch (_) {}
  if (!stopping) R.chain = R.chain.then(() => recClosePart(part));
  if (interrupted) R.notice = `${i18nText("ui.record.recording_interrupted_part")} ${recNumber(part)}${i18nText("ui.record.was_saved_you_can_continue_with_a")}`;
  recWakeSync(); recRender();
}

/* ---------- wake lock: the screen stays on while recording or sending ---------- */
async function recWakeSync() {
  const want = Boolean(R.rec || (R.s && recPendingBytes() > 0)) && !document.hidden;
  if (!want) { const lock = R.wake; R.wake = null; if (lock) lock.release().catch(() => {}); return; }
  if (R.wake || R.wakePending || !navigator.wakeLock) return;
  try {
    R.wakePending = true;
    const lock = await navigator.wakeLock.request("screen");
    R.wake = lock; lock.addEventListener("release", () => { if (R.wake === lock) R.wake = null; recRender(); });
  } catch (_) {} finally { R.wakePending = false; recRender(); }
}

/* ---------- actions ---------- */
const recLive = () => (R.s ? R.s.parts.filter((p) => !["deleted", "deleting"].includes(p.state)) : []);
const recNumber = (part) => recLive().indexOf(part) + 1 || part.n;
const recPendingBytes = () => recLive().reduce((sum, p) => sum + Math.max(0, p.bytes - (p.sent || 0)), 0);
function recReady() {
  const live = recLive();
  return Boolean(R.s && R.s.id && !R.rec && live.length && live.every((p) => p.state === "closed" && p.index != null)
    && !R.s.parts.some((p) => p.state === "deleting"));
}
async function recDelete(n) {
  const part = R.s && R.s.parts.find((p) => p.n === n);
  if (!part || part === R.part) return;
  if (!confirm(`${i18nText("ui.record.delete_part")} ${recNumber(part)}${i18nText("ui.record.it_will_not_be_included_in_the")}`)) return;
  part.state = "deleting"; recSave(); await recDrop(R.s.local, part.n); recPump(); recRender();
}
async function recConfirm() {
  const s = R.s;
  if (!recReady() || R.busy) return;
  if (!confirm(`${i18nText("ui.record.submit_this_lesson_for_processing")}${recLive().length} ${i18nText("ui.record.parts_you_cannot_change_the_parts_afterward")}`)) return;
  R.busy = true; recRender();
  try {
    const st = await recCall("GET", `/api/recordings/${s.id}`);
    if (st.status !== 200) throw recFail(st);
    if (st.data.completed) return await recFinished(st.data.result);
    const known = new Set(recLive().map((p) => p.index));
    for (const extra of st.data.parts) {   // a part the server made when its reply was lost
      if (known.has(extra.index)) continue;
      const r = await recCall("DELETE", `/api/recordings/${s.id}/parts/${extra.index}`);
      if (r.status !== 200) throw recFail(r);
    }
    const r = await recCall("POST", `/api/recordings/${s.id}/confirm`);
    if (r.status !== 200) throw recFail(r);
    await recFinished(r.data);
  } catch (e) { toast(e.net ? i18nText("ui.record.no_connection_try_again_shortly") : e.message); }
  finally { R.busy = false; recRender(); }
}
async function recFinished(result) {
  const s = R.s;
  R.done = result; R.s = null; recSave(); recCameraOff(); recWakeSync();
  if (s) await recDrop(s.local);
  S.last = {}; S.summary = null; toast(i18nText("ui.record.lesson_submitted_for_processing"));
}
async function recCancel() {
  if (!R.s || R.busy) return;
  if (!confirm(i18nText("ui.record.cancel_the_entire_recording_all_parts_will"))) return;
  const s = R.s; R.busy = true;
  if (R.rec) { const rec = R.rec; R.rec = null; R.part = null; clearInterval(R.watch); rec.onstop = null; rec.ondataavailable = null; try { rec.stop(); } catch (_) {} }
  try { if (s.id) await recCall("DELETE", `/api/recordings/${s.id}`); }
  catch (_) {}   // offline: the server deletes abandoned uploads after 3 days
  await recDrop(s.local);
  R.s = null; R.error = ""; R.notice = ""; R.busy = false; recSave(); recWakeSync(); recRender();
  if ($("#rec-group")) recDefaults();
}
async function recRecover() {
  if (R.recovered) return;
  R.recovered = true;
  const saved = recLoad();
  if (!saved || !saved.local || !Array.isArray(saved.parts)) return;
  const chunks = await recScan(saved.local, () => true);
  for (const p of saved.parts) {
    p.bytes = Math.max(p.bytes || 0, ...chunks.filter((c) => c.n === p.n).map((c) => c.offset + c.data.byteLength));
    if (p.state === "recording") {   // the page closed while this part was recording
      p.state = "closing"; p.interrupted = true; p.duration = Math.max(0, ((p.last || p.started) - p.started) / 1000);
    }
  }
  R.s = saved; recSave();
  if (saved.id) {
    try {
      const st = await recCall("GET", `/api/recordings/${saved.id}`);
      if (st.status === 200 && st.data.completed) { await recFinished(st.data.result); return; }
      if (st.status === 200) for (const sp of st.data.parts) { const p = saved.parts.find((x) => x.index === sp.index); if (p) p.sent = Math.min(p.bytes, sp.received); }
      else R.error = recFail(st).message;
    } catch (_) { R.offline = true; }
  }
  recPump(); recRender();
}

/* ---------- view ---------- */
function recInsecure(secure) {
  if ($("#rec-https")) return;
  set("#main", `<div id="rec-https"><div class="top"><div><span class="eyebrow">REEL</span><h1>${i18nText("ui.record.record_lesson")}</h1></div></div>
    <section class="card rec-card"><div class="alert warn">${ic("shield")}<div>
    ${secure ? `<b>${i18nText("ui.record.this_browser_cannot_record_from_the_page")}</b><p>${i18nText("ui.record.use_safari_on_iphone_ios_14_3")}</p>`
      : `<b>${i18nText("ui.record.recording_requires_https")}</b><p>${i18nText("ui.record.the_browser_allows_camera_and_microphone_access")} <span class="mono">${esc(location.origin)}</span> ${i18nText("ui.record.so_recording_is_unavailable_here")}</p>
      <p>${i18nText("ui.record.what_you_can_do")}</p><ol><li>${i18nText("ui.record.open_the_dashboard_over_https_using_the")}</li><li>${i18nText("ui.record.or_record_with_your_phone_s_camera")}</li></ol>`}
    </div></div><div class="actions"><a class="btn primary" id="rec-https-link" hidden>${ic("shield")} ${i18nText("ui.record.open_secure_url")}</a><a class="btn" href="#/upload">${ic("upload")} ${i18nText("ui.app.upload_lesson")}</a></div></section></div>`);
  if (!secure) api("/api/recordings/info").then((info) => {
    const link = $("#rec-https-link");
    if (link && info.public_url) { link.href = info.public_url + "/#/record"; link.hidden = false; }
  }).catch(() => {});
}
async function viewRecord() {
  if (!canUpload()) { set("#main", (`<div class="alert err">${i18nText("ui.record.you_do_not_have_permission_to_record")}</div>`)); return; }
  const secure = Boolean(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  if (!secure || typeof MediaRecorder === "undefined") { recInsecure(secure); return; }
  if ($("#rec-view")) { recRender(); return; }
  set("#main", `<div id="rec-view"><div class="top"><div><span class="eyebrow">REEL</span><h1>${i18nText("ui.record.record_lesson")}</h1><p>${i18nText("ui.record.record_on_your_phone_in_parts_video")}</p></div></div>
    <section class="card rec-card">
      <form id="rec-setup" class="grid upload-fields" onsubmit="return false">
        <label class="fld"><span>${i18nText("ui.accounts.group")}</span><select class="input" id="rec-group" required></select></label>
        <label class="fld"><span>${i18nText("ui.app.subject")}</span><select class="input" id="rec-subject" required></select><small class="muted" id="rec-subject-hint"></small></label>
        <label class="fld"><span>${i18nText("ui.record.lesson_number")}</span><input class="input" id="rec-number" type="number" inputmode="numeric" min="1" max="999" required></label>
        <p class="muted rec-date" id="rec-date"></p>
      </form>
      <p class="rec-summary" id="rec-summary" hidden></p>
      <div class="rec-stage"><video id="rec-preview" playsinline muted autoplay></video>
        <div class="rec-badge" id="rec-badge" hidden><span class="rec-dot"></span><span id="rec-clock">0:00</span><span id="rec-part-label"></span></div>
        <div class="rec-placeholder" id="rec-placeholder">${ic("play")}<span>${i18nText("ui.record.camera_off")}</span></div></div>
      <p class="muted rec-orient" id="rec-orient" hidden>${i18nText("ui.record.hold_your_phone_horizontally_to_keep_the")}</p>
      <div class="rec-controls" id="rec-controls"></div>
      <div id="rec-status" role="status" aria-live="polite"></div>
      <ol class="rec-parts" id="rec-parts"></ol>
      <div class="rec-actions" id="rec-actions"></div>
      <div class="upload-tip"><b>${i18nText("ui.app.how_it_works")}</b><p>${i18nText("ui.record.each_part_uploads_while_you_record_if")}</p></div>
    </section></div>`);
  R.done = null;
  await recRecover();
  await recLoadGroups();
  recRender();
}
async function recLoadGroups() {
  const select = $("#rec-group"); if (!select) return;
  const data = await api("/api/groups");
  if (!Array.isArray(data.groups)) { R.error = i18nText("ui.record.could_not_load_groups"); return; }
  R.groups = data.groups;
  select.innerHTML = data.groups.map((g) => `<option value="${esc(g.name)}">${esc(g.name)}</option>`).join("");
  if (!data.groups.length) { R.error = i18nText("ui.record.no_group_is_assigned_to_you_yet"); return; }
  select.onchange = () => recDefaults();
  $("#rec-subject").onchange = () => recDefaults($("#rec-subject").value);
  await recDefaults();
}
async function recDefaults(subject) {
  const group = $("#rec-group"), select = $("#rec-subject"), number = $("#rec-number");
  if (!group || !group.value) return;
  const seq = ++R.seq; number.disabled = true;
  try {
    const d = await api(`/api/recordings/defaults?group=${encodeURIComponent(group.value)}${subject ? "&subject=" + encodeURIComponent(subject) : ""}`);
    if (seq !== R.seq || !$("#rec-subject")) return;
    R.defaults = d;
    select.innerHTML = d.subjects.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
    select.value = d.subject; number.value = d.number;
    $("#rec-subject-hint").textContent = d.subject === d.current ? i18nText("ui.record.current_subject_from_the_schedule") : "";
    $("#rec-date").textContent = `${i18nText("ui.record.date")} ${formatLessonDate(d.date)} ${i18nText("ui.record.today")}`;
  } catch (e) { if (e.message !== "auth") toast(e.message || i18nText("ui.record.could_not_load_defaults")); }
  finally { if (seq === R.seq && number) number.disabled = false; }
}
function recPartState(p) {
  if (p === R.part) return `<span class="rec-live">${i18nText("ui.record.recording")}</span>`;
  if (p.state === "closed") return `${ic("check")} ${i18nText("ui.record.uploaded")}`;
  const pct = p.bytes ? Math.floor(100 * (p.sent || 0) / p.bytes) : 0;
  return R.offline ? `${i18nText("ui.record.saved_on_phone")} ${pct}%` : `${i18nText("ui.record.uploading")} ${pct}%`;
}
function recRender() {
  const root = $("#rec-view");
  if (!root) return;
  const s = R.s, live = recLive(), pending = recPendingBytes();
  if(R.done && !$("#record-lecturer-review") && typeof pollUploadLecturerReview==='function' && root.insertAdjacentHTML){
    root.insertAdjacentHTML('beforeend','<div id="record-lecturer-review"></div>');
    pollUploadLecturerReview(R.done.job_id||R.done.duplicate_of,'#record-lecturer-review');
  }
  $("#rec-setup").hidden = Boolean(s || R.done);
  const summary = $("#rec-summary");
  summary.hidden = !s;
  if (s) summary.textContent = `${s.group} · ${s.subject} ${i18nText("ui.record.lesson")} ${s.lesson_number} · ${formatLessonDate(s.lesson_date || (R.defaults && R.defaults.date))}`;
  const recording = Boolean(R.rec);
  $("#rec-badge").hidden = !recording;
  if (recording) $("#rec-part-label").textContent = ` ${i18nText("ui.record.part")} ${recNumber(R.part)}`;
  $("#rec-placeholder").hidden = Boolean(R.stream) || Boolean(R.done);
  const portrait = typeof matchMedia === "function" && matchMedia("(orientation: portrait)").matches;
  $("#rec-orient").hidden = !R.stream || !portrait;
  const controls = R.done ? "" : recording
    ? `<button class="btn danger rec-stop" id="rec-stop" type="button">${i18nText("ui.record.stop_part")}</button>`
    : `<button class="btn primary rec-start" id="rec-start" type="button" ${R.busy ? "disabled" : ""}>● ${live.length ? i18nText("ui.record.record_next_part") : i18nText("ui.record.start_recording")}</button>`;
  recSet("#rec-controls", controls);
  let status = "";
  if (R.done) {
    const job = R.done.job_id || R.done.duplicate_of;
    status = `<div class="alert ok">${ic("check")}<div>${i18nText("ui.record.lesson_submitted_for_processing_2")} ${R.done.duplicate_of ? (`${i18nText("ui.record.this_video_already_exists")} `) : ""}<a href="#/jobs/${esc(job)}">${i18nText("ui.record.open_lesson")}</a></div></div>
      <button class="btn" id="rec-new" type="button">${i18nText("ui.record.record_new_lesson")}</button>`;
  } else {
    const lines = [];
    if (R.error) lines.push(`<div class="alert err" role="alert">${ic("alert")}<div>${esc(R.error)}${s ? (` <button class="btn sm" id="rec-retry" type="button">${i18nText("ui.record.try_again")}</button>`) : ""}</div></div>`);
    if (R.notice) lines.push(`<div class="alert warn">${ic("alert")}<div>${esc(R.notice)}</div></div>`);
    if (R.offline && s) lines.push(`<p class="rec-line warn-text">${ic("alert")} ${i18nText("ui.record.no_connection_video_is_saved_on_this")}</p>`);
    if (recording) lines.push(`<p class="rec-line">${R.wake ? i18nText("ui.record.the_screen_is_staying_awake") : i18nText("ui.record.keep_the_screen_on_turning_it_off")}</p>`);
    if (s && pending > 0 && !R.offline) lines.push(`<p class="rec-line muted">${i18nText("ui.record.remaining_to_upload")} ${fmtSize(pending)}</p>`);
    if (s && !recording && live.length && !pending && recReady()) lines.push(`<p class="rec-line">${ic("check")} ${i18nText("ui.record.all_parts_uploaded_record_another_part_or")}</p>`);
    status = lines.join("");
  }
  recSet("#rec-status", status);
  recSet("#rec-parts", R.done ? "" : live.map((p) => `<li class="rec-part"><div class="grow"><b>${i18nText("ui.record.part_2")} ${recNumber(p)}</b> · ${p === R.part ? "…" : clock(p.duration || 0)} <span class="muted">· ${fmtSize(p.bytes)}</span>${p.interrupted ? (` <span class="muted">${i18nText("ui.record.interrupted")}</span>`) : ""}${p.lost ? (` <span class="err-text">${i18nText("ui.record.some_footage_lost")}</span>`) : ""}<div class="muted rec-part-state">${recPartState(p)}</div></div>${p === R.part ? "" : `<button class="btn sm danger" type="button" data-rec-del="${p.n}" ${R.busy ? "disabled" : ""}>${i18nText("ui.record.delete")}</button>`}</li>`).join(""));
  recSet("#rec-actions", !s || R.done ? "" : `<button class="btn primary" id="rec-confirm" type="button" ${recReady() && !R.busy ? "" : "disabled"}>${ic("check")} ${i18nText("ui.record.confirm_and_submit_for_processing")}</button>
    <button class="btn danger" id="rec-cancel" type="button" ${R.busy ? "disabled" : ""}>${i18nText("ui.record.cancel_all")}</button>`);
}
function recSet(sel, html) { const el = $(sel); if (el && el.dataset.html !== html) { el.dataset.html = html; el.innerHTML = html; } }

/* ---------- events ---------- */
document.addEventListener("click", (e) => {
  const t = e.target.closest("#rec-start,#rec-stop,#rec-confirm,#rec-cancel,#rec-retry,#rec-new,[data-rec-del]");
  if (!t) return;
  if (t.id === "rec-start") recStartPart();
  else if (t.id === "rec-stop") { R.notice = ""; recStop(false); }
  else if (t.id === "rec-confirm") recConfirm();
  else if (t.id === "rec-cancel") recCancel();
  else if (t.id === "rec-retry") { R.error = ""; recPump(); }
  else if (t.id === "rec-new") { R.done = null; R.notice = ""; recRender(); recDefaults(); }
  else if (t.dataset.recDel) recDelete(Number(t.dataset.recDel));
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { if (R.rec) recStop(true); }
  else { recWakeSync(); recPump(); }
});
window.addEventListener("pagehide", () => { if (R.rec) recStop(true); });
window.addEventListener("online", () => recPump());
window.addEventListener("hashchange", () => {
  if (location.hash.startsWith("#/record")) return;
  if (R.rec) { recStop(false); toast(i18nText("ui.record.recording_stopped_because_you_left_the_page")); }
  recCameraOff();
});
window.addEventListener("beforeunload", (e) => { if (R.rec || (R.s && recPendingBytes() > 0)) { e.preventDefault(); e.returnValue = ""; } });
if (typeof matchMedia === "function") { try { matchMedia("(orientation: portrait)").addEventListener("change", () => recRender()); } catch (_) {} }
