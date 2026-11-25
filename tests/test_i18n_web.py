"""Language selection over the real HTTP boundary with temporary configuration only."""
import json
from urllib.request import Request

import yaml

from reel.i18n import catalog
from test_web_upload import web


def configure(web, language):
    path = web.root / "config.yaml"
    config = yaml.safe_load(path.read_text())
    config.pop("ui_language", None)
    if language is not None:
        config["ui_language"] = language
    path.write_text(yaml.safe_dump(config), encoding="utf-8")


def messages(client, cookie=None):
    headers = {"Cookie": "reel-language=" + cookie} if cookie else {}
    with client.opener.open(Request(client.base + "/i18n-data.js", headers=headers), timeout=10) as response:
        assert response.headers["Cache-Control"] == "no-store"
        body = response.read().decode()
    assert body.startswith("window.REEL_I18N = ") and body.endswith(";\n")
    return json.loads(body[len("window.REEL_I18N = "):-2])


def test_english_default_armenian_config_and_browser_override(web):
    client = web.client()
    configure(web, None)
    data = messages(client)
    assert data["language"] == "en"
    assert data["messages"] == dict(catalog("en"))
    assert client.call("GET", "/api/privacy/policy")[1]["notice"] == catalog("en")["privacy.lessons_are_recorded_and_faces_are_blurred"]
    configure(web, "hy")
    data = messages(client)
    assert data["language"] == "hy" and data["messages"] == dict(catalog("hy"))
    assert messages(client, "en")["language"] == "en"
    assert messages(client, "../en")["language"] == "hy"
    assert messages(client, "unsupported")["language"] == "hy"
    assert messages(client)["language"] == "hy"
