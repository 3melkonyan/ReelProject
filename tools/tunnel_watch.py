"""Keep the public quick tunnel alive across sleep/wake and open the working link on this Mac.

A trycloudflare quick tunnel dies after the Mac sleeps or the network drops ("Tunnel not
found"), and every restart gets a new URL. This watcher asks the local cloudflared readiness
endpoint whether the tunnel has a live edge connection, restarts the tunnel with tools/share.sh
when it does not, and opens the current link in the default browser (without email
notifications). Only the tunnel/dashboard are touched, never the worker.

Health is checked locally rather than through the public hostname: a lookup made before a new
quick-tunnel name is published can be cached as "not found" by macOS or a phone hotspot for
many minutes, which would otherwise look like a dead tunnel and cause a restart loop.

  nohup .venv/bin/python tools/tunnel_watch.py >> logs/tunnel_watch.log 2>&1 &

or keep it running across logins with `bash deploy/install.sh --share`.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS_PORT = os.environ.get("TUNNEL_METRICS_PORT", "20241")  # same default as tools/share.sh
READY_URL = f"http://127.0.0.1:{METRICS_PORT}/ready"
CHECK_SEC = 30  # normal check interval
WAKE_GAP_SEC = 90  # a longer gap between checks means the Mac was asleep
GRACE_SEC = 20  # let cloudflared reconnect by itself after a wake-up
FAILS_BEFORE_RESTART = 3
READY_WAIT_SEC = 60  # how long a fresh tunnel may take to register
SHOW_DELAY_SEC = 10  # give the new public name time to publish before the browser looks it up

log = logging.getLogger("tunnel_watch")


def _get(url: str, timeout: float = 10) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


def current_url() -> str | None:
    try:
        url = (ROOT / "logs" / "tunnel_url.txt").read_text().strip()
    except OSError:
        return None
    return url if url.startswith("https://") else None


def tunnel_ready(url: str = READY_URL) -> bool:
    """True when the local cloudflared reports at least one registered edge connection."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = json.loads(response.read() or b"{}")
            return response.status == 200 and int(body.get("readyConnections") or 0) > 0
    except Exception:  # noqa: BLE001 - 503, refused, bad JSON: all mean "not ready"
        return False


def wait_dashboard(attempts: int = 30, pause: float = 2) -> bool:
    """At login the dashboard agent may still be starting; share.sh would otherwise launch a second
    server that races it for the port."""
    port = os.environ.get("PORT", "8765")
    for _ in range(attempts):
        if _get(f"http://127.0.0.1:{port}/healthz", timeout=2):
            return True
        time.sleep(pause)
    return False


def online() -> bool:
    return _get("https://www.cloudflare.com/cdn-cgi/trace", timeout=8)


def wait_online(attempts: int = 30, pause: float = 10) -> bool:
    for _ in range(attempts):  # up to ~5 min by default
        if online():
            return True
        time.sleep(pause)
    return False


def restart() -> str | None:
    try:
        subprocess.run(
            ["bash", "tools/share.sh"],
            cwd=ROOT,
            timeout=180,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("share.sh failed: %s", type(exc).__name__)
        return None
    deadline = time.monotonic() + READY_WAIT_SEC
    while time.monotonic() < deadline:
        if tunnel_ready():
            return current_url()
        time.sleep(3)
    log.warning("tunnel started but did not register within %d s", READY_WAIT_SEC)
    return None


def show(url: str) -> None:
    time.sleep(SHOW_DELAY_SEC)
    subprocess.run(["open", url], check=False)
    log.info("opened %s", url)


def bring_up(reason: str) -> None:
    log.info("tunnel not ready (%s): restarting", reason)
    new = restart()
    if new:
        log.info("tunnel ready: %s", new)
        show(new)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("watching %s (readiness: %s)", current_url(), READY_URL)
    if not tunnel_ready():
        wait_dashboard()
        if wait_online():
            bring_up("startup")
    last_wall = time.time()
    fails = 0
    while True:
        time.sleep(CHECK_SEC)
        now_wall = time.time()
        gap = now_wall - last_wall
        woke = gap > WAKE_GAP_SEC  # the wall clock keeps running while asleep
        last_wall = now_wall
        if woke:
            log.info("wake-up detected (gap %.0f s)", gap)
            wait_online()
            time.sleep(GRACE_SEC)
        url = current_url()
        if url and tunnel_ready():
            fails = 0
            if woke:
                show(url)
            continue
        fails += 1
        if not (woke or fails >= FAILS_BEFORE_RESTART) or not online():
            continue
        bring_up(f"fails={fails}, woke={woke}")
        fails = 0
        last_wall = time.time()


if __name__ == "__main__":
    main()
