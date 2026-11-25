# Administration

Sign in with a personal administrator account. The interface defaults to Armenian; select English with **Language** to follow the labels in this guide. [Web dashboard](WEB.md) describes account permissions and language preferences.

## Courses, groups and subjects

Open **Courses** (`#/groups`) for course and group management. **Settings** contains platform-wide options; it is separate from group forms.

1. Choose a course from the catalog, then add a group.
2. Give the group a name starting with a letter or digit: at most 60 characters, using letters, digits, spaces, hyphens, periods or underscores. This also names its inbox directory.
3. Add 1–5 subjects. Names must be unique within the group and at most 40 characters. Each subject may have one YouTube playlist; an empty playlist publishes without playlist insertion.
4. Set the group Slack channel, optional Classroom course ID and lecturer reference sets. Empty lecturer references enable automatic identification. Invite the publishing bot to the configured Slack channel.
5. Save the group. Use its dates and team controls to assign lecturers and teaching assistants and set subject periods. Group completion affects whether new lessons can be uploaded.

Playlist URLs are normalized to their `list=` IDs. Nonempty playlist IDs must be unique within a group. The connected account must be able to edit the selected playlists. See [Setup](SETUP.md) for service access.

Each save validates the configuration and preserves the previous YAML as `config.yaml.bak`. The worker reloads valid changes within 30 seconds. Save group, team and platform forms separately.

### Rename or remove a group

Removing or renaming a subject is blocked while queued, running or stopped lessons use it. Renaming a group moves its inbox and is blocked by unfinished lessons or a conflicting destination. The last group, a group with unfinished jobs, or one with files in its inbox cannot be removed. Group removal retains lesson history, recordings and remote playlists.

Wait for uploads to finish before changing their group or subjects. Completed lessons keep their original group name. A `409` response identifies conflicts that need attention.

## Platform settings

Open **Settings** (`#/settings`) to inspect email delivery and public URL status, control shared sign-in, choose a theme and select the default thumbnail design. SMTP credentials and the trusted public URL are configured locally, not entered into this page.

**Advanced settings** contains the operator Slack channel and these configuration values:

| Key | Meaning |
| --- | --- |
| `operator_slack_channel` | Operational notification destination |
| `cut.min_silence_sec` | Positive silence threshold; silence alone does not justify a cut |
| `cut.max_cut_ratio` | Maximum cut fraction, strictly between 0 and 1; exceeding it preserves the lesson |
| `time_savings.editing_min_per_hour` | Display assumption from 0–240 minutes |
| `time_savings.publishing_min_per_lesson` | Display assumption from 0–120 minutes |

The time-savings defaults, 30 and 5, are assumptions rather than measured savings. The theme applies to the current browser. **Language** selects the personal interface language; `ui_language` in YAML sets the deployment default. Transcription and generated-text languages are separate; see [Language settings](../README.md#language-settings).

## Lecturer review

After upload or confirmed browser recording, the uploader and administrators can review up to three lecturer candidates while processing continues. Candidate crops are private to those accounts.

- Confirm the highlighted person, try the next candidate or choose **Blur everyone**.
- The first final answer wins and is recorded in the audit log.
- At blur entry, an unanswered review automatically uses the highest-ranked candidate. It does not wait for a response.
- A failed scan or an identity that cannot be matched in the processed video falls back to blurring everyone.

Saved confirmed references can be inspected and removed on the administrator privacy page. Removing a reference before blurring removes that job's exemption; it does not rewrite a published video.

Jobs without an upload review have an administrator-only lecturer panel around the blur stage. A saved choice applies only to that job. Changes are restricted to eligible queued/stopped jobs before completed blurring or publication. Do not assume an ambiguous identification always stops processing. See [Privacy behavior](WEB.md#privacy-behavior).

## Delete a lesson

Open the lesson and select **Delete lesson**. The confirmation covers YouTube, Slack and library cleanup. Only administrators can perform this action.

A running job first receives a cancellation request. Cleanup waits for its worker to release it, backs up job database state, and records each removal step. If a service fails, retry from the lesson page; completed steps are retained. A missing Slack channel or unresolved publication receipt requires reconciliation before cleanup can finish.

Source recordings, database history and the local backup remain. Completed deletion supersedes the lesson's digest/publication labels so the recording can be uploaded again. See [Deleting a lesson](WEB.md#deleting-a-lesson).

## Framing and validation

Groups support `framing: "off"`, `auto`, `board` and `projector`, with optional rotation or perspective correction. New groups default to framing off. Unreliable framing retains the full frame. Test group management, lecturer review and privacy outcomes with synthetic data before using a deployment with students.
