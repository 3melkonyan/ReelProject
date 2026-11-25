# Web dashboard

The interface remains Armenian. This guide uses English descriptions of the controls and documents their behavior without changing on-screen labels.

## Start locally

```sh
.venv/bin/python -m uvicorn reel.web.app:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. This starts neither a public tunnel nor the processing worker. See [Usage](USAGE.md).

## Optional external link

```sh
bash tools/share.sh
```

This starts the sharing workflow and prints an HTTPS Cloudflare quick-tunnel address. Only share it with intended users after reviewing authentication. It changes on restart and works only while the Mac is awake and connected. Local access remains available without that tunnel.

```sh
bash tools/share.sh stop
```

Web and tunnel logs are in `logs/web.log` and `logs/tunnel.log`.

## Initial and legacy credentials

Initial settings are stored locally in `secrets/web.json`, excluded from Git. Keep them private. An initial administrator password opens account setup only while the users table is empty. Personal administrator sign-in subsequently uses password plus email verification, subject to remembered-device policy.

Legacy shared viewer/uploader/student passwords remain for migration and can be disabled by a personal administrator. Viewer access is operational read-only; uploader access includes authorized uploads but excludes administrator identity crops. Legacy shared access does not have personal-account group membership. The old shared-password limiter blocks repeated incorrect attempts; personal login has its own stricter limits.

Changing local credential settings requires a controlled web restart. Runtime environment overrides include `REEL_ADMIN_PASSWORD`, `REEL_VIEWER_PASSWORD`, `REEL_UPLOADER_PASSWORD` and `REEL_STUDENT_PASSWORD`; overrides are not persisted by the loader.

## Upload a lesson

1. Sign in with upload permission and select the group, subject and lesson details.
2. Select the recording, or all parts of one lesson. Check the natural filename order before submitting.
3. Submit the chunked upload and follow progress. If the connection fails, reselecting the same available files can resume an existing upload.
4. After completion, the lesson enters the processing queue. Content-digest deduplication prevents processing the same lesson again.

Supported file extensions include MP4, MOV, M4V and MKV; the upload flow supports up to eight parts. The processing minimum is one minute. Upload limits and storage constraints still apply. The browser cannot guarantee recovery of unsent data after storage eviction, closure or a mobile operating-system interruption.

Browser recording also requires camera/microphone permission and HTTPS outside localhost. Review parts and confirm submission; unconfirmed recording parts are not queued lessons.

## Lecturer identification

Selection priority is the job's explicit administrator choice, matching configured lecturer references, then automatic discovery. Automatic discovery samples approximately every two seconds and clusters identities; the intended lecturer is the clearly dominant person near the teaching area. The original rule required at least twice the next identity's presence and 40% of sampled face-containing frames. Current geometry/quality checks and saved choices also affect selection; use the job report rather than assuming presence alone proves identity.

An ambiguous selection can stop the job before blurring/publication so an administrator can choose a candidate or blur everyone. With few/no faces, the no-lecturer fallback blurs detected faces. Identity crops may include students and are administrator-only. See [Administration](ADMIN.md) for choice, cancellation and retry behavior.

## Main views

- Overview: current processing, recent events and operational statistics.
- Lessons and lesson detail: progress, stages, stop reasons, final preview, chapters, cut timeline and publication outcomes.
- Library: authorized completed lessons, chapter navigation, search and personal viewing progress.
- Settings and users: administrator configuration and personal-account management.
- Help/about: workflow and limitations.

## Privacy behavior

Original/unblurred recordings are not served as dashboard previews. Viewer/student media requires the appropriate privacy and group checks. Identity crops are restricted to administrators.

The current confident-student policy can leave uncertain faces visible. Sustained unexplained student events can stop a job; isolated one- or two-sample events may warn without stopping. An automatic pass is not guaranteed anonymization. Review consent requirements and output before distributing a link.

Dry-run publications are labeled as test results and have no live destination link.

## Conservative fallbacks and framing

Ambiguous text, excessive planned cuts or an unexpectedly short result can preserve the complete lesson. When reliable metadata cannot be generated, the system can use a basic lesson/date title and description without chapters. The automatic-decision explanation appears on the lesson page. Invalid configuration and critical processing errors still stop work.

Optional board framing detects the teaching surface, corrects tilt and creates a suitable crop. If detection/cropping is unsafe or unsuitable, the full frame is retained. Additional framing modes and temporal plans are described in technical contracts. New groups default to framing off.

## Library, cuts and statistics APIs

| Endpoint | Access and behavior |
| --- | --- |
| `GET /api/library` | Authorized library roles; passed privacy and published YouTube data are required. Personal group scope applies. |
| `GET /api/stats` | Administrator/viewer operational statistics; unavailable measurements remain null. |
| `GET /api/jobs/{id}/cuts` | Authorized operational roles; original and edited timeline details. |
| `POST /api/jobs/{id}/cuts/{index}/preview` | Authorized roles plus CSRF header; generates a short, bounded preview only from blurred output. |
| `GET /api/jobs/{id}/cuts/{index}/preview` | Serves an existing safe cache after rechecking privacy/access. |

Mutation requests use `X-Requested-With: reel`. Transition previews are at most ten seconds at 480p; one low-priority encoder is allowed at a time. Typical failures include 409 busy, 410 missing final media, 504 timeout, 403 privacy/symlink rejection and 404 missing job/cut/cache. Cache entries under `out/transition-previews/<id>/` incorporate media modification time/size and transition time. There is no original-video fallback.

Chapter links use `final_t`, not original `t`. Old records expose only stored data. Student accounts cannot use operational APIs. Public static assets and `/healthz` do not disclose lesson content.

## Progress and time-savings estimates

Job progress contains observed stage fraction, weighted completion and approximate ETA. Unobserved stages do not advance merely because time passes. YouTube-processing wait is a real observation and is capped before completion. Completed jobs show 100%; missing old progress stays unknown. The interface animates only toward already observed values and respects reduced motion.

Optional `time_savings` settings contain `editing_min_per_hour` (0–240) and `publishing_min_per_lesson` (0–120), finite numbers excluding booleans. Defaults of 30 and 5 are assumptions, not measured human savings. Only administrators can change them.

## Theme and navigation

The default theme is dark. An explicitly saved light/dark choice overrides it; operating-system theme changes do not replace a saved choice. Mobile navigation exposes up to four direct destinations and a More menu. Stage labels come from `reel/web/labels.py`; identifiers remain stable.

## Personal accounts

Personal roles are administrator, lecturer and student; TA membership uses student-level access. There is no open signup. Administrators invite users and assign groups. Personal lecturers' operational/upload access is scoped to their groups, and students' library access to their assigned group. Foreign-group resources return 404 where appropriate; aggregate data is also scoped.

Routes include `#/users` for administrators, `#/profile` for personal accounts, public invitation/reset/forgot flows and a bootstrap-only first-account screen.

