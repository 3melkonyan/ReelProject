/* Early review and durable cleanup share the job page and completion views. */
async function renderUploadLecturerReview(job, selector) {
  const panel = $(selector);
  if (!panel || panel.dataset.busy === "true") return;
  let data;
  try {
    data = await api(`/api/jobs/${job}/lecturer-review`);
  } catch (error) {
    if (error.status === 403 || error.status === 404) {
      panel.innerHTML = "";
      return;
    }
    throw error;
  }
  if ($(selector) !== panel) return;
  const chosen = data.decision;
  const candidates = data.candidates || [];
  const current =
    candidates.find((person) => person.rank === (chosen?.identity || data.cursor)) || candidates[0];
  let text =
    data.status === "pending" || data.status === "scanning"
      ? i18nText("review.scanning")
      : i18nText("review.unavailable");
  if (data.status === "cancelled") text = i18nText("review.cancelled");
  else if (chosen)
    text = chosen.none
      ? i18nText("review.none")
      : data.applied === "none"
        ? i18nText("review.failed_match")
        : data.automatic
          ? i18nText("review.automatic")
          : i18nText("review.confirmed");
  else if (candidates.length) text = i18nText("review.explain");
  panel.innerHTML = `<section class="card lecturer-review"><h2>${i18nText("review.title")}</h2><p class="${data.automatic ? "alert warn" : "muted"}">${esc(text)}</p>
    <div class="review-candidates">${candidates.map((person) => `<figure class="${person.rank === current?.rank ? "selected" : ""}"><img src="${esc(person.image)}" alt="${esc(i18nText("review.person", { rank: person.rank }))}"><figcaption>${i18nText("review.person", { rank: person.rank })}${person.share == null ? "" : ` · ${Math.round(person.share * 100)}%`}</figcaption></figure>`).join("")}</div>
    ${!chosen && data.can_answer !== false ? `<div class="actions">${current ? `<button class="btn primary" data-review="identity">${i18nText("review.confirm")}</button><button class="btn" data-review="next">${i18nText("review.next")}</button>` : ""}<button class="btn danger" data-review="none">${i18nText("review.none")}</button></div>` : ""}<p class="err-text" role="alert" data-review-error></p></section>`;
  panel.onclick = async (event) => {
    const button = event.target.closest("[data-review]");
    if (!button || panel.dataset.busy === "true") return;
    panel.dataset.busy = "true";
    panel.querySelectorAll("button").forEach((node) => (node.disabled = true));
    try {
      const action = button.dataset.review;
      await api(`/api/jobs/${job}/lecturer-review`, {
        method: "POST",
        body: JSON.stringify({ [action]: action === "identity" ? current.rank : true }),
      });
      delete panel.dataset.busy;
      await renderUploadLecturerReview(job, selector);
    } catch (error) {
      panel.querySelector("[data-review-error]").textContent = error.message;
    } finally {
      delete panel.dataset.busy;
      panel.querySelectorAll("button").forEach((node) => (node.disabled = false));
    }
  };
}
function pollUploadLecturerReview(job, selector) {
  const tick = () => {
    if (!$(selector)) {
      clearInterval(timer);
      return;
    }
    renderUploadLecturerReview(job, selector).catch(() => {});
  };
  const timer = setInterval(tick, 3000);
  tick();
}
async function refreshJobNotifications() {
  if (!["admin", "lecturer", "uploader"].includes(S.role)) return;
  const box = $("#job-notifications");
  if (!box) return;
  const data = await api("/api/notifications");
  set(
    "#job-notifications",
    (data.items || []).length
      ? `<aside class="card job-notifications" aria-label="${i18nText("review.notifications")}">${data.items.map((item) => `<a href="#/jobs/${item.job_id}">${esc(item.message)}</a>`).join("")}</aside>`
      : "",
  );
}
function renderLessonDeletion(d) {
  const panel = $("#jd-delete");
  if (!panel || S.role !== "admin") return;
  if (
    panel.dataset.busy === "true" ||
    panel.querySelector("dialog")?.open ||
    panel.querySelector("[data-delete-channel]") === document.activeElement
  )
    return;
  const deletion = d.deletion,
    steps = deletion?.steps || {};
  const state = deletion?.state;
  const message =
    state === "done"
      ? "done"
      : state === "partial"
        ? "partial"
        : state === "waiting"
          ? "waiting"
          : deletion
            ? "running"
            : null;
  panel.innerHTML = `<section class="card lesson-deletion"><h2>${i18nText("deletion.title")}</h2>
    ${message ? `<p role="status" class="${state === "partial" ? "alert err" : "muted"}">${i18nText("deletion." + message)}</p><ol>${["backup", "reconcile", "youtube", "slack", "library"].map((step) => `<li>${i18nText("deletion." + step)} <strong>${i18nText("deletion.step_" + (steps[step] || "pending"))}</strong></li>`).join("")}</ol>` : ""}
    ${deletion?.error === "missing_slack_channel" ? `<p>${i18nText("deletion.missing_slack_channel")}</p><label class="fld">${i18nText("deletion.slack_channel")}<input class="input" data-delete-channel maxlength="100"></label>` : ""}
    ${deletion?.error === "unresolved_publication" ? `<p class="alert warn">${i18nText("deletion.unresolved_publication")}</p>` : ""}
    ${state !== "done" ? `<button class="btn danger" data-delete-open ${["waiting", "running", "requested"].includes(state) ? "disabled" : ""}>${i18nText(deletion ? "deletion.retry" : "deletion.title")}</button>` : ""}
    <p class="err-text" role="alert" data-delete-error></p><dialog class="delete-confirm" aria-labelledby="delete-confirm-title"><h2 id="delete-confirm-title">${i18nText("deletion.confirm_title")}</h2><p>${i18nText("deletion.confirm_body")}</p><div class="actions"><button class="btn" data-delete-cancel>${i18nText("ui.accounts.cancel")}</button><button class="btn danger" data-delete-confirm>${i18nText("deletion.confirm")}</button></div></dialog></section>`;
  panel
    .querySelector("[data-delete-open]")
    ?.addEventListener("click", () => panel.querySelector("dialog").showModal());
  panel
    .querySelector("[data-delete-cancel]")
    ?.addEventListener("click", () => panel.querySelector("dialog").close());
  panel.querySelector("[data-delete-confirm]")?.addEventListener("click", async () => {
    panel.querySelector("dialog").close();
    panel.dataset.busy = "true";
    const buttons = [...panel.querySelectorAll("button")];
    buttons.forEach((node) => (node.disabled = true));
    try {
      const channel = panel.querySelector("[data-delete-channel]")?.value.trim();
      const result = await api(`/api/jobs/${d.id}/delete`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, ...(channel ? { slack_channel: channel } : {}) }),
      });
      delete panel.dataset.busy;
      renderLessonDeletion({ ...d, deletion: result });
    } catch (error) {
      panel.querySelector("[data-delete-error]").textContent = error.message;
    } finally {
      delete panel.dataset.busy;
      buttons.forEach((node) => (node.disabled = false));
    }
  });
}
async function renderLecturerReferences() {
  const main = document.querySelector(".account-form");
  if (!main) return;
  main.insertAdjacentHTML(
    "beforeend",
    `<section class="reference-inventory"><h2>${i18nText("review.references")}</h2><p>${i18nText("review.local_only")}</p><div id="lecturer-references"></div></section>`,
  );
  if (S.role !== "admin") return;
  const box = $("#lecturer-references");
  const data = await api("/api/privacy/lecturer-references");
  box.innerHTML = data.items.length
    ? data.items
        .map(
          (item) =>
            `<div class="row"><a class="grow" href="#/jobs/${item.job_id}">${esc(item.grp)} · #${item.job_id}</a><button class="btn danger" data-delete-ref="${item.job_id}">${i18nText("review.delete_reference")}</button></div>`,
        )
        .join("")
    : `<p>${i18nText("review.no_references")}</p>`;
  box.onclick = async (event) => {
    const button = event.target.closest("[data-delete-ref]");
    if (!button || !confirm(i18nText("review.delete_reference_confirm"))) return;
    button.disabled = true;
    try {
      await api(`/api/privacy/lecturer-references/${button.dataset.deleteRef}`, {
        method: "DELETE",
      });
      button.closest(".row").remove();
    } catch (error) {
      toast(error.message);
      button.disabled = false;
    }
  };
}
