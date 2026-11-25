# Operator guide

Run commands from the repository root using `.venv/bin/python`. See [Setup](SETUP.md) for credentials, [Running](RUN.md) for worker control and [Web dashboard](WEB.md) for interface language selection. This guide uses English labels; Armenian is the default and English is selectable.

## Daily operation

1. Upload a lesson through the dashboard or copy a complete recording into `inbox/<group>/`, where the group matches `config.yaml`.
2. Leave the source unchanged after copying. The watcher waits for stable size and avoids duplicate content.
3. For a lesson in parts, put all parts in one subdirectory: `inbox/<group>/<lesson>/`. Natural filename order puts part 2 before part 10. Separate files outside that subdirectory are separate lessons. The watcher waits five minutes after a folder stops changing. Compatible streams can be joined without re-encoding; other combinations are normalized. Originals remain archived.
4. Follow the job through joining, probing, transcription, segment planning, rendering/audio, blurring, privacy checks, metadata, thumbnails, YouTube, Slack, optional Classroom and the library.
5. Inspect the preview and stop reason in the dashboard at <http://127.0.0.1:8765> or your authorized external address.

```sh
.venv/bin/python -m reel.cli status
.venv/bin/python -m reel.cli status ID
```

Outputs are in `work/ID/`; logs are in `logs/daemon.log`. Wait for `done`: a YouTube URL alone does not establish completion of every downstream stage.

## Lecturer review and lesson removal

Uploaders and administrators can confirm a lecturer after upload, try another candidate or blur everyone. Processing continues during review; an unanswered review uses the top candidate when blur starts. Review identity choices and the privacy result before distributing a lesson.

Administrators can delete a lesson from its page. Cleanup waits for an active worker to release the job, backs up its database state and tracks YouTube, Slack and library removal. Failed steps can be retried; source recordings remain local. See [Administration](ADMIN.md).

## Stopped jobs

Read the specific error before retrying. Conservative fallbacks can retain content or use basic metadata when editing or generation is uncertain.

| Message or condition | Meaning and action |
| --- | --- |
| Target may already be posted | Inspect Slack/Classroom, reconcile the true result, then retry. |
| ffprobe cannot read file | Check FFmpeg availability, permissions and whether the source is a valid recording. |
| Recording shorter than one minute | Confirm the complete intended lesson was supplied. Retry cannot change source duration. |
| Loudness outside target tolerance | Inspect render output and loudnorm measurements before rerunning affected stages. |
| Missing lecturer references | Check the group's lecturer setting and generate valid references if using reference mode. |
| Lecturer rarely recognized | Confirm the actual lecturer and reference set rather than forcing publication. |
| Non-lecturer face visible after blur | Review the privacy report, restricted crops and timestamps; fix blurring/reference problems. Never disable the guard just to publish. |
| Isolated privacy warnings | Review them even if the job did not stop. Repeated isolated warnings are not currently a mandatory stop. |
| Cannot ground chapters or generate valid metadata | Inspect transcript, kept intervals and Ollama. Do not invent topics to fill a chapter quota. |
| Many unclear transcript windows | Check transcript quality and model output. Raising thresholds does not fix poor evidence. |
| Excessive cuts or unexpectedly short result | Compare the proposed plan with the source and inspect conservative fallback information. |

After resolving the cause:

```sh
.venv/bin/python -m reel.cli retry ID
```

Retry preserves completed stages. Changing references after a privacy stop does not automatically rerun an already completed blur stage; changing a transcript does not rerun completed transcription. A developer must safely invalidate affected checkpoints and dependent artifacts where necessary. Do not delete database rows or publication markers manually. Regenerated output must pass privacy checks again.

## Reconcile an ambiguous publication

1. Stop the worker as described in [RUN.md](RUN.md).
2. Inspect the actual lesson title and link in Slack/Classroom.
3. Use `posted` if the message exists, or `not-posted` only after confirming it is absent.

```sh
.venv/bin/python -m reel.cli resolve 21 slack posted
.venv/bin/python -m reel.cli retry 21
```

Replace the ID and use `slack` or `classroom`. Reconcile both separately when necessary, then restart the worker. This command does not repair references or edit plans.

## Add a group or lecturer

Use **Courses** and the administrator group controls or carefully edit configuration while the worker is stopped. Configure 1–5 subjects, optional playlists, the group Slack channel and optional quoted Classroom course ID; preserve the operator channel and cut settings. See [Administration](ADMIN.md).

For reference-based lecturer recognition, generate references from approved footage of the actual lecturer:

```sh
.venv/bin/python tools/make_refs.py PATH_TO_LESSON.mp4 --name lecturer2
```

Privately review the generated reference preview. Multiple clusters may be combined only after confirming they depict the same lecturer:

```sh
.venv/bin/python tools/make_refs.py PATH_TO_LESSON.mp4 --name lecturer2 --clusters 0,1,2,3
```

Those cluster numbers are examples. Never include another person's cluster. Crops and `.npz` descriptors live in `refs/lecturer2/`. Regenerate descriptors after changing reference images; mismatched image/descriptor caches are rejected.

Validate configuration before restarting and inspecting the first consented lesson:

```sh
.venv/bin/python -m reel.config --smoke --config config.yaml
```

## Disk cleanup

Inspect candidates before allowing deletion:

```sh
.venv/bin/python -m reel.cleanup --days 7 --blurred-days 30 --dry-run
.venv/bin/python -m reel.cleanup --days 7 --blurred-days 30
```

Only old completed jobs are cleaned. JSON artifacts, privacy reports, thumbnails and previews are retained. Blurred output has a separate default 30-day retention period. Queued, running and stopped jobs are not cleaned.

Archived originals are retained by default. Explicitly opting into archive deletion requires a separate backup and review:

```sh
.venv/bin/python -m reel.cleanup --archive-days 90 --dry-run
```

Running that archive option without `--dry-run` can delete completed jobs' old original recordings. Keep the JSON reports even when videos are already on YouTube.

## Media formats

Reel accepts supported phone MOV/MP4 inputs, including HEVC/H.265, 10-bit, 4K and HDR where the local FFmpeg build can decode them. Processing normalizes output to H.264 `yuv420p` MP4 with AAC 48 kHz audio as needed. Rotation and aspect ratio are preserved; the shorter side is limited to 1080 pixels, without upscaling smaller input. Portrait 4K becomes 1080×1920 and landscape 4K becomes 1920×1080. The same blurring and final privacy checks apply to multi-part lessons and normalized formats.
