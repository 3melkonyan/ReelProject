# Using Reel

Complete [Installation](INSTALLATION.md) first. Commands below run from the repository root. The dashboard and worker are separate processes; stop a foreground process with Ctrl-C.

## Start the dashboard and worker

Dashboard:

```sh
.venv/bin/python -m uvicorn reel.web.app:app --host 127.0.0.1 --port 8765
```

Worker, in a second terminal:

```sh
.venv/bin/python -m reel.cli run
```

Open <http://127.0.0.1:8765>. The normal worker publishes to configured services. Only one worker can hold the project's worker lock at a time.

## Upload through the dashboard

1. Sign in as an administrator or an authorized lecturer/uploader.
2. Select the group and subject, then enter the lesson details.
3. Choose the recording. For a lesson recorded in parts, select all parts and check their order before submitting.
4. Follow upload progress, then the processing status on the lesson page.
5. Resolve stopped stages or lecturer-selection requests before retrying. Inspect the privacy result, framing, title and chapters before sharing the final link.

Chunked uploads can resume when the necessary local files and state remain available. Browser storage is not a backup: mobile operating systems, private browsing, storage cleanup or a closed tab can prevent recovery. Keep the source recording until processing has completed.

## Record in the browser

Use the recording screen from a compatible browser, grant camera and microphone access, and review the recorded parts before confirming submission. Outside localhost, use HTTPS. Avoid locking the phone or switching apps during recording. Wake Lock and local browser storage improve continuity but cannot guarantee recovery.

## Add local files

The worker watches `inbox/<group>/`. Replace `Demo-Group` with a configured group name. A folder containing ordered video parts represents one lesson.

You can also queue a file explicitly:

```sh
.venv/bin/python -m reel.cli add /path/to/lesson.mp4 --group Demo-Group
```

For multiple parts:

```sh
.venv/bin/python -m reel.cli add /path/to/lesson-parts --group Demo-Group
```

The explicit `add` command copies the input to the project archive; it does not alter the user's original file. Duplicate lesson content is detected by its digest. For groups with several subjects, use the dashboard to choose the intended subject explicitly.

## Monitor and recover

```sh
.venv/bin/python -m reel.cli status
.venv/bin/python -m reel.cli status 21
.venv/bin/python -m reel.cli retry 21
```

Replace `21` with the job ID. Retry requeues a job and skips completed stages. Fix the underlying problem before retrying.

If a process stopped during a Slack or Classroom post, inspect the actual destination first. Record whether the post exists, then retry:

```sh
.venv/bin/python -m reel.cli resolve 21 slack posted
.venv/bin/python -m reel.cli retry 21
```

Use `not-posted` only after checking that the post is absent. Valid destinations are `slack` and `classroom`. Incorrect reconciliation can cause duplicate posts or suppress a missing announcement.

## Development without publication

In a **separate checkout** with example settings, no production credentials and synthetic input:

```sh
REEL_DRY_RUN=1 .venv/bin/python -m reel.cli run --once
```

This processes queued jobs until none are runnable and then exits. `REEL_DRY_RUN=1` substitutes the publishing adapters; transcription, rendering, model use, local database changes and file writes still happen. It does not create an isolated environment automatically, and the web process has its own email/notification behavior. Do not treat this flag as a blanket block on every possible network call.

## Common problems

| Symptom | Check |
| --- | --- |
| Configuration cannot be read | Copy the example on a fresh installation, edit it, then run the config smoke check. |
| Worker pauses without starting a job | At least 20 GB must be free; also check whether another worker holds the lock. |
| Transcription will not start | Confirm Apple Silicon, MLX dependencies, model download access and free memory. |
| Local model request fails | Start Ollama and download the selected `REEL_LLM` model. |
| Face model cannot load | Check the exact `.onnx` filenames and confirm the files are binary weights. |
| Administrator email code does not arrive | Check SMTP credentials, STARTTLS, sender policy and mail filtering. |
| Google authorization expires | Stop publication and authorize the intended service again; see [Setup](SETUP.md). |
| Upload or processing is interrupted | Keep the original, inspect job state and resolve any ambiguous external publication before retrying. |
| Encoding is slower than expected | Reel falls back to software H.264 when its VideoToolbox probe fails. Check system resources and restart the worker after restoring hardware access. |

## Retention and backups

Back up the SQLite database consistently, configuration, credentials and required recordings before maintenance. Do not copy only an active SQLite main file while ignoring its WAL. Use SQLite's backup mechanism or shut down writers for a consistent backup.

Automatic cleanup targets completed-job intermediates after seven days and blurred output after thirty days. Originals in `archive/` are retained by default. Explicit cleanup options can remove archived originals; inspect the cleanup command's help and dry-run output before enabling them. See [RUN.md](RUN.md).

For detailed guides, see [Operator](OPERATOR.md), [Web dashboard](WEB.md) and [Administration](ADMIN.md).
