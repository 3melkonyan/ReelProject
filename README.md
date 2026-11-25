<p align="center">
  <img src="reel/web/static/brand/reel-signal-mark.svg" alt="Reel" width="88">
</p>

# Reel

**From a classroom recording to a searchable lesson library.**

Reel processes lecture recordings on an Apple Silicon Mac. It combines recording parts, transcribes speech (Armenian by default), prepares conservative edits, adjusts framing and audio, and applies face blurring before publishing lessons to YouTube and sharing them with a class.

Designed for schools and training centers, Reel includes a dashboard for administrators, lecturers and students. The interface is Armenian by default; English can be selected. Processing runs locally with FFmpeg, MLX Whisper, OpenCV and Ollama; YouTube, Slack, Google Classroom and email are external integrations.

[Service setup](docs/SETUP.md) · [Operator guide](docs/OPERATOR.md) · [Installation](docs/INSTALLATION.md) · [Usage](docs/USAGE.md) · [Architecture](docs/ARCHITECTURE.md)

## What it does

- **Accept recordings:** upload files, record in a compatible browser, or place recordings in a group's inbox. Multi-part lessons are joined in filename order.
- **Process lessons:** transcribe speech, identify clearly unrelated material, normalize audio, and optionally frame a board or projector. Uncertain edits retain the material.
- **Prepare publishing assets:** generate a title, description, chapters, thumbnail and preview. The library supports caption search and chapter navigation.
- **Apply privacy checks:** detect and blur confidently identified student faces, with lecturer selection and a final scan before publication. This is an aid to review, not guaranteed anonymization: uncertain or missed faces can remain visible.
- **Deliver the result:** upload an unlisted YouTube video, optionally add it to a playlist, post to Slack and Google Classroom, and register it in the lesson library.
- **Manage access and operations:** personal accounts, group-scoped access, administrator email verification, processing progress, audit records, retry checkpoints and completion notifications.

## Processing flow

```text
File upload / browser recording / group inbox
                    ↓
       Queue and per-stage checkpoints
                    ↓
     Join → transcribe → plan conservative edits
                    ↓
     Render and audio → face blur → privacy scan
                    ↓
       Metadata → thumbnail → preview
                    ↓
       YouTube → Slack / Classroom → library
```

The web application and processing worker run separately. Opening the dashboard does not start processing. Failed or interrupted jobs retain completed stage checkpoints; ambiguous external publications require operator reconciliation before retry.

## Requirements

| Component | Requirement |
| --- | --- |
| Operating system | macOS on Apple Silicon; other platforms are not validated |
| Python | Python 3.12 is the tested development version |
| Media tools | FFmpeg and ffprobe; VideoToolbox is preferred, with a software H.264 fallback |
| Local models | MLX Whisper, Ollama (`gemma3:12b` by default), OpenCV YuNet and SFace |
| Storage | The worker pauses below 20 GB free; large recordings require additional space |
| Publishing | Your own Google OAuth credentials and Slack bot configuration |
| Account emails | SMTP with STARTTLS for administrator codes, invitations and recovery |

Memory and processing time depend on video resolution, lesson length and model choice. Model weights are downloaded separately and are not included in this repository.

## Download and install

```sh
git clone https://github.com/3melkonyan/ReelProject.git
cd ReelProject
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env
```

If you use `uv`, install dependencies with `uv pip install --python .venv/bin/python -r requirements.txt` instead. Downloading a ZIP from GitHub's **Code → Download ZIP** menu also works; run the remaining commands from the extracted project directory.

**Continue with [Installation](docs/INSTALLATION.md)** to install FFmpeg, Ollama and face models, configure groups and connect publishing services. The commands above install Python dependencies only.

## Run locally

After configuration, start the dashboard:

