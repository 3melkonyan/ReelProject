/* admin-only lecturer snapshots and explicit choices. app.js owns routing. */
async function renderLecturerPanel(d) {
  const panel = $("#jd-lecturer");
  if (!panel) return;
  if (S.role !== "admin") {
    panel.innerHTML = "";
    delete S.last["#jd-lecturer"];
    S.lecturerPanel = null;
    return;
  }
  if (!d || !Number.isInteger(d.id)) return;
  let state = S.lecturerPanel;
  if (!state || state.job !== d.id || state.panel !== panel) {
    state = { job: d.id, panel, signature: null, busy: false, data: null, revision: 0, error: "", latest: d };
    S.lecturerPanel = state;
    panel.innerHTML = "";
    delete S.last["#jd-lecturer"];
  }
  state.latest = d;
  const blur = (d.stages || []).find((stage) => stage.name === "blur");
  const signature = JSON.stringify([d.id, d.status, blur && blur.status, blur && blur.attempts, blur && blur.error]);
  if (state.busy || state.signature === signature) return;
  state.signature = signature;
  if (!blur || (blur.status === "pending" && !blur.attempts)) {
    if (panel.innerHTML) panel.innerHTML = "";
    delete S.last["#jd-lecturer"];
    state.data = null;
    state.error = "";
    return;
  }
  const alive = () => S.lecturerPanel === state && $("#jd-lecturer") === panel && S.role === "admin";
  const endpoint = `/api/jobs/${state.job}/lecturer`;
  const errorText = (error) => error.net ? i18nText("ui.lecturer.could_not_connect_to_the_service_try") : String(error.message || error);
  const share = (identity) => identity && Number.isFinite(identity.share) ? `${Math.round(Math.max(0, Math.min(1, identity.share)) * 100)}%` : null;
  const imageURL = (name) => `${endpoint}/${encodeURIComponent(name)}?v=${state.revision}`;

  if (!$("#lecturer-css")) {
    const style = document.createElement("style");
    style.id = "lecturer-css";
    style.textContent = `
      #jd-lecturer:empty{display:none}
      #jd-lecturer .lecturer-card{margin-bottom:16px}
      #jd-lecturer .lecturer-card h2{margin-bottom:12px}
      #jd-lecturer .lecturer-refs{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
      #jd-lecturer .lecturer-refs img{width:88px;height:88px;max-width:100%;object-fit:cover;border-radius:10px;border:1px solid var(--border);background:var(--surface-2)}
      #jd-lecturer .lecturer-identities{display:grid;gap:12px;margin-top:14px}
      #jd-lecturer .lecturer-candidate{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:14px;border:1px solid var(--border);border-radius:12px;padding:12px}
      #jd-lecturer .lecturer-candidate img{display:block;width:480px;max-width:100%;height:auto;border-radius:8px;background:var(--surface-2)}
      #jd-lecturer .lecturer-caption{margin-top:8px;font-size:12.5px;overflow-wrap:anywhere}
      #jd-lecturer .lecturer-actions,#jd-lecturer .lecturer-choice{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:14px}
      #jd-lecturer .lecturer-choice{padding:12px;border-radius:10px;background:var(--accent-soft)}
      #jd-lecturer .lecturer-reason{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0 0}
      #jd-lecturer .lecturer-error{white-space:pre-wrap;overflow-wrap:anywhere;margin:14px 0 0}
      #jd-lecturer .lecturer-card p{margin:0}
      #jd-lecturer .lecturer-empty-image{max-width:480px;padding:20px;border:1px dashed var(--border);border-radius:8px;text-align:center}
      @media(max-width:640px){#jd-lecturer .lecturer-candidate{grid-template-columns:1fr}#jd-lecturer .lecturer-candidate .btn{justify-content:center}#jd-lecturer .lecturer-actions .btn{width:100%;justify-content:center}#jd-lecturer .lecturer-choice .btn{width:100%;justify-content:center}}
    `;
    document.head.append(style);
  }

  function lockControls() {
    panel.querySelectorAll("[data-lecturer-action]").forEach((button) => { button.disabled = state.busy; });
    panel.setAttribute("aria-busy", String(state.busy));
  }

  function render() {
    if (!alive() || state.busy) return;
    const data = state.data;
    let body = "";
    if (data) {
      const identities = data.identities || [];
      if (data.mode === "auto" || data.mode === "chosen") {
        const selected = identities.find((identity) => identity.rank === data.lecturer_rank);
        const percent = share(selected);
        body = `<div class="row"><span class="pill">${ic("check")} ${data.mode === "auto" ? i18nText("ui.lecturer.selected_automatically") : i18nText("ui.lecturer.selected_by_an_administrator")}</span></div>
          ${percent ? `<p class="muted" style="margin-top:10px">${i18nText("ui.lecturer.appears_in")} ${percent}${i18nText("ui.lecturer.of_frames_containing_faces")}</p>` : ""}
          <div class="lecturer-refs">${(data.refs || []).filter((name) => /^ref_\d{1,2}\.jpg$/.test(name)).slice(0, 8).map((name) => `<img loading="lazy" src="${esc(imageURL(name))}" alt="${i18nText("ui.lecturer.selected_lecturer_reference_image")}">`).join("")}</div>`;
      } else if (data.mode === "refs") {
        body = `<p>${i18nText("ui.lecturer.from_reference_photos")} <b>${esc((data.lecturers || []).join(", ") || "—")}</b></p>`;
      } else if (data.mode === "none") {
        body = `<p>${i18nText("ui.lecturer.no_lecturer_selected_all_faces_will_be")}</p>`;
      } else if (data.mode === "unsure") {
        body = `<div class="alert warn">${ic("alert")}<div>${i18nText("ui.lecturer.automatic_recognition_could_not_confidently_identify_the")} ${data.can_choose ? i18nText("ui.lecturer.nothing_has_been_published_make_a_selection") : i18nText("ui.lecturer.selection_is_currently_unavailable")}</div></div>`;
        if (data.can_choose) {
          body += `<div class="lecturer-identities">${identities.map((identity) => {
            const rank = Number(identity.rank), percent = share(identity);
            const image = /^identity_[1-6]\.jpg$/.test(identity.image || "") ? identity.image : null;
            const count = Number.isFinite(identity.presence) ? Math.max(0, Math.round(identity.presence)) : "—";
            const first = Number.isFinite(identity.first_t) ? clock(identity.first_t) : "—";
            return `<article class="lecturer-candidate"><div>${image ? `<img loading="lazy" src="${esc(imageURL(image))}" alt="${i18nText("ui.lecturer.person")}${rank}">` : `<div class="lecturer-empty-image muted">${i18nText("ui.lecturer.image_unavailable")}</div>`}
              <div class="muted lecturer-caption">#${rank} · ${count} ${i18nText("ui.lecturer.frames")}${percent ? ` (${percent})` : ""} ${i18nText("ui.lecturer.first_appearance")} ${esc(first)}</div></div>
              <button type="button" class="btn primary" data-lecturer-action="choose" data-rank="${rank}">${i18nText("ui.lecturer.this_is_the_lecturer")}</button></article>`;
          }).join("")}</div><div class="lecturer-actions"><button type="button" class="btn danger" data-lecturer-action="none">${i18nText("ui.lecturer.no_lecturer_blur_everyone")}</button></div>`;
        }
      } else {
        body = `<p class="muted">${data.blur_status === "running" ? i18nText("ui.lecturer.identifying_the_lecturer") : i18nText("ui.lecturer.lecturer_recognition_data_is_not_available_yet")}</p>`;
      }
      if (data.reason) body += `<div class="mono faint lecturer-reason">${esc(data.reason)}</div>`;
      if (data.choice && data.can_choose) {
        const choice = data.choice.none ? i18nText("ui.lecturer.blur_everyone") : `${i18nText("ui.lecturer.lecturer")}${data.choice.identity}`;
        body += `<div class="lecturer-choice"><span class="grow"><b>${i18nText("ui.lecturer.saved_selection")}</b> ${esc(choice)}</span><button type="button" class="btn" data-lecturer-action="clear">${i18nText("ui.lecturer.clear_selection")}</button></div>`;
      }
    }
    if (state.error) body += `<div class="alert err lecturer-error" role="alert">${ic("alert")}<div>${esc(state.error)}</div></div>
      <div class="lecturer-actions"><button type="button" class="btn" data-lecturer-action="refresh">${ic("refresh")} ${i18nText("ui.lecturer.refresh_dashboard")}</button></div>`;
    set("#jd-lecturer", `<section class="card lecturer-card" aria-label="${i18nText("ui.groups.lecturer")}"><h2>${ic("users")} ${i18nText("ui.groups.lecturer")}</h2>${body}</section>`);
    lockControls();
  }

  async function snapshot() {
    const data = await api(endpoint);
    if (alive()) {
      state.data = data;
      state.revision += 1;
    }
  }

  // api() omits response.status. Keep exact 409 handling local to this route,
  // while retaining the shared same-origin credentials and CSRF convention.
  async function retryJob() {
    let response;
    try {
      response = await fetch(`/api/jobs/${state.job}/retry`, {
        method: "POST", credentials: "same-origin",
        headers: { "X-Requested-With": "reel", "Content-Type": "application/json" },
      });
    } catch (error) { error.net = true; throw error; }
    if (response.status === 409) return false;
    if (response.status === 401) {
      await api("/api/me"); // Invoke the existing expired-session/login flow.
      throw new Error(i18nText("ui.lecturer.your_session_has_expired_sign_in_again"));
    }
    if (!response.ok) {
      let detail = `${i18nText("ui.lecturer.could_not_resume_processing_http")} ${response.status}${i18nText("ui.lecturer.punctuation")}`;
      try { detail = String((await response.json()).detail || detail); } catch (_) {}
      const error = new Error(detail);
      if (response.status >= 500) error.net = true;
      throw error;
    }
    return true;
  }

  async function choose(body) {
    if (!alive() || state.busy || !state.data || !state.data.can_choose) return;
    state.busy = true;
    state.error = "";
    lockControls();
    let saved = false;
    try {
      const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
      if (!alive()) return;
      saved = true;
      state.data.choice = result.choice;
      if (body.clear) {
        toast(i18nText("ui.lecturer.selection_cleared"));
      } else {
        const restarted = await retryJob();
        if (!alive()) return;
        toast(restarted ? i18nText("ui.lecturer.selection_saved_resuming_processing") : i18nText("ui.lecturer.the_choice_will_apply_when_the_lesson"));
      }
      await snapshot();
    } catch (error) {
      if (alive() && error.message !== "auth") {
        state.error = (saved ? (`${i18nText("ui.lecturer.selection_change_saved")} `) : "") + errorText(error);
        if (saved) {
          try { await snapshot(); } catch (_) { /* Preserve the saved choice and original error. */ }
        }
      }
    } finally {
      state.busy = false;
      if (alive()) render();
    }
  }

  panel.onclick = (event) => {
    const button = event.target.closest("[data-lecturer-action]");
    if (!button || button.disabled || !alive() || state.busy) return;
    const action = button.dataset.lecturerAction;
    if (action === "choose") choose({ identity: Number(button.dataset.rank) });
    if (action === "none" && confirm(i18nText("ui.lecturer.no_lecturer_will_be_selected_and_all"))) choose({ none: true });
    if (action === "clear") choose({ clear: true });
    if (action === "refresh") { state.signature = null; renderLecturerPanel(state.latest); }
  };

  state.busy = true;
  lockControls();
  try { await snapshot(); state.error = ""; }
  catch (error) { if (alive() && error.message !== "auth") state.error = errorText(error); }
  finally {
    state.busy = false;
    if (alive()) render();
  }
}
