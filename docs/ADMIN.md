# Group, subject and lecturer administration

Sign in as an administrator and open **Settings** (`#/settings`). Viewer and uploader accounts cannot manage this page. English labels in this guide describe the Armenian controls shown in the application; the interface itself remains Armenian. See [Web dashboard](WEB.md).

Save changes explicitly. The worker reloads valid configuration within **30 seconds**. Each save preserves the previous version as `config.yaml.bak`; background page refreshes do not discard the current form.

## Create a group

1. Select **New group** and enter a name. It also names the inbox directory. Start with a letter or digit; use at most 60 characters consisting of letters, digits, spaces, hyphens, periods or underscores.
2. Add **1–5 subjects**, such as Python and JavaScript. Names are at most 40 characters and must be unique within the group. Nonempty playlist IDs must also be unique. A playlist can be left empty until one is available.
3. Choose playlists from the connected YouTube channel or paste their URLs/IDs. The URL's `list=` value becomes the stored ID. The selector shows at most 200 playlists.
4. Enter the group's **Slack channel ID**. Find it under the channel's About/Details panel and invite the bot with `/invite @Reel`. All subjects share this group channel.
5. Choose automatic lecturer detection or existing lecturer reference directories. Multiple reference sets are supported. This page selects references; it does not create photographs. See [Operator guide](OPERATOR.md).
6. Optionally enter a **Classroom course ID**. Omit it to disable Classroom announcements. The authorized Google account must be a teacher on that course.
7. Select **Save**. The success notice says the change will apply within 30 seconds.

The subject selected for an upload determines that lesson's single playlist. Subjects use the connected YouTube channel. See [Access checklist](KEYS.md) for account preparation.

## Edit, rename or delete

- Edit subjects, destinations and settings, then save. A validation error leaves the existing configuration unchanged.
- Removing or renaming a subject is blocked while queued, running or stopped lessons use it. The response lists affected job IDs. Completed lessons do not block the change.
- Renaming a group moves `inbox/<old>` to `inbox/<new>`. It is blocked by unfinished lessons, an existing group with the new name or a conflicting destination directory.
- Deletion requires confirmation. The last group, a group with unfinished jobs, or a group whose inbox contains files cannot be deleted. Lesson history, recordings and YouTube playlists are retained.

Wait for web uploads to finish before these changes. Completed lessons retain their original group name. A `409` response usually identifies a job or directory that must be handled first. See [retry and reconciliation](OPERATOR.md).

## General settings

- `operator_slack_channel`: destination for operational notifications; invite the bot there too.
- `min_silence_sec`: positive silence duration considered by segment planning. Silence alone is not sufficient evidence to remove teaching material.
- `max_cut_ratio`: a value strictly between 0 and 1. For example, `0.4` means 40%. If planned cuts exceed the limit, the conservative fallback preserves the lesson without those cuts.

General settings and individual group forms have separate Save buttons. API credentials are managed locally, not through this page.

## Troubleshooting

| Situation | Action |
| --- | --- |
| Access denied | Use an administrator account. |
| Invalid input (`400`) | Check subject count, duplicate names/playlists and highlighted fields. |
| Unfinished lessons (`409`) | Open the listed jobs and resolve them first. |
| Inbox or destination conflict (`409`) | Process existing input or resolve the directory conflict. |
| Playlist list unavailable (`502`) | Enter the playlist ID/URL manually and check Google authorization. |
| Save succeeded but list looks stale | Wait or reopen Settings; do not repeat a successful mutation. |

## Lecturer selection

Open a lesson as an administrator. The **Lecturer** panel becomes available around the blur stage. Viewers and uploaders cannot access the identity crops, which may contain student faces. Do not distribute these images.

The panel distinguishes automatic selection, an administrator's choice, configured references and no lecturer selected. Automatic presence percentages refer to sampled frames containing faces, not the whole lesson duration.

When detection is ambiguous, the job stops before publication. The panel lists identity ranks, crops, presence counts/shares and first appearance times:

1. Identify the lecturer and select **This is the lecturer**.
2. Alternatively choose **No lecturer — blur everyone** and confirm.
3. The choice applies only to this job; the page attempts to resume processing.
4. A queued job applies the choice when it reaches blurring. If the connection drops after saving, refresh, verify the choice/status and retry if needed.

A queued or stopped job may expose **Cancel selection** while blurring is unfinished and no YouTube publication exists. Cancelling restores reference/automatic rules but does not itself resume the job. A running, already blurred or published job cannot have its choice changed through this panel.

## Board-only framing

In **Settings → Edit group**, enable board framing to detect the board, correct tilt and compose a 16:9 view. New groups default to framing off. If the board cannot be located confidently or the crop is unsuitable, Reel keeps the full frame without an approval request. The lesson's rendering stage shows the applied framing and reason to administrators and viewers.

## Deployment validation

Test group management and lecturer selection with temporary data and synthetic identity images before enabling a deployment. Validate the connected publication channel separately.
