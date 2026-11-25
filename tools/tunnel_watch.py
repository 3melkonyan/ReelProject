"""Keep the public quick tunnel alive across sleep/wake and open the working link on this Mac.

A trycloudflare quick tunnel dies after the Mac sleeps or the network drops ("Tunnel not
found"), and every restart gets a new URL. This watcher notices a wake-up (a gap in the wall
clock) or a dead public URL, restarts the tunnel with tools/share.sh when needed, and opens the
current link in the default browser (owner's choice: no email). Only the tunnel/dashboard are
touched, never the worker.

  nohup .venv/bin/python tools/tunnel_watch.py >> logs/tunnel_watch.log 2>&1 &
"""
from __future__ import annotations

import logging
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_SEC = 30          # normal check interval
WAKE_GAP_SEC = 90       # a longer gap between checks means the Mac was asleep
GRACE_SEC = 20          # let cloudflared reconnect by itself after a wake-up
FAILS_BEFORE_RESTART = 3

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


def public_ok(url: str | None) -> bool:
    return bool(url) and _get(f"{url}/healthz")


def online() -> bool:
    return _get("https://www.cloudflare.com/cdn-cgi/trace", timeout=8)


def restart() -> str | None:
    try:
        subprocess.run(["bash", "tools/share.sh"], cwd=ROOT, timeout=180, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        log.warning("share.sh failed: %s", type(exc).__name__)
        return None
    url = current_url()
    for _ in range(12):
        if public_ok(url):
            return url
        time.sleep(5)
    return url


def show(url: str) -> None:
    subprocess.run(["open", url], check=False)
    log.info("opened %s", url)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("watching %s", current_url())
    last_wall = time.time()
    fails = 0
    while True:
        time.sleep(CHECK_SEC)
        now_wall = time.time()
        gap = now_wall - last_wall
        woke = gap > WAKE_GAP_SEC                      # the wall clock keeps running while asleep
        last_wall = now_wall
        if woke:
            log.info("wake-up detected (gap %.0f s)", gap)
            for _ in range(30):                       # wait up to ~5 min for the network
                if online():
                    break
                time.sleep(10)
            time.sleep(GRACE_SEC)
        url = current_url()
        if url and public_ok(url):
            fails = 0
            if woke:
                show(url)
            continue
        fails += 1
        if not (woke or fails >= FAILS_BEFORE_RESTART) or not online():
            continue
        log.info("public URL down (fails=%d, woke=%s): restarting tunnel", fails, woke)
        new = restart()
        fails = 0
        if new and public_ok(new):
            show(new)
        last_wall = time.time()


if __name__ == "__main__":
    main()
