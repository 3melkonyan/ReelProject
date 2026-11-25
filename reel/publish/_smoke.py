"""Offline adapter checks. No credentials, browser, or live publications."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import json
from googleapiclient.errors import HttpError
from httplib2 import Response
from reel.publish.errors import PublishError


def youtube_smoke() -> None:
    from reel.publish import youtube as y
    service = MagicMock()
    service.channels().list().execute.return_value = {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "uploads"}}}]}
    service.playlistItems().list().execute.side_effect = [
        {"items": [{"contentDetails": {"videoId": "old"}}], "nextPageToken": "page2"},
        {"items": [{"contentDetails": {"videoId": "match"}}]},
    ]
    service.videos().list().execute.side_effect = [
        {"items": [{"id": "old", "snippet": {"tags": ["other"]}}]},
        {"items": [{"id": "match", "snippet": {"tags": ["reel-1"]}}]},
    ]
    with patch.object(y, "_service", return_value=service):
        assert y.find_by_tag("reel-1") == "match"
        service.search.assert_not_called()
        assert service.playlistItems().list.call_args.kwargs["pageToken"] == "page2"
        service.playlistItems().list().execute.side_effect = None
        service.playlistItems().list().execute.return_value = {"items": []}
        assert y.find_by_tag("absent") is None
        assert not y.is_in_playlist("v", "p")
        service.playlistItems().list().execute.return_value = {"items": [{"id": "item"}]}
        assert y.is_in_playlist("v", "p")
        service.playlistItems().insert().execute.return_value = {"id": "item"}
        assert y.add_to_playlist("v", "p") == "item"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.mp4"
            path.write_bytes(b"offline fixture")
            service.videos().insert().next_chunk.side_effect = [(None, None), (None, {"id": "v"})]
            assert y.upload(str(path), "Title", "Description", ["reel-1"])["video_id"] == "v"
            call = service.videos().insert.call_args.kwargs
            assert call["body"]["status"]["privacyStatus"] == "unlisted"
            assert call["media_body"].resumable()
            assert call["media_body"].stream().closed
            y.set_thumbnail("v", str(path))
        for status, reason, retryable in [(403, "quotaExceeded", True), (403, "forbidden", False), (429, "rateLimitExceeded", True), (503, "backendError", True)]:
            service.playlistItems().list().execute.side_effect = HttpError(Response({"status": str(status)}), json.dumps({"error": {"errors": [{"reason": reason}]}}).encode())
            try:
                y.is_in_playlist("v", "p")
                raise AssertionError("Expected PublishError")
            except PublishError as error:
                assert error.retryable is retryable
    print("OK: YouTube offline pagination, exact tag, resumable upload, playlist, thumbnail, error classification")


def classroom_smoke() -> None:
    from reel.publish import classroom as c
    service = MagicMock()
    request = service.courses().announcements().create
    request().execute.return_value = {"id": "announcement"}
    with patch.object(c, "_service", return_value=service):
        assert c.announce("123", "Title", "https://example.com/video") == "announcement"
        body = request.call_args.kwargs["body"]
        assert body["state"] == "PUBLISHED"
        assert body["materials"][0]["link"]["url"] == "https://example.com/video"
        assert body["text"] == "Title\nhttps://example.com/video"
        request().execute.side_effect = HttpError(Response({"status": "403"}), b'{"error":{"errors":[{"reason":"forbidden"}]}}')
        try:
            c.announce("123", "Title", "https://example.com/video")
            raise AssertionError("Expected PublishError")
        except PublishError as error:
            assert not error.retryable
    print("OK: Classroom offline announcement body, returned ID, permission error")


def library_smoke() -> None:
    from reel.publish import library as lib
    entry = dict(job_id="job-1", group="Խումբ", title="Դաս\nմեկ", url="https://example.com/video", published_at="2026-11-17T00:00:00Z")
    # Resolve the real project path while cwd is mocked to an unrelated directory.
    with patch.object(Path, "cwd", return_value=Path("/tmp")):
        assert lib._path() == Path(lib.__file__).resolve().parents[2] / "library.jsonl"
    with TemporaryDirectory() as directory:
        target = Path(directory) / "library.jsonl"
        with patch.object(lib, "_path", return_value=target):
            lib.add(entry)
            lib.add({**entry, "job_id": "job-2"})
            rows = [json.loads(line) for line in target.read_text().splitlines()]
            assert rows == [entry, {**entry, "job_id": "job-2"}]
            for invalid in ({}, {**entry, "url": "invalid"}, {**entry, "extra": float("nan")}):
                try:
                    lib.add(invalid)
                    raise AssertionError("Expected PublishError")
                except PublishError as exc:
                    assert not exc.retryable
            assert len(target.read_text().splitlines()) == 2
        with patch.object(lib, "_path", return_value=target / "missing"):
            try:
                lib.add(entry)
                raise AssertionError("Expected PublishError")
            except PublishError as exc:
                assert not exc.retryable
    print("OK: Library offline project root, JSONL append, validation, local I/O error")


def oauth_smoke() -> None:
    from reel import google_auth as auth
    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        secrets = root / "secrets"
        secrets.mkdir()
        with patch.object(auth, "__file__", str(root / "reel" / "google_auth.py")), patch.object(auth, "InstalledAppFlow") as flow, patch.object(auth, "Credentials") as credentials, patch.object(auth, "Request"):
            try:
                auth.get_credentials(["scope"])
                raise AssertionError("Expected noninteractive authorization error")
            except PublishError as exc:
                assert not exc.retryable and "--authorize" in str(exc)
            flow.from_client_secrets_file.assert_not_called()
            (secrets / "token.json").write_text("{}")
            token = credentials.from_authorized_user_file.return_value
            token.has_scopes.return_value = True
            token.valid = True
            assert auth.get_credentials(["scope"]) is token
            assert credentials.from_authorized_user_file.call_args.args[0] == str(secrets / "token.json")
            token.valid = False
            token.refresh_token = "offline-fixture"
            token.to_json.return_value = "{}"
            auth.get_credentials(["scope"])
            token.refresh.assert_called_once()
            assert (secrets / "token.json").stat().st_mode & 0o777 == 0o600
            token.has_scopes.return_value = False
            try:
                auth.get_credentials(["scope"])
                raise AssertionError("Expected scope error")
            except PublishError as exc:
                assert not exc.retryable
            flow.from_client_secrets_file.assert_not_called()
            (secrets / "client_secret.json").write_text("{}")
            interactive = flow.from_client_secrets_file.return_value.run_local_server.return_value
            interactive.has_scopes.return_value = True
            interactive.to_json.return_value = "{}"
            assert auth._get(["scope"], interactive=True) is interactive
            flow.from_client_secrets_file.return_value.run_local_server.assert_called_once()
    print("OK: OAuth offline project root, no automatic browser, refresh, scopes, explicit authorization")
