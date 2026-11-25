"""Per-service Google tokens: a brand YouTube channel cannot hold the Classroom scope."""
from unittest.mock import patch

import pytest

from reel import google_auth as auth
from reel.publish.errors import PublishError

YT, CL = auth.SERVICES["youtube"], auth.SERVICES["classroom"]


def test_service_and_token_names():
    assert auth.service_of(YT) == "youtube" and auth.token_name(YT) == "token_youtube.json"
    assert auth.service_of(CL) == "classroom" and auth.token_name(CL) == "token_classroom.json"
    assert auth.service_of(YT + CL) is None and auth.token_name(YT + CL) == "token.json"
    assert auth.service_of(["scope"]) is None


def test_publishers_ask_for_one_service_each():
    from reel.publish import classroom, youtube
    for module, want in ((youtube, "youtube"), (classroom, "classroom")):
        with patch.object(module, "get_credentials") as creds, patch.object(module, "build"):
            module._service()
        assert auth.service_of(creds.call_args.args[0]) == want


def test_reads_own_token_and_names_missing_service(tmp_path):
    root = tmp_path.resolve()
    secrets = root / "secrets"
    secrets.mkdir()
    (secrets / "token_classroom.json").write_text("{}")
    with patch.object(auth, "__file__", str(root / "reel" / "google_auth.py")), \
            patch.object(auth, "Credentials") as credentials, patch.object(auth, "InstalledAppFlow") as flow:
        token = credentials.from_authorized_user_file.return_value
        token.has_scopes.return_value = True
        token.valid = True
        assert auth.get_credentials(CL) is token
        assert credentials.from_authorized_user_file.call_args.args[0] == str(secrets / "token_classroom.json")
        with pytest.raises(PublishError) as missing:      # no YouTube token: no browser, exact command
            auth.get_credentials(YT)
        assert "--authorize youtube" in str(missing.value) and not missing.value.retryable
        flow.from_client_secrets_file.assert_not_called()
