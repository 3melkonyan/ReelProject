# Installation

Run commands from the repository root. The supported processing environment is an Apple Silicon Mac with Python 3.12. Reel probes VideoToolbox once per worker and falls back to software H.264 encoding when it is unavailable; software encoding can be slower. A virtual environment moved from another directory should be recreated at its final path.

## 1. System tools

Install Python 3.12, FFmpeg and Ollama using their official installers or, if Homebrew is already installed:

```sh
brew install python@3.12 ffmpeg ollama
python3.12 --version
ffmpeg -version
ffprobe -version
```

Start Ollama in a separate terminal and download the default language model:

```sh
ollama serve
```

```sh
ollama pull gemma3:12b
```

Skip `ollama serve` if Ollama is already running. Keep it on its default loopback address. `REEL_LLM` selects the language model and `OLLAMA_URL` selects the server. When using the worker directly, export overrides in the terminal before starting it; a key in `.env` is not a universal override for every module.

## 2. Python environment

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env
mkdir -p secrets models
chmod 700 secrets
chmod 600 .env
```

Only copy examples for a fresh installation; do not overwrite an existing configuration. With `uv`, use `uv pip install --python .venv/bin/python -r requirements.txt`. The first transcription downloads `mlx-community/whisper-large-v3-mlx`; allow time, network access and disk space for its model cache.

## 3. Face models

Download these model files from the official [OpenCV Zoo](https://github.com/opencv/opencv_zoo) model pages:

| Model page | Save as |
| --- | --- |
| [YuNet face detection](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | `models/face_detection_yunet_2023mar.onnx` |
| [SFace face recognition](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) | `models/face_recognition_sface_2021dec.onnx` |

Download the actual binary weights, not a Git LFS pointer or GitHub HTML page. Review the upstream model licenses. Reel loads these files locally; it does not bundle them or automatically install them.

## 4. Groups and publishing

Edit `config.yaml` with your group names, subjects and Slack channel IDs. A subject's `playlist` may be empty. Omit `classroom_course_id` if Classroom announcements are not needed. `lecturers` names reference directories inside `refs/`; an empty list enables automatic lecturer detection.

Set `ui_language: hy` or `en` for the deployment interface default, `transcription_language` for speech recognition, and `metadata_language: hy` or `en` for generated lesson text. An optional `transcription_prompt` supplies recognition vocabulary; `null` disables it. See [Language settings](../README.md#language-settings).

The example uses `framing: "off"` to preserve the source framing. Choose framing options only after testing them on representative synthetic or consented footage.

Validate the file without publication:

```sh
.venv/bin/python -m reel.config --smoke --config config.yaml
```

Follow the [Google and Slack setup guide](SETUP.md#2-google-cloud-and-apis) for OAuth scopes, account authorization, playlist IDs and bot installation. Google tokens for YouTube and Classroom are separate. Authorize only services you intend to use:

```sh
.venv/bin/python -m reel.google_auth --authorize youtube
# Optional, only if Classroom is configured:
.venv/bin/python -m reel.google_auth --authorize classroom
```

Put the Slack bot token in `.env`, not in YAML. Invite the bot to the configured group and operator channels. The example channel IDs are placeholders and cannot receive messages.

## 5. Email and first account

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM` in `.env`. Reel uses STARTTLS. Set `REEL_PUBLIC_URL` to your intended HTTPS origin for usable invitation and recovery links when serving other users.

Start only the local dashboard first:

```sh
.venv/bin/python -m uvicorn reel.web.app:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. Initial credentials are generated locally in `secrets/web.json`; keep them private. With no personal users yet, the initial admin password opens first-account setup. Later personal administrator sign-ins require email verification unless covered by the application's remembered-device policy. Test SMTP before deployment and retain local access to your configuration.

The dashboard defaults to Armenian; select English with **Language** on sign-in or in the navigation. Administrators create invitations and assign lecturers and students to groups. Legacy shared passwords exist for migration; disable them after personal accounts have been established.

## 6. First lesson

Use a synthetic recording of at least one minute in an isolated development checkout before using real student footage. Start the worker only when destinations and processing settings have been checked. See [Usage](USAGE.md) for the worker, input methods and dry-run scope.

## Optional background operation

[RUN.md](RUN.md) describes the macOS LaunchAgent installer. Install it only when automatic processing is intended. The installer starts a persistent worker; it is not a prerequisite for interactive use. Relocating the repository requires reinstalling the agent at the new path.

`tools/share.sh` also creates an external Cloudflare tunnel. Use the local dashboard command above unless external access is intended. HTTPS is required for browser camera access outside localhost.
