# Running Reel

Run commands from the repository root after [setup](SETUP.md). You need the virtual environment, `config.yaml`, YouTube authorization, FFmpeg/ffprobe and installed dependencies. Classroom authorization is required only when that integration is enabled. A macOS user LaunchAgent needs a signed-in user session. Processing stops while the computer sleeps.

## Foreground worker

```sh
.venv/bin/python -m reel.cli run
```

Stop with Ctrl-C. This is the real publishing worker, not a simulation. See [Usage](USAGE.md) for isolated development mode.

## Automatic startup

```sh
bash deploy/install.sh
```

The installer detects the repository root, expands `__ROOT__` placeholders and writes `~/Library/LaunchAgents/com.reel.daemon.plist`. The agent runs `.venv/bin/python -m reel.cli run`, keeps it alive and starts it on sign-in. After moving the project, uninstall the old agent, recreate the virtual environment at its final path and reinstall. Do not start a second worker manually.

## Status and logs

```sh
.venv/bin/python -m reel.cli status
.venv/bin/python -m reel.cli status ID
launchctl print "gui/$UID/com.reel.daemon"
tail -f logs/daemon.log
```

Replace `ID` with a job number. Standard output and errors go to `logs/daemon.log`; monitor its size. The agent PATH includes `/opt/homebrew/bin` and `/usr/local/bin` for FFmpeg.

## Stop and uninstall the agent

```sh
bash deploy/uninstall.sh
```

Recordings and database state are retained.

## Retry after fixing a problem

```sh
.venv/bin/python -m reel.cli retry ID
```

Completed stages are skipped. If a crash occurred during Slack/Classroom publication, stop the worker and inspect the actual destination first. Do not delete ambiguous-publication markers manually.

The reconciliation command format is:

```text
.venv/bin/python -m reel.cli resolve ID TARGET posted|not-posted
```

Use `slack` or `classroom` for TARGET and exactly one final state. `posted` records an existing message; `not-posted` permits a new attempt after you verified the message is absent.

```sh
.venv/bin/python -m reel.cli resolve 21 slack posted
.venv/bin/python -m reel.cli retry 21
bash deploy/install.sh
```

Reconcile each ambiguous destination separately before retrying.

## Thumbnail smoke check

```sh
.venv/bin/python -m reel.thumbnail --smoke
```

This creates a temporary ten-second test-pattern video and verifies sharp-frame selection, Armenian glyph support, JPEG output, 1280×720 dimensions and a size below 2 MB.
