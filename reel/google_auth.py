"""Desktop OAuth credentials, one token per Google service, stored outside version control.

YouTube and Classroom are authorized separately (secrets/token_youtube.json,
secrets/token_classroom.json): a YouTube channel is often a brand account, and a
brand account cannot use Classroom -- Google answers "service unavailable" when
both scopes are requested in one consent.
"""
from pathlib import Path
import json
import os
import tempfile
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from reel.publish.errors import PublishError

SERVICES = {
    "youtube": ["https://www.googleapis.com/auth/youtube.force-ssl"],
    "classroom": ["https://www.googleapis.com/auth/classroom.announcements"],
}
ACCOUNT_HINT = {
    "youtube": "YouTube — ընտրիր այն ԱԼԻՔԸ, որտեղ Reel-ը պետք է հրապարակի (brand ալիքը նույնպես կարելի է)",
    "classroom": "Classroom — ընտրիր քո անձնական հաշիվը, որը դասընթացում ՈՒՍՈՒՑԻՉ է (ոչ brand ալիքը)",
}


def service_of(scopes: list[str]) -> str | None:
    """The single service all scopes belong to ('youtube' / 'classroom'), else None."""
    found = set()
    for scope in scopes:
        names = [name for name in SERVICES if isinstance(scope, str) and f"/auth/{name}" in scope]
        found.add(names[0] if len(names) == 1 else None)
    return found.pop() if len(found) == 1 else None


def token_name(scopes: list[str]) -> str:
    service = service_of(scopes)
    return f"token_{service}.json" if service else "token.json"


def _authorize_command(scopes: list[str]) -> str:
    service = service_of(scopes)
    return "python -m reel.google_auth --authorize" + (f" {service}" if service else "")


def _save(credentials: Credentials, path: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".token-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(credentials.to_json())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_credentials(scopes: list[str]) -> Credentials:
    return _get(scopes, interactive=False)


def _get(scopes: list[str], interactive: bool) -> Credentials:
    if not scopes or any(not isinstance(scope, str) or not scope.strip() for scope in scopes):
        raise PublishError("At least one non-empty Google OAuth scope is required", False)
    directory = Path(__file__).resolve().parents[1] / "secrets"
    token_path = directory / token_name(scopes)
    client_path = directory / "client_secret.json"
    command = _authorize_command(scopes)
    credentials = None
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        if token_path.exists() and not interactive:
            token_path.chmod(0o600)
            credentials = Credentials.from_authorized_user_file(str(token_path))
            if not credentials.has_scopes(scopes):
                raise PublishError(f"Google scopes changed; run {command} to grant all scopes", False)
        if credentials and credentials.valid:
            return credentials
        if credentials and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not interactive:
                raise PublishError(f"run {command}", False)
            if not client_path.is_file():
                raise PublishError("Missing secrets/client_secret.json; follow docs/SETUP.md", False)
            client_path.chmod(0o600)
            flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=scopes)
            credentials = flow.run_local_server(host="localhost", port=0, access_type="offline", prompt="consent", timeout_seconds=600)
        if not credentials.has_scopes(scopes):
            raise PublishError("Required Google permissions were not granted", False)
        _save(credentials, token_path)
        return credentials
    except PublishError:
        raise
    except TransportError:
        raise PublishError("Google OAuth connection failed", True) from None
    except RefreshError as exc:
        raise PublishError(f"Google OAuth refresh failed; if access was revoked run {command}", bool(getattr(exc, "retryable", False))) from None
    except (OSError, ValueError, json.JSONDecodeError):
        raise PublishError("Cannot load or save Google OAuth files; check secrets/ and docs/SETUP.md", False) from None
    except Exception as exc:
        # Exception text may contain OAuth secrets; expose only its type.
        raise PublishError(f"Google OAuth authorization failed ({type(exc).__name__}); run {command}", False) from None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--authorize", nargs="?", const="all", choices=[*SERVICES, "all"],
                      help="youtube, classroom or all (default): one browser sign-in per service")
    args = parser.parse_args()
    if args.smoke:
        from reel.publish._smoke import oauth_smoke
        oauth_smoke()
    else:
        for name in (list(SERVICES) if args.authorize == "all" else [args.authorize]):
            print(ACCOUNT_HINT[name], flush=True)
            _get(SERVICES[name], interactive=True)
            print(f"OK: {name} credentials ready (secrets/token_{name}.json)", flush=True)
