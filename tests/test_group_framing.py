"""G-24: the group's 'framing' setting is validated and survives a Settings save (not on the form yet)."""
import json

import pytest

from reel import config as config_module
from test_admin_api import group_body, group_url, saved, web  # noqa: F401  (web is a fixture)


def _set_framing(web, value):
    cfg = json.loads(web.config_path.read_text(encoding="utf-8"))
    cfg["groups"]["Group-A"]["framing"] = value
    web.config_path.write_text(json.dumps(cfg), encoding="utf-8")


def test_settings_save_keeps_the_framing_setting(web):
    _set_framing(web, "board")
    code, response = web.client("admin").call("PUT", group_url("Group-A"), group_body(slack_channel="Cnew"))
    assert code == 200, response
    g = saved(web)["groups"]["Group-A"]
    assert g["framing"] == "board" and g["slack_channel"] == "Cnew"


def test_framing_value_is_checked(web):
    _set_framing(web, "zoom")
    with pytest.raises(ValueError, match="framing must be"):
        config_module.load_config(web.config_path)