The signed `reel_session` cookie is HttpOnly, SameSite=Lax and Secure under HTTPS. Every personal request revalidates `user_id` and `session_version` against the database. Five incorrect personal login/IP attempts trigger a fifteen-minute pause. Role/group/status/password changes revoke prior session versions.

| API | Purpose |
| --- | --- |
| `/api/login`, `/api/me`, `/api/logout` | Sign-in, current identity and sign-out |
| `/api/accounts/bootstrap` | Transactional first-admin creation using bootstrap access |
| `/api/accounts/invitation`, `/api/accounts/register` | Read/accept a single-use invitation |
| `/api/accounts/forgot`, `/api/accounts/reset` | Non-enumerating recovery request and password reset |
| `/api/accounts/profile`, `/api/accounts/password` | Own profile/password management |
| `/api/accounts/avatar`, `/api/accounts/avatars/{id}` | Bounded JPEG/PNG/WebP profile upload and authenticated serving |
| `/api/accounts/users`, `/api/accounts/users/{id}` | Administrator user listing and role/group/active-state changes |
| `/api/accounts/users/{id}/reset` | Administrator-created reset link |
| `/api/accounts/invitations`, `/api/accounts/invitations/{id}/revoke` | Invitation management |
| `/api/accounts/legacy` | Enable/disable legacy shared access |

All mutations, including login, forgot, register and reset, require the existing request header. The last active administrator is protected. Profile avatars are not identity-recognition crops.

## SMTP and trusted links

Configure `SMTP_HOST`, `SMTP_PORT` (normally 587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` and `REEL_PUBLIC_URL` locally. SMTP uses STARTTLS. Invitation/reset URLs use only a trusted configured base; they are not built from arbitrary Host or forwarded-host headers. The optional tunnel URL fallback accepts only the expected HTTPS quick-tunnel form written by the sharing script.

A stable public URL avoids breaking old links when a quick tunnel changes. Without SMTP/trusted URL, email is skipped without revealing whether an account exists. Administrators can still obtain a relative reset link for local handling. Invitations last 72 hours, resets 60 minutes; both are single-use. Only token hashes are stored, and raw links are shown only when created.

Never expose `avatars/`, `work/` or identity directories through a separate public static server that bypasses application permissions.
