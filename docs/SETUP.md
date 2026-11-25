# Initial service setup

Start with [Installation](INSTALLATION.md) for FFmpeg, Ollama and face model installation. This guide covers Google and Slack integrations. Run commands from the repository root with the virtual environment activated. Keep credentials local and out of chat, YAML and version control.

## 1. Python and credential directory

```sh
cd /path/to/ReelProject
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p secrets
chmod 700 secrets
cp config.example.yaml config.yaml
```

Copy the example only for a fresh installation. `.gitignore` excludes `secrets/`, `.env` and `config.yaml`. Check with:

```sh
git check-ignore secrets/client_secret.json secrets/token_youtube.json .env config.yaml
```

Do not force-add these paths.

## 2. Google Cloud and APIs

1. Open [Google Cloud Console](https://console.cloud.google.com/), create a project called `Reel` and select it.
2. Enable **YouTube Data API v3** under **APIs & Services → Library**. Enable **Google Classroom API** only if using Classroom.
3. Under **Google Auth Platform → Branding**, enter the app name, support email and developer contact.
4. Under **Audience**, choose Internal if available and appropriate for your Workspace organization; otherwise choose External. In Testing mode, add the intended Google accounts as Test users.
5. Add `https://www.googleapis.com/auth/youtube.force-ssl` for uploads, own-channel lookup, playlists and thumbnails. Add `https://www.googleapis.com/auth/classroom.announcements` only for Classroom announcements.
6. Under **Clients → Create client → Desktop app**, create `Reel local`, download its JSON and save it as `secrets/client_secret.json`. This is a desktop OAuth client, not an API key or service account.
7. Restrict access with `chmod 600 secrets/client_secret.json`.

References: [Classroom Python quickstart](https://developers.google.com/workspace/classroom/quickstart/python), [Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app), [YouTube authorization](https://developers.google.com/youtube/data/guides/authentication).

## 3. First Google authorization

Authorize YouTube and Classroom separately. A YouTube brand account does not have Classroom; combining scopes can produce a service-unavailable error. Classroom authorization is unnecessary when the integration is disabled.

```sh
python -m reel.google_auth --authorize youtube
# Optional: choose a personal Google account that teaches the configured course.
python -m reel.google_auth --authorize classroom
```

Without a service argument, `--authorize` runs both flows in sequence. Each allows ten minutes. In Testing mode, the sign-in email must be an approved test user. Tokens are saved separately as `secrets/token_youtube.json` and `secrets/token_classroom.json`, with mode 600. Later calls refresh them automatically. The daemon never opens an authorization browser; missing authorization produces a command for the operator to run.

To change accounts/channels, add the intended account to Test users if required, stop publication, rerun the matching authorization command and choose the correct identity. The token is replaced without changing application code. Update playlist, Slack and Classroom destinations as appropriate.

If scopes change or access is revoked, stop Reel and authorize the affected service again. External/Testing refresh tokens may expire after seven days. Check [Google OAuth](https://developers.google.com/identity/protocols/oauth2) for production/verification requirements and any Workspace restrictions.

## 4. YouTube and Classroom identifiers

Create the intended YouTube playlist and put its URL's `list=` value in the subject's `playlist` field in `config.yaml`. The authorized account must be allowed to edit it. The legacy `playlist_id` form is also accepted for one-subject groups.

Classroom needs the numeric API course ID, not a join code or encoded URL segment. A manual API Explorer `courses.list` request can use `classroom.courses.readonly` to retrieve it; Reel itself does not need that additional scope. Quote numeric IDs in YAML.

Check an authorized test upload's actual visibility in YouTube Studio and open the result outside the signed-in account. Consult [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert) for API restrictions, and verify channel eligibility for custom thumbnails.

## 5. Slack app

1. Open [Slack Apps](https://api.slack.com/apps) and choose **Create New App → From scratch** in the intended workspace.
2. Add **chat:write** under **OAuth & Permissions → Bot Token Scopes**. For administrator direct-message alerts, also grant `users:read`, `users:read.email` and `im:write`: Reel looks up the administrator by email and opens a direct conversation. See [email lookup permissions](https://docs.slack.dev/reference/scopes/users.read.email/) and [direct-message permissions](https://docs.slack.dev/reference/scopes/im.write/).
3. Install the app to the workspace. Use the Bot User OAuth Token, not an app token. Reinstall after scope changes.
4. Edit `.env` locally and set `SLACK_BOT_TOKEN`. Restrict it with `chmod 600 .env`.
5. Invite the bot with `/invite @Reel` in every group and operator channel, including private channels.
6. Set group `slack_channel` and a separate `operator_slack_channel` from each channel's details.

References: [Slack app setup](https://docs.slack.dev/app-management/quickstart-app-settings/) and [chat:write](https://docs.slack.dev/reference/scopes/chat.write/).

## 6. Offline checks and live validation

```sh
python -m reel.publish.errors --smoke
python -m reel.config --smoke
python -m reel.google_auth --smoke
python -m reel.publish.youtube --smoke
python -m reel.publish.classroom --smoke
python -m reel.publish.library --smoke
python -m reel.config --smoke --config config.yaml
```

Smoke checks are offline; OAuth smoke does not open a browser. For the Slack adapter, install the development dependencies as described in [Contributing](../CONTRIBUTING.md), then run `REEL_DRY_RUN=1 python -m pytest -q tests/test_slack_publish.py`. Only explicit `--authorize` performs live authorization, without publication. Separately validate an actual upload, playlist insertion, Slack post and optional Classroom announcement before declaring the deployment ready.

Publisher smoke checks mock external APIs. Library smoke writes only in a temporary directory. Runtime `library.jsonl`, `.env` and `secrets/` resolve from the repository root regardless of the caller's working directory. `notify()` reads the operator channel from configuration. Adapters do not own database state or pipeline retries.
