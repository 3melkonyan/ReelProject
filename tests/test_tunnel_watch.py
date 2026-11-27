"""tools/tunnel_watch.py health checks and the launchd agent templates."""

from __future__ import annotations

import json
import plistlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tools import tunnel_watch as watch

ROOT = Path(__file__).resolve().parents[1]


def _serve(status: int, body: bytes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/ready"


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (200, {"status": 200, "readyConnections": 4}, True),
        (200, {"status": 200, "readyConnections": 0}, False),
        (503, {"status": 503, "readyConnections": 0}, False),  # "Tunnel not found" / offline
        (200, b"not json", False),
    ],
)
def test_tunnel_ready_reads_local_cloudflared_status(status, body, expected):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    server, url = _serve(status, raw)
    try:
        assert watch.tunnel_ready(url) is expected
    finally:
        server.shutdown()


def test_tunnel_ready_is_false_without_cloudflared():
    server, url = _serve(200, b"{}")
    server.shutdown()
    server.server_close()
    assert watch.tunnel_ready(url) is False


def test_restart_waits_for_registration_not_public_dns(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))
    monkeypatch.setattr(watch, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "tunnel_url.txt").write_text("https://fresh-name.trycloudflare.com\n")
    answers = iter([False, True])
    monkeypatch.setattr(watch, "tunnel_ready", lambda *_a: next(answers))
    monkeypatch.setattr(watch.time, "sleep", lambda _s: None)
    assert watch.restart() == "https://fresh-name.trycloudflare.com"
    assert calls == [["bash", "tools/share.sh"]]


@pytest.mark.parametrize("label", ["com.reel.daemon", "com.reel.web", "com.reel.tunnel"])
def test_launchd_templates_are_valid_and_relocatable(label):
    with open(ROOT / "deploy" / f"{label}.plist", "rb") as stream:
        config = plistlib.load(stream)
    assert config["Label"] == label
    assert config["WorkingDirectory"] == "__ROOT__"
    assert config["ProgramArguments"][0].startswith(("__ROOT__/", "/usr/bin/"))
    assert config["StandardOutPath"].startswith("__ROOT__/logs/")
    assert config["KeepAlive"] is True and config["RunAtLoad"] is True
