"use strict";
/* search inside lessons, lesson player, watch statistics, e-mail preferences. */

/* ---------- 2. search inside lessons (speech + board/slide text) ---------- */
const HIT_SOURCE = {speech: "Խոսք", board: "Գրատախտակ", slide: "Սլայդ"};
let libraryTextTimer = null;
function lessonLink(lesson, seconds) {
  return `${lesson.url}?t=${Math.max(0, Math.floor(seconds || 0))}`;
}
function markTerms(text, terms) {
  const source = String(text || ""), lower = source.toLocaleLowerCase(), ranges = [];
  for (const term of terms || []) {
    for (let at = term ? lower.indexOf(term) : -1; at >= 0; at = lower.indexOf(term, at + term.length)) ranges.push([at, at + term.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  let html = "", done = 0;
  for (const [start, end] of ranges) {
    if (start < done) continue;
    html += esc(source.slice(done, start)) + "<mark>" + esc(source.slice(start, end)) + "</mark>";
    done = end;
  }
  return html + esc(source.slice(done));
}
function scheduleLibraryTextSearch() {
  clearTimeout(libraryTextTimer);
  libraryTextTimer = setTimeout(runLibraryTextSearch, 350);
}
async function runLibraryTextSearch() {
  const box = $("#library-text-results"), input = $("#library-search");
  if (!box || !input) return;
  const query = input.value.trim();
  if (query.length < 2) {S.libraryText = null; box.innerHTML = ""; return;}
  box.innerHTML = '<p class="muted" role="status">Որոնում ենք դասերի տեքստում…</p>';
  try {
    const data = await api("/api/library/search?q=" + encodeURIComponent(query));
    if ($("#library-search")?.value.trim() !== query) return;          // a newer query is already running
    S.libraryText = {...data, query};
    renderLibraryText();
  } catch (error) {
    if (error.message !== "auth" && box.isConnected) box.textContent = "Որոնումը չհաջողվեց։ Փորձեք կրկին։";
  }
}
function renderLibraryText() {
  const box = $("#library-text-results"), data = S.libraryText;
  if (!box || !data) return;
  const group = $("#library-group")?.value || "", subject = $("#library-subject")?.value || "";
  const lessons = new Map((S.library || []).map(x => [x.job_id, x]));
  const list = (data.results || []).filter(r => {
    const x = lessons.get(r.job_id);
    return x && isPlayableLesson(x) && (!group || x.group === group) && (!subject || x.subject === subject);
  });
  if (!list.length) {
    box.innerHTML = `<p class="muted" role="status">Դասերի տեքստում «${esc(data.query)}» չի գտնվել։</p>`;
    return;
  }
  box.innerHTML = `<section class="card library-text" aria-label="Որոնման արդյունքները դասերի տեքստում"><h2>Գտնվել է դասերի տեքստում · ${list.length} դաս</h2>${list.map(r => {
    const x = lessons.get(r.job_id);
    return `<article class="library-hit"><h3>${esc(x.subject || "Առարկա")} | Դաս ${esc(x.lesson_no ?? "—")} | ${esc(x.title)}</h3><ol>${r.hits.map(h =>
      `<li><a href="${esc(lessonLink(x, h.t))}" data-library-lesson="${esc(x.job_id)}" data-lesson-time="${Math.max(0,Number(h.t)||0)}" target="_blank" rel="noopener"><span class="mono">${clock(h.t)}</span><span class="hit-source ${esc(h.source)}">${HIT_SOURCE[h.source] || "Խոսք"}</span><span class="hit-text">${markTerms(h.text, data.terms)}</span></a></li>`).join("")}</ol>${
      r.total > r.hits.length ? `<p class="muted">Եվս ${r.total - r.hits.length} համընկնում այս դասում</p>` : ""}</article>`;
  }).join("")}</section>`;
}

/* ---------- 3. AI study notes (summary, key terms, self-check questions) ---------- */
function notesHtml(notes) {
  if (!notes) return '<p class="muted">Այս դասի համար AI ամփոփում դեռ չկա։</p>';
  return `<p class="notes-label">${ic("sparkles")} Ստեղծված է AI-ով դասի տեքստից․ կարող է անճշտություններ պարունակել։</p>
    <h3>Ամփոփում</h3><ul class="notes-summary">${notes.summary.map(line => `<li>${esc(line)}</li>`).join("")}</ul>
    <h3>Հիմնական տերմիններ</h3><dl class="notes-terms">${notes.terms.map(t => `<div><dt>${esc(t.term)}</dt><dd>${esc(t.meaning)}</dd></div>`).join("")}</dl>
    <h3>Ստուգեք ինքներդ</h3><ol class="notes-questions">${notes.questions.map(q => `<li>${esc(q)}</li>`).join("")}</ol>`;
}
async function loadLessonNotes(event) {
  const box = event.target;
  if (!box?.matches?.("details[data-notes]") || !box.open) return;
  const body = box.querySelector(".notes-body");
  if (!body || body.dataset.loaded) return;
  body.textContent = "Բեռնում ենք…";
  try {
    const lesson = await api(`/api/library/${encodeURIComponent(box.dataset.notes)}`);
    body.innerHTML = notesHtml(lesson.summary);
    body.dataset.loaded = "1";
  } catch (error) {
    if (error.message !== "auth") body.textContent = "Ամփոփումը բեռնել չհաջողվեց։ Փորձեք կրկին։";
  }
}


/* ---------- 4. embedded lesson player and private viewing progress ---------- */
let learningPlayer = null, youtubeReady = null, learningHooks = false, learningEpoch = 0;
const legacyLessonProgress = new Map();
function lessonRoute(lesson, seconds = null) {
  const route = `#/library/${encodeURIComponent(lesson.job_id)}`;
  return seconds == null ? route : `${route}?t=${Math.max(0, Math.floor(Number(seconds) || 0))}`;
}
function learningIdentity() { return `${S.role || ""}:${S.user?.id || "shared"}`; }
function mergeWatchIntervals(intervals) {
  const list = intervals.filter(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && p[0] >= 0 && p[1] > p[0]).map(p => p.slice()).sort((a,b) => a[0]-b[0]);
  const merged = [];
  for (const pair of list) {
    const last = merged[merged.length-1];
    if (last && pair[0] <= last[1]) last[1] = Math.max(last[1],pair[1]);
    else merged.push(pair);
  }
  return merged;
}
function watchedSeconds(intervals) { return intervals.reduce((sum,pair) => sum + pair[1]-pair[0],0); }
function lessonProgressHtml(progress) {
  if (!progress) return "";
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
  return `<div class="learning-progress"><progress max="100" value="${percent}" aria-label="Դիտված է ${Math.round(percent)} տոկոս"></progress><span>${progress.completed ? "Դիտված է" : `Դիտված է ${Math.round(percent)}%`}</span></div>`;
}
function isPlayableLesson(lesson) {
  return !lesson.demo && lesson.playable !== false && Number.isInteger(lesson.job_id) && lesson.job_id > 0 && !!lesson.url;
}
function libraryCardHtml(x) {
  const playable=isPlayableLesson(x), route=playable ? lessonRoute(x) : null, chapters=x.chapters || [];
  const image=x.thumbnail ? `<img src="${esc(x.thumbnail)}" loading="lazy" alt="${esc(x.title)}">` : `<span class="library-demo-poster" aria-hidden="true">${ic("book")}</span>`;
  const cover=playable ? `<a class="library-cover" href="${esc(route)}">${image}<span class="play-pill">${ic("play")} Դիտել դասը</span>${x.duration ? `<span class="library-duration">${fmtDur(x.duration)}</span>` : ""}</a>` : `<div class="library-cover library-demo-cover">${image}<span class="badge library-demo-badge">Ցուցադրական</span></div>`;
  const title=esc(x.title || "Դաս"), date=esc(formatLessonDate(x.lesson_date));
  return `<article class="card library-card${playable ? "" : " library-demo"}">${cover}<div class="library-body"><div class="library-card-meta"><span class="pill">${esc(x.group)}</span><span>${esc(x.subject || "Առարկան նշված չէ")}</span></div><h2>${playable ? `<a href="${esc(route)}">${title}</a>` : title}</h2><p class="library-card-date">${x.lesson_no != null ? `<span>Դաս ${esc(x.lesson_no)}</span>` : ""}<span>${date}${x.lesson_date_inferred ? " · գրանցման օր" : ""}</span></p>${playable ? `${lessonProgressHtml(libraryProgressFor(x.job_id))}<details><summary>Գլուխներ · ${chapters.length}</summary><ol class="chapter-links">${chapters.map(c=>`<li><a href="${esc(lessonRoute(x,c.start))}"><span class="mono">${clock(c.start)}</span> ${esc(c.title)}</a></li>`).join("") || "<li>Տվյալ չկա</li>"}</ol></details><details class="lesson-notes" data-notes="${esc(x.job_id)}"><summary>Ամփոփում և հարցեր</summary><div class="notes-body" aria-live="polite"></div></details>` : `${x.description ? `<p class="library-demo-description">${esc(x.description)}</p>` : ""}<p class="library-demo-note">Ցուցադրական դաս · վիդեո չկա</p>`}</div></article>`;
}

function libraryProgressFor(id) {
  return (S.libraryProgress?.items || []).find(p => Number(p.job_id) === Number(id)) || legacyLessonProgress.get(Number(id));
}
function renderContinueWatching() {
  const box = $("#library-continue");
  if (!box) return;
  const lessons = new Map((S.library || []).map(x => [Number(x.job_id),x]));
  const list = (S.library || []).filter(isPlayableLesson).map(x => libraryProgressFor(x.job_id)).filter(p => p && p.position > 5 && !p.completed && lessons.has(Number(p.job_id))).sort((a,b) => (b.updated_at || "").localeCompare(a.updated_at || "")).slice(0,4);
  box.innerHTML = list.length ? `<section class="card continue-watching"><h2>Շարունակեք դիտել</h2><div>${list.map(p => {const x=lessons.get(Number(p.job_id));return `<a href="${esc(lessonRoute(x))}"><strong>${esc(x.title)}</strong><span>${esc(x.subject)} · ${clock(p.position)}-ից</span>${lessonProgressHtml(p)}</a>`;}).join("")}</div></section>` : "";
}
function resetLearningSession() {
  learningEpoch++; legacyLessonProgress.clear(); S.libraryProgress=null; S.libraryFacets=null; S.libraryText=null; S.library=[]; releaseLearningPlayer(false);
}
function rejectLearningIdentity() {
  resetLearningSession(); S.role=null; S.user=null;
  if (typeof renderLogin === "function") renderLogin();
}
function installLearningLifecycle() {
  if (learningHooks) return;
  learningHooks = true;
  const reset = resetLearningSession;
  document.addEventListener("click",event => {if (event.target.closest?.("#logout")) reset();},true);
  document.addEventListener("submit",event => {if (event.target.id === "login-form") reset();},true);
  window.addEventListener("pagehide",() => releaseLearningPlayer(true));
  new MutationObserver(() => {
    if (!S.role) {if (learningPlayer || legacyLessonProgress.size) reset(); return;}
    if (learningPlayer && !learningPlayer.root.isConnected) releaseLearningPlayer(true);
  }).observe(document.body,{childList:true,subtree:true});
}
function releaseLearningPlayer(save = true) {
  const current = learningPlayer;
  learningPlayer = null;
  if (!current) return;
  clearInterval(current.timer);
  current.tracker?.stop(save);
  try {current.player?.destroy();} catch (_) {}
}
function loadYouTubePlayerAPI() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (youtubeReady) return youtubeReady;
  youtubeReady = new Promise((resolve,reject) => {
    const previous = window.onYouTubeIframeAPIReady;
    let finished = false;
    const finish = error => {
      if (finished) return;
      finished = true; clearTimeout(timer);
      if (error) {document.getElementById("learning-youtube-api")?.remove(); youtubeReady=null; reject(error);}
      else resolve(window.YT);
    };
    const timer = setTimeout(() => finish(new Error("youtube timeout")),20000);
    window.onYouTubeIframeAPIReady = () => {try {previous?.();} finally {window.YT?.Player ? finish() : finish(new Error("youtube unavailable"));}};
    const script = document.createElement("script");
    script.id = "learning-youtube-api"; script.src = "https://www.youtube.com/iframe_api"; script.async = true;
    script.onerror = () => finish(new Error("youtube unavailable"));
    document.head.append(script);
  });
  return youtubeReady;
}
/* Only consecutive playback earns credit. A seek or a suspended browser timer does not. */
function createWatchTracker({lesson,player,valid,onProgress,onSaveState,now = () => performance.now()/1000}) {
  const personal = lesson.personal === true, expectedUserId=Number.isInteger(S.user?.id) ? S.user.id : null;
  let progress = {...(lesson.progress || {}),job_id:Number(lesson.job_id)}, pending = [], intervals = personal ? [] : (legacyLessonProgress.get(Number(lesson.job_id))?.intervals || []);
  let previous = null, playing = false, stopped = false, saving = false, dirty = false, lastSave = now();
  const number = (value,fallback=0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  function read() {
    const duration = Math.max(0,number(lesson.duration) || number(player.getDuration?.()));
    const raw = Math.max(0,number(player.getCurrentTime?.()));
    return {position:duration ? Math.min(raw,duration) : raw,duration};
  }
  function report() {
    if (!personal) {
      intervals = mergeWatchIntervals(intervals);
      const watched = watchedSeconds(intervals), percent = progress.duration ? Math.min(100,100*watched/progress.duration) : 0;
      progress = {...progress,intervals,watched_sec:watched,percent,completed:percent>=90,updated_at:new Date().toISOString()};
      if (valid()) legacyLessonProgress.set(Number(lesson.job_id),progress);
    }
    onProgress?.(progress);
  }
  function sample() {
    if (stopped || !valid()) {previous=null;return;}
    const point = read(), at = now(), rate = Math.max(0.25,number(player.getPlaybackRate?.(),1));
    if (playing && previous) {
      const elapsed=at-previous.at, delta=point.position-previous.position;
      if (elapsed > 0 && elapsed <= 5 && delta > 0 && delta <= elapsed*rate+0.6) {
        const pair=[previous.position,point.position]; pending.push(pair); intervals.push(pair);
      }
    }
    if (point.position !== progress.position) dirty = true;
    progress={...progress,...point}; previous=playing ? {...point,at} : null;
    report();
    if (at-lastSave>=12) {lastSave=at; save();}
  }
  async function save() {
    if (!valid() || saving || (personal && expectedUserId===null) || (!dirty && !pending.length)) return;
    if (!personal) {dirty=false;pending=[];report();return;}
    const batch=mergeWatchIntervals(pending).slice(0,500); pending=[];dirty=false;saving=true;let savedOK=false;
    try {
      onSaveState?.("saving");
      const saved=await api(`/api/library/${encodeURIComponent(lesson.job_id)}/progress`,{method:"POST",body:JSON.stringify({position:progress.position,duration:progress.duration,intervals:batch,expected_user_id:expectedUserId}),keepalive:true});
      if (!valid()) return;
      if (saved.saved) {savedOK=true;progress={...progress,...saved};onProgress?.(progress);onSaveState?.("saved");}
    } catch(error) {
      if (valid()) {pending=[...batch,...pending];dirty=true;onSaveState?.("error");}
    } finally {saving=false;if (savedOK && stopped && valid() && (dirty || pending.length)) save();}
  }
  return {
    sample,save,
    state(value) {sample();playing=value===1;previous=playing ? {...read(),at:now()} : null;if (value===0 || value===2) save();},
    seek(position) {previous=null;progress.position=Math.max(0,Number(position)||0);dirty=true;},
    stop(flush=true) {if (stopped) return;sample();if (flush) save();stopped=true;},
    progress:() => progress
  };
}
function activeLessonCue(cues,position) {
  return cues.findIndex(cue => position >= Number(cue.start) && position < Number(cue.end));
}
function lessonInitialPosition(lesson,hash) {
  const query = hash.includes("?") ? hash.slice(hash.indexOf("?")+1) : "";
  const value = new URLSearchParams(query).get("t");
  const previous = lesson.personal ? lesson.progress : legacyLessonProgress.get(Number(lesson.job_id));
  const raw=value !== null && /^\d+(?:\.\d+)?$/.test(value) ? Number(value) : Number(previous?.position)||0;
  return Math.max(0,Math.min(raw,Number(lesson.duration)||raw));
}
async function viewLesson(id) {
  installLearningLifecycle();
  const identity=learningIdentity(), epoch=learningEpoch;
  if (learningPlayer?.id===id && learningPlayer.root.isConnected && learningPlayer.identity===identity && learningPlayer.epoch===epoch) return;
  releaseLearningPlayer(true);
  const main=$("#main");
  main.innerHTML='<section id="lesson-view" class="lesson-view"><a class="btn" href="#/library">← Դասերի գրադարան</a><p role="status">Բեռնում ենք դասը…</p></section>';
  const root=$("#lesson-view"), current={id,root,identity,epoch,player:null,tracker:null,timer:null};
  learningPlayer=current;
  const sameSession=() => learningEpoch===epoch && learningIdentity()===identity && !!S.role;
  const alive=() => learningPlayer===current && root.isConnected && sameSession();
  try {
    const lesson=await api(`/api/library/${id}`);
    if (!alive()) return;
    const cues=Array.isArray(lesson.cues) ? lesson.cues : [], chapters=Array.isArray(lesson.chapters) ? lesson.chapters : [];
    const initial=lessonInitialPosition(lesson,location.hash);
    root.innerHTML=`<div class="lesson-heading"><a class="btn" href="#/library">${ic("back")} Դասերի գրադարան</a><span class="pill">${esc(lesson.group)} · ${esc(lesson.subject)}</span><h1>${esc(lesson.title)}</h1></div>
      <div class="lesson-layout"><section class="lesson-screen"><div class="lesson-video"><div id="lesson-youtube-${id}"></div></div><p class="muted" id="lesson-player-status" role="status">Բեռնում ենք YouTube նվագարկիչը…</p>
      <div class="lesson-controls"><label class="fld"><span>Արագություն</span><select class="input" id="lesson-speed" disabled><option value="1">1×</option></select></label><a class="btn" href="${esc(lesson.url)}" target="_blank" rel="noopener">${ic("external")} YouTube-ում</a></div>
      <div id="lesson-watch-progress">${lessonProgressHtml(lesson.progress || legacyLessonProgress.get(id))}</div><p class="muted lesson-save" id="lesson-save-status" role="status">${lesson.personal ? "Դիտման առաջընթացը պահվում է ձեր հաշվին։" : "Ընդհանուր մուտք․ առաջընթացը պահվում է միայն այս բացված էջի հիշողության մեջ մինչև ելքը։"}</p>
      <section class="card lesson-chapters"><h2>Գլուխներ</h2><ol>${chapters.map(c => `<li><button class="btn" data-lesson-seek="${Math.max(0,Number(c.start)||0)}"><span class="mono">${clock(c.start)}</span><span>${esc(c.title)}</span></button></li>`).join("") || '<li class="muted">Այս դասի համար գլուխներ չկան։</li>'}</ol></section></section>
      <section class="card lesson-transcript"><div class="row"><h2>Դասի տեքստը</h2><label class="lesson-follow"><input type="checkbox" id="lesson-follow" checked> Հետևել խոսքին</label></div><div class="lesson-cues" id="lesson-cues">${cues.map((cue,i) => `<button class="lesson-cue" data-cue="${i}" data-lesson-seek="${Math.max(0,Number(cue.start)||0)}"><span class="mono">${clock(cue.start)}</span><span>${esc(cue.text)}</span></button>`).join("") || '<p class="muted">Այս դասի տեքստը դեռ հասանելի չէ։</p>'}</div></section></div>
      <section class="card lesson-notes lesson-study"><h2>AI ամփոփում և հարցեր</h2><div class="notes-body">${notesHtml(lesson.summary)}</div></section>`;
    if (!/^[A-Za-z0-9_-]{11}$/.test(lesson.video_id || "")) throw new Error("video unavailable");
    const YT=await loadYouTubePlayerAPI();
    if (!alive()) return;
    let cueIndex=-1;
    const status=root.querySelector("#lesson-player-status"), speed=root.querySelector("#lesson-speed");
    const update=() => {
      if (!alive()) {if (learningPlayer===current) releaseLearningPlayer(sameSession());return;}
      current.tracker?.sample();
      const active=activeLessonCue(cues,Number(current.player?.getCurrentTime?.())||0);
      if (active !== cueIndex) {
        root.querySelector(`[data-cue="${cueIndex}"]`)?.removeAttribute("aria-current");
        const button=root.querySelector(`[data-cue="${active}"]`);button?.setAttribute("aria-current","true");cueIndex=active;
        if (button && root.querySelector("#lesson-follow").checked) {
          const scroll=root.querySelector("#lesson-cues");
          scroll.scrollTop=Math.max(0,button.offsetTop-scroll.offsetTop-scroll.clientHeight/3);
        }
      }
    };
    current.player=new YT.Player(`lesson-youtube-${id}`,{width:"100%",height:"100%",videoId:lesson.video_id,playerVars:{playsinline:1,start:Math.floor(initial),origin:location.origin,rel:0},events:{
      onReady(event) {
        if (!alive()) {try {event.target.destroy();} catch (_) {}return;}
        current.player=event.target;
        event.target.getIframe?.()?.setAttribute("title",lesson.title);
        current.tracker=createWatchTracker({lesson,player:event.target,valid:sameSession,onProgress(progress) {if (alive()) root.querySelector("#lesson-watch-progress").innerHTML=lessonProgressHtml(progress);},onSaveState(state) {if (alive()) root.querySelector("#lesson-save-status").textContent=state==="error" ? "Առաջընթացը պահել չհաջողվեց։ Կփորձենք կրկին։" : state==="saving" ? "Պահպանում ենք առաջընթացը…" : "Առաջընթացը պահպանված է ձեր հաշվին։";}});
        const rates=event.target.getAvailablePlaybackRates?.() || [1];
        speed.innerHTML=rates.filter(r => r>=0.25 && r<=2).map(r => `<option value="${Number(r)}"${r===1?' selected':''}>${Number(r)}×</option>`).join("");speed.disabled=false;
        status.textContent=initial>0 ? `Կարող եք շարունակել ${clock(initial)}-ից։` : "Սեղմեք նվագարկման կոճակը՝ դասը դիտելու համար։";
        current.timer=setInterval(update,1000);update();
      },
      onStateChange(event) {if (alive()) {current.tracker?.state(event.data);update();}},
      onPlaybackRateChange(event) {if (alive()) {current.tracker?.seek(current.player.getCurrentTime());speed.value=String(event.data);}},
      onError() {if (alive()) {current.tracker?.state(-1);status.textContent="YouTube նվագարկիչը հասանելի չէ։ Կարող եք բացել դասը YouTube-ում։";}}
    }});
    root.addEventListener("click",event => {
      const button=event.target.closest("[data-lesson-seek]");
      if (!button || !alive() || !current.tracker) return;
      const position=Number(button.dataset.lessonSeek)||0;
      current.tracker.seek(position);current.player.seekTo(position,true);current.player.playVideo();update();
    });
    speed.addEventListener("change",() => {if (alive()) current.player.setPlaybackRate(Number(speed.value));});
  } catch(error) {
    if (!alive() || error.message==="auth") return;
    const status=root.querySelector("#lesson-player-status");
    if (status) status.textContent="Նվագարկիչը բեռնել չհաջողվեց։ Կարող եք բացել դասը YouTube-ում կամ վերաբեռնել էջը։";
    else root.innerHTML='<a class="btn" href="#/library">← Դասերի գրադարան</a><p class="alert err" role="alert">Այս դասը հասանելի չէ։ Վերադարձեք գրադարան և փորձեք կրկին։</p>';
  }
}

/* ---------- 5. watch statistics for authorized personal accounts ---------- */
function renderLearningLinks() {
  const box=$("#library-tools");
  if (!box) return;
  const links=[];
  if (S.libraryFacets?.watch_stats_available === true) links.push('<a class="btn sm" href="#/library/watch-stats" aria-label="Դիտման վիճակագրություն" title="Դիտման վիճակագրություն">'+ic("chart")+'<span class="library-tool-label">Դիտման վիճակագրություն</span></a>');
  if (S.user?.id) links.push('<a class="btn sm" href="#/library/settings" aria-label="Ծանուցումների կարգավորումներ" title="Ծանուցումների կարգավորումներ">'+ic("settings")+'<span class="library-tool-label">Կարգավորումներ</span></a>');
  const html=links.join("");
  if (box.innerHTML!==html) box.innerHTML=html;
}
function watchPercent(value) { return `${Math.max(0,Math.min(100,Number(value)||0)).toLocaleString("hy-AM",{maximumFractionDigits:1})}%`; }
function watchDate(value) {
  if (!value) return "Դեռ չի դիտել";
  const date=new Date(value);
  return Number.isNaN(date.getTime()) ? "Նշված չէ" : date.toLocaleDateString("hy-AM");
}
function watchStatisticsHtml(data) {
  const lessons=data.lessons || [], students=data.students || [], missed=data.missed || [], titles=new Map(lessons.map(x => [Number(x.job_id),x.title]));
  return `<div class="watch-metrics"><div class="card"><strong>${lessons.length}</strong><span>Հրապարակված դաս</span></div><div class="card"><strong>${students.length}</strong><span>Ուսանող՝ խմբերի կազմում</span></div><div class="card"><strong>${missed.length}</strong><span>Վերջին երկու դասերը չդիտած</span></div></div>
    <p class="muted watch-explanation">Դասը համարվում է դիտված՝ առնվազն ${Number(data.threshold)||90}% եզակի հատված դիտելուց հետո։ Առաջ տալը չի հաշվվում որպես դիտում։ Թվերը վերաբերում են միայն անձնական հաշիվներով դիտումներին։</p>
    <section class="card watch-section"><h2>Ըստ դասերի</h2>${lessons.length ? `<div class="watch-lesson-list">${lessons.map(x => `<article class="watch-lesson"><div><h3><a href="${esc(lessonRoute(x))}">${esc(x.title)}</a></h3><p class="muted">${esc(x.group)} · ${esc(x.subject)} · ${esc(watchDate(x.published_at))}</p></div><dl><div><dt>Սկսել է</dt><dd>${Number(x.started)||0} / ${Number(x.students)||0}</dd></div><div><dt>Դիտել է ≥90%</dt><dd>${Number(x.completed)||0}</dd></div><div><dt>Միջին դիտում</dt><dd>${watchPercent(x.average_percent)}</dd></div></dl></article>`).join("")}</div>` : '<p class="muted">Այս ընտրության համար հրապարակված դասեր չկան։</p>'}</section>
    <section class="card watch-section"><h2>Ըստ ուսանողների</h2>${students.length ? `<div class="watch-student-list">${students.map(x => `<details class="watch-student"><summary><span><strong>${esc(x.name)}</strong><small>${esc(x.group)} · Վերջին դիտում՝ ${esc(watchDate(x.last_watched_at))}</small></span><span class="watch-student-total"><strong>${watchPercent(x.percent)}</strong><small>${Number(x.completed)||0} / ${Number(x.total_lessons)||0} դաս</small></span></summary><p class="muted">Եզակի դիտված ժամանակ՝ ${fmtDur(Number(x.watched_sec)||0)}</p><ul>${(x.lessons || []).map(lesson => `<li><a href="${esc(lessonRoute(lesson))}">${esc(titles.get(Number(lesson.job_id)) || "Դաս")}</a><span>${watchPercent(lesson.percent)}${lesson.completed ? " · Դիտված է" : ""}</span></li>`).join("") || '<li class="muted">Դասեր չկան։</li>'}</ul></details>`).join("")}</div>` : '<p class="muted">Այս ընտրության համար ուսանողներ չկան։</p>'}</section>
    <section class="card watch-section"><h2>Վերջին երկու դասերը չդիտած ուսանողներ</h2><p class="muted">Ցանկում այն ուսանողներն են, որոնք իրենց խմբի վերջին երկու հրապարակված դասերից ոչ մեկում դիտված հատված չունեն։ Երկուսից քիչ դաս ունեցող խմբերը չեն գնահատվում։</p>${missed.length ? `<ul class="watch-missed">${missed.map(x => `<li><strong>${esc(x.name)}</strong><span class="muted">${esc(x.group)}</span><ul>${(x.lessons || []).map(lesson => `<li><a href="${esc(lessonRoute(lesson))}">${esc(lesson.title)}</a></li>`).join("")}</ul></li>`).join("")}</ul>` : '<p class="muted">Այս ընտրության համար չդիտած ուսանողներ չկան։</p>'}</section>`;
}
async function viewWatchStats() {
  installLearningLifecycle();
  const hash=location.hash, identity=learningIdentity(), epoch=learningEpoch;
  let root=$("#learning-watch-stats");
  if (root && root.dataset.hash===hash && root.dataset.identity===identity && (root.learningBusy || Date.now()-Number(root.dataset.loadedAt)<30000)) return;
  if (!root || root.dataset.hash!==hash || root.dataset.identity!==identity) {
    $("#main").innerHTML='<section id="learning-watch-stats" class="learning-watch-stats"><div class="top"><div><a class="btn" href="#/library">← Դասերի գրադարան</a><h1>Դիտման վիճակագրություն</h1><p>Ձեր հասանելի խմբերի ուսանողների դիտման առաջընթացը։</p></div></div><label class="fld watch-group"><span>Խումբ</span><select class="input" id="watch-group" aria-label="Վիճակագրության խումբ"><option value="">Բոլոր հասանելի խմբերը</option></select></label><div id="watch-content" aria-live="polite"><p role="status">Բեռնում ենք վիճակագրությունը…</p></div></section>';
    root=$("#learning-watch-stats");root.dataset.hash=hash;root.dataset.identity=identity;
    root.querySelector("#watch-group").addEventListener("change",event => {const query=new URLSearchParams();if (event.target.value) query.set("group",event.target.value);location.hash="#/library/watch-stats"+(query.size?"?"+query.toString():"");});
  }
  const same=() => root.isConnected && $("#learning-watch-stats")===root && location.hash===hash && learningIdentity()===identity && learningEpoch===epoch;
  root.learningBusy=true;
  try {
    const query=new URLSearchParams(hash.split("?")[1] || ""), group=query.get("group") || "";
    const data=await api("/api/library/watch-stats"+(group?"?group="+encodeURIComponent(group):""));
    if (!same()) return;
    const select=root.querySelector("#watch-group"), options='<option value="">Բոլոր հասանելի խմբերը</option>'+(data.groups || []).map(x => `<option value="${esc(x.name)}">${esc(x.name)}</option>`).join("");
    if (select.dataset.options!==options) {select.innerHTML=options;select.dataset.options=options;}
    select.value=data.selected_group || "";
    const body=root.querySelector("#watch-content"), html=watchStatisticsHtml(data);
    if (body.innerHTML!==html) body.innerHTML=html;
    root.dataset.loadedAt=String(Date.now());
  } catch(error) {
    if (!same() || error.message==="auth") return;
    root.querySelector("#watch-content").innerHTML='<p class="alert err" role="alert">Վիճակագրությունը բեռնել չհաջողվեց կամ այն հասանելի չէ ձեր հաշվին։</p><button class="btn" id="watch-reload">Փորձել կրկին</button>';
    root.querySelector("#watch-reload").addEventListener("click",() => {root.dataset.loadedAt="0";viewWatchStats();});
    root.dataset.loadedAt=String(Date.now());
  } finally {root.learningBusy=false;}
}

/* ---------- 6. personal e-mail notification preferences ---------- */
function notificationPreferencesHtml(data) {
  if (!data.personal) return '<p class="muted">Էլ․ նամակների կարգավորումները հասանելի են միայն անձնական հաշիվներով։ Ընդհանուր մուտքով անձնական ծանուցումներ չեն ուղարկվում։</p>';
  return `<form class="learning-preferences-form" novalidate><p class="muted">Էլ․ նամակ ստանալ ձեր խմբերում հրապարակված դասերի մասին։</p><label class="learning-mail-option"><input type="checkbox" name="new_lessons"${data.new_lessons === true ? " checked" : ""}><span><strong>Նոր դասերի ծանուցումներ</strong><small>Էլ․ նամակ՝ ձեր խմբում նոր դաս հրապարակվելուց հետո։</small></span></label><label class="learning-mail-option"><input type="checkbox" name="weekly_digest"${data.weekly_digest === true ? " checked" : ""}><span><strong>Շաբաթական դասերի ամփոփագիր</strong><small>Անցած շաբաթվա հրապարակված դասերը՝ մեկ նամակում։</small></span></label><div class="learning-mail-actions"><button class="btn primary" type="submit" id="learning-mail-save">Պահպանել</button><p class="muted" id="learning-mail-status" role="status" aria-live="polite"></p></div></form>`;
}
async function mountLearningPreferences(target) {
  if (!target) return;
  installLearningLifecycle();
  const identity=learningIdentity(), epoch=learningEpoch, expectedUserId=Number.isInteger(S.user?.id) ? S.user.id : null;
  if (target.learningMounted && target.learningIdentity===identity && target.learningEpoch===epoch) return;
  target.learningMounted=true;target.learningIdentity=identity;target.learningEpoch=epoch;
  target.innerHTML='<p class="muted" role="status">Բեռնում ենք ծանուցումների կարգավորումները…</p>';
  const same=() => target.isConnected && learningIdentity()===identity && learningEpoch===epoch && !!S.role;
  try {
    const data=await api("/api/library/notifications");
    if (!same()) return;
    if ((data.personal && data.user_id!==expectedUserId) || (!data.personal && expectedUserId!==null)) {rejectLearningIdentity();return;}
    target.innerHTML=notificationPreferencesHtml(data);
    if (!data.personal) return;
    const form=target.querySelector(".learning-preferences-form"), news=target.querySelector('[name="new_lessons"]'), weekly=target.querySelector('[name="weekly_digest"]'), save=target.querySelector("#learning-mail-save"), status=target.querySelector("#learning-mail-status");
    form.addEventListener("submit",async event => {
      event.preventDefault();
      if (form.learningBusy || !same()) return;
      form.learningBusy=true;news.disabled=true;weekly.disabled=true;save.disabled=true;status.textContent="Պահպանում ենք…";
      try {
        const saved=await api("/api/library/notifications",{method:"PUT",body:JSON.stringify({new_lessons:news.checked,weekly_digest:weekly.checked,expected_user_id:expectedUserId})});
        if (!same()) return;
        if (!saved.personal || saved.user_id!==expectedUserId) {rejectLearningIdentity();return;}
        news.checked=saved.new_lessons===true;weekly.checked=saved.weekly_digest===true;
        status.textContent="Կարգավորումները պահպանված են։";
      } catch(error) {
        if (same() && error.message!=="auth") status.textContent="Կարգավորումները պահել չհաջողվեց։ Փորձեք կրկին։";
      } finally {
        form.learningBusy=false;
        if (same()) {news.disabled=false;weekly.disabled=false;save.disabled=false;}
      }
    });
  } catch(error) {
    if (!same() || error.message==="auth") return;
    target.innerHTML='<p class="alert err" role="alert">Ծանուցումների կարգավորումները բեռնել չհաջողվեց։</p><button class="btn" id="learning-mail-retry">Փորձել կրկին</button>';
    target.querySelector("#learning-mail-retry").addEventListener("click",() => {target.learningMounted=false;mountLearningPreferences(target);});
  }
}
async function viewLearningSettings() {
  let target=$("#learning-notifications");
  if (!target) {
    $("#main").innerHTML='<section class="learning-settings"><div class="top"><div><a class="btn" href="#/library">← Դասերի գրադարան</a><h1>Ծանուցումների կարգավորումներ</h1><p>Ընտրեք՝ ինչ էլ․ նամակներ եք ցանկանում ստանալ։</p></div></div><section class="card" id="learning-notifications" aria-label="Անձնական ծանուցումներ"></section></section>';
    target=$("#learning-notifications");
  }
  await mountLearningPreferences(target);
}
