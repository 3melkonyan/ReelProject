"use strict";
/* Course-first read views. Mutations continue through the established administrator APIs. */
let coursesSequence = 0;
const courseError = (e) =>
  e.net
    ? i18nText("ui.courses.could_not_connect_to_the_server_try")
    : e.message === "auth"
      ? i18nText("ui.courses.sign_in_again")
      : e.message;
function courseAvatarStack(team) {
  const people = [...(team?.lecturers || []), ...(team?.tas || [])];
  return `<div class="course-avatars" aria-label="${i18nText("ui.courses.group_team")}">${[
    ...new Map(people.map((p) => [p.id, p])).values(),
  ]
    .slice(0, 6)
    .map(
      (p) =>
        `<span class="gp-avatar" title="${esc(p.name)}">${
          p.avatar
            ? `<img src="${esc(p.avatar)}" alt="">`
            : esc(
                p.name
                  .trim()
                  .split(/\s+/)
                  .slice(0, 2)
                  .map((x) => x[0])
                  .join(""),
              )
        }</span>`,
    )
    .join("")}</div>`;
}
async function renderGroupsPage(force = false) {
  const stable = $("#main .courses-page");
  if (!force && stable?.dataset.courseRoute === location.hash) return;
  const seq = ++coursesSequence;
  groupsStyles();
  const match = location.hash.match(/^#\/groups\/([^?]+)/),
    name = match ? decodeURIComponent(match[1]) : null;
  const tab = new URLSearchParams(location.hash.split("?")[1] || "").get("tab") || "general";
  if (name && ["edit", "schedule", "create"].includes(tab) && S.role === "admin")
    return renderLegacyGroupsPage({
      kind: tab === "schedule" ? "team" : "edit",
      name: tab === "create" ? null : name,
    });
  const main = $("#main");
  main.innerHTML = `<div class="card" role="status">${i18nText("ui.accounts.loading")}</div>`;
  try {
    if (!name) {
      const data = await api("/api/courses");
      if (seq !== coursesSequence || !location.hash.startsWith("#/groups")) return;
      const displayCourses = ["admin", "viewer"].includes(S.role)
        ? data.courses
        : data.courses.filter((c) => c.groups.length);
      function group(g) {
        return `<a class="course-group" href="#/groups/${encodeURIComponent(g.name)}"><div class="row"><h2 class="grow">${esc(g.short_name)}</h2><span class="chip">${g.completed ? i18nText("ui.courses.completed") : i18nText("ui.courses.active")}</span></div><div class="muted">${esc(groupRange(g.course_start, g.course_end))}</div><div class="muted">${g.subjects.length} ${i18nText("ui.courses.subjects")} ${g.counts.students} ${i18nText("ui.courses.students")} ${g.counts.lessons} ${i18nText("ui.app.lessons_3")}</div>${courseAvatarStack(g.team)}</a>`;
      }
      main.innerHTML = `<div class="courses-page" data-course-route="${esc(location.hash)}"><div class="top"><div><h1>${i18nText("ui.app.courses")}</h1><p>${i18nText("ui.courses.choose_a_course_then_your_group")}</p></div>${S.role === "admin" ? `<button class="btn primary" id="course-new">${i18nText("ui.courses.add_group")}</button>` : ""}</div>${displayCourses.map((c, i) => `<details class="card course-section" ${i === displayCourses.findIndex((x) => x.groups.length) ? "open" : ""}><summary><span class="course-title">${esc(c.name)}</span><span class="pill">${c.groups.length} ${i18nText("ui.courses.groups")}</span></summary><div class="course-grid">${c.groups.map(group).join("") || `<p class="course-empty">${i18nText("ui.courses.no_groups_yet")}</p>`}</div></details>`).join("")}${data.unassigned.length ? `<details class="card course-section" open><summary>${i18nText("ui.courses.unassigned_groups")}</summary><div class="course-grid">${data.unassigned.map(group).join("")}</div></details>` : ""}</div>`;
      if ($("#course-new"))
        $("#course-new").onclick = () => {
          location.hash = "#/groups/new?tab=create";
        };
      return;
    }
    const g = await api("/api/group-view/" + encodeURIComponent(name));
    if (seq !== coursesSequence) return;
    const admin = S.role === "admin",
      base = "#/groups/" + encodeURIComponent(name);
    const tabs = [
      ["general", i18nText("ui.courses.overview")],
      ["subjects", i18nText("ui.courses.subjects_2")],
      ["team", i18nText("ui.courses.team")],
      ...(g.can_students ? [["students", i18nText("ui.accounts.students")]] : []),
      ["lessons", i18nText("ui.app.lessons")],
    ];
    main.innerHTML = `<div class="courses-page" data-course-route="${esc(location.hash)}"><div class="top"><div><a class="account-text-link" href="#/groups">${i18nText("ui.courses.courses")}</a><h1>${esc(g.short_name)}</h1>${g.name !== g.short_name ? `<p>${esc(g.name)}</p>` : ""}</div><span class="chip">${g.completed ? i18nText("ui.courses.completed") : i18nText("ui.courses.active")}</span></div><nav class="course-tabs" aria-label="${i18nText("ui.courses.group_sections")}">${tabs.map(([id, label]) => `<a class="chip${id === tab ? " on" : ""}" ${id === tab ? 'aria-current="page"' : ""} href="${base}?tab=${id}">${label}</a>`).join("")}</nav><section class="card" id="course-content"></section><p id="course-error" class="err-text" role="alert"></p></div>`;
    const box = $("#course-content");
    if (tab === "subjects") {
      const missing = g.subjects.filter((s) => !s.has_playlist).length;
      box.innerHTML = `<div class="row"><h2 class="grow">${i18nText("ui.courses.subjects_2")}</h2>${admin ? `<a class="btn primary" href="${base}?tab=edit">${i18nText("ui.courses.edit_subjects")}</a>` : ""}</div>${missing ? `<p class="alert warn">${missing} ${i18nText("ui.courses.subjects_without_a_playlist")} ${admin ? `<a class="account-text-link" href="${base}?tab=edit">${i18nText("ui.courses.link_playlist")}</a>` : ""}</p>` : ""}<div class="course-rows">${g.subjects.map((s) => `<article class="course-row"><div><h3>${esc(s.name)}</h3><p>${esc(s.lecturer?.name || i18nText("ui.courses.no_lecturer_assigned"))}</p><span class="muted">${esc(groupRange(s.start_date, s.end_date))}</span></div></article>`).join("")}</div>${admin ? `<a class="btn" href="${base}?tab=schedule">${i18nText("ui.courses.change_lecturers_and_dates")}</a>` : ""}`;
    } else if (tab === "team") {
      box.innerHTML =
        groupPeople(g.team) +
        (admin
          ? `<a class="btn" href="${base}?tab=schedule">${i18nText("ui.courses.change_lecturers")}</a><h3>${i18nText("ui.courses.assign_student_assistant")}</h3><button class="btn" id="course-ta-open">${i18nText("ui.courses.choose_assistant")}</button><div class="course-picker" id="course-picker" hidden><label class="fld">${i18nText("ui.courses.search_by_name")}<input class="input" id="course-ta-search" type="search" placeholder="${i18nText("ui.courses.enter_at_least_2_letters")}"></label><div id="course-ta-results" aria-live="polite"></div></div>`
          : "");
      if (admin) {
        $("#course-ta-open").onclick = () => {
          $("#course-picker").hidden = false;
          $("#course-ta-search").focus();
        };
        let timer,
          request = 0;
        $("#course-ta-search").oninput = () => {
          clearTimeout(timer);
          const q = $("#course-ta-search").value;
          const token = ++request;
          timer = setTimeout(async () => {
            try {
              const r = await api(
                "/api/admin/group-students/" +
                  encodeURIComponent(name) +
                  "?q=" +
                  encodeURIComponent(q),
              );
              if (token !== request || !$("#course-ta-results")) return;
              $("#course-ta-results").innerHTML =
                r.items
                  .map(
                    (p) =>
                      `<p class="course-picker-result"><span>${esc(p.name)}</span><button class="btn" data-assign-ta="${p.id}">${i18nText("ui.courses.assign")}</button></p>`,
                  )
                  .join("") || `<p class="muted">${i18nText("ui.courses.no_students_found")}</p>`;
              document
                .querySelectorAll("[data-assign-ta]")
                .forEach(
                  (b) => (b.onclick = () => saveTas([...g.ta_ids, Number(b.dataset.assignTa)])),
                );
            } catch (e) {
              $("#course-error").textContent = courseError(e);
            }
          }, 300);
        };
        box.insertAdjacentHTML(
          "beforeend",
          g.team.tas
            .map(
              (p) =>
                `<button class="btn" data-remove-ta="${p.id}">${i18nText("ui.courses.remove")} ${esc(p.name)}</button>`,
            )
            .join(""),
        );
        async function saveTas(ids) {
          try {
            await api("/api/admin/group-assistants/" + encodeURIComponent(name), {
              method: "PUT",
              body: JSON.stringify({ ta_ids: [...new Set(ids)] }),
            });
            await renderGroupsPage(true);
          } catch (e) {
            $("#course-error").textContent = courseError(e);
          }
        }
        box
          .querySelectorAll("[data-remove-ta]")
          .forEach(
            (b) =>
              (b.onclick = () =>
                saveTas(g.ta_ids.filter((id) => id !== Number(b.dataset.removeTa)))),
          );
      }
    } else if (tab === "students" && g.can_students) {
      box.innerHTML = `<h2>${i18nText("ui.courses.students_2")} ${g.students.length}</h2><div class="course-rows">${g.students.map((p) => `<div class="course-row">${groupPerson(p)}${admin ? `<a class="btn" href="#/users/${p.id}">${i18nText("ui.courses.open_account")}</a>` : ""}</div>`).join("") || `<p class="course-empty">${i18nText("ui.courses.no_students_yet")}</p>`}</div>`;
    } else if (tab === "lessons") {
      const selected = g.subjects.find((s) => s.lecturer?.id === S.user?.id) || g.subjects[0];
      box.innerHTML = `<div class="row"><h2 class="grow">${i18nText("ui.app.lessons")}</h2>${canUpload() && !g.completed ? `<a class="btn primary" href="#/upload?group=${encodeURIComponent(name)}&subject=${encodeURIComponent(selected?.name || "")}">${i18nText("ui.courses.add_lesson")}</a>` : ""}</div>${g.lessons.map((l) => `<article class="course-row"><div><h3>${esc(l.title || l.file)}</h3><span class="muted">${esc(l.subject || "")}</span> ${l.demo ? `<span class="chip">${i18nText("ui.app.demo")}</span>` : ""}</div><a class="btn" href="${l.demo ? "#/library?group=" + encodeURIComponent(name) : "#/jobs/" + l.id}">${i18nText("ui.courses.open_lesson")}</a></article>`).join("") || `<p class="course-empty">${i18nText("ui.courses.no_lessons_yet")}</p>`}`;
    } else {
      box.innerHTML = `<h2>${i18nText("ui.courses.about_the_group")}</h2><p>${esc(groupRange(g.course_start, g.course_end))}</p><div class="course-schedule"><span>${g.counts.students} ${i18nText("ui.courses.students_3")}</span><span>${g.subjects.length} ${i18nText("ui.courses.subjects_3")}</span><span>${g.counts.lessons} ${i18nText("ui.app.lessons_3")}</span></div>${courseAvatarStack(g.team)}${admin ? `<p><a class="btn" href="${base}?tab=schedule">${i18nText("ui.courses.change_dates")}</a><a class="btn" href="${base}?tab=edit">${i18nText("ui.courses.group_settings")}</a></p>` : ""}`;
    }
  } catch (e) {
    if (seq === coursesSequence)
      main.innerHTML = `<div class="alert err" role="alert">${esc(courseError(e))}</div>`;
  }
}