```sh
.venv/bin/python -m uvicorn reel.web.app:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. On first launch, Reel creates local access settings in `secrets/web.json`. Use the initial administrator password locally to create the first personal account; configure SMTP before relying on subsequent administrator email verification.

In a second terminal, from the project directory, start the worker:

```sh
.venv/bin/python -m reel.cli run
```

This worker performs real configured publications. For development, `REEL_DRY_RUN=1` replaces publishing adapters with local fakes, but **still processes recordings and writes job data**. Use a separate checkout with synthetic recordings, a separate database and no production credentials. See [Usage](docs/USAGE.md).

## Everyday use

1. Configure a group and its subjects, publishing destinations and lecturer references.
2. Upload a lesson through the dashboard or place its files in `inbox/<group>/`.
3. Follow processing progress and resolve any stopped job or lecturer-selection request.
4. Check the final privacy result and generated metadata before distributing the published lesson.
5. Students find completed lessons in their group's library.

See [Usage](docs/USAGE.md) for CLI examples, retry behavior, recording limitations and troubleshooting.

## Development

```sh
uv pip install --python .venv/bin/python -r requirements-dev.txt
REEL_DRY_RUN=1 .venv/bin/python -m pytest -q
.venv/bin/python -m reel.config --smoke
```

Tests use temporary databases, synthetic media and mocked publishing services. Some start local HTTP servers and some require FFmpeg and a usable H.264 encoder; a restricted execution environment can prevent those tests from running. GitHub Actions runs a smaller cross-platform core suite; the complete media suite is a separate macOS check. See [Contributing](CONTRIBUTING.md).

## Repository layout

```text
reel/
  cli.py, pipeline.py, db.py   Queue, worker and persistent job state
  stages/                    Media processing and privacy checks
  publish/                   YouTube, Slack, Classroom and library adapters
  web/                       FastAPI dashboard, accounts and static assets
  assets/                    Thumbnail assets and bundled font notices
tests/                       Automated checks and synthetic fixtures
docs/                        Installation, operation and architecture
deploy/                      Optional macOS LaunchAgent scripts
tools/                       Development and evaluation utilities
```

The tracked example configuration contains placeholders. Actual configuration, credentials, databases, recordings, face references and user avatars belong to the local installation and are excluded from Git.

## Limits and data handling

- Face recognition and transcription are imperfect. The current policy can leave uncertain faces visible; review consent requirements and outputs before use with real students.
- An **unlisted** YouTube video can be watched by anyone with its link. Group permissions protect the Reel interface, not a YouTube link after it has been shared.
- Browser recording requires camera/microphone permission and, away from localhost, HTTPS. Mobile sleep, storage eviction and browser closure can interrupt recording or recovery.
- Originals remain in the archive by default. Completed intermediate files and blurred output have separate retention rules; review [operation](docs/RUN.md) before cleanup.
- Monitoring runs inside the web process. It cannot report a complete host or web-process outage.
- This application contains local AI processing; it does not depend on a paid hosted LLM API by default. Publishing and email still communicate with their respective providers.

## Project information

Maintained by [Gevorg Melkonyan](https://github.com/3melkonyan). For reproducible bugs or feature requests, use [GitHub Issues](https://github.com/3melkonyan/ReelProject/issues). Report sensitive findings through [Security](SECURITY.md), without posting credentials or student data.

## License

Reel is released under the [MIT License](LICENSE). Bundled fonts keep their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).

## Languages

The interface is Armenian by default; English can be selected. Use **Հայերեն / English** in the header. Before sign-in, the choice is saved in localStorage on the current browser; after sign-in, it is saved to your account and follows you across devices. Emails use the recipient’s account language, falling back to the deployment default.

Set `ui_language: hy` or `ui_language: en` in your configuration to choose the deployment default. Existing configurations without this option use Armenian.

Lesson content has independent settings:

- `transcription_language: hy`: a two- or three-letter language code, or `auto` to detect the recording’s language.
- `transcription_prompt`: optional recognition vocabulary; use `null` to disable it. The existing Armenian programming vocabulary is the default for Armenian transcription and is omitted for other languages unless supplied explicitly.
- `metadata_language: hy`: `hy` or `en` for generated titles, descriptions, study notes, numbered publication titles, and lesson announcements. The English path builds titles and chapters from grounded English topics. Existing content is retained.

Group names, subject names, recordings, transcripts, and existing lesson titles are content and are not translated when the interface language changes. See [the dashboard guide](docs/WEB.md) for account behavior.
