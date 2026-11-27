#!/bin/bash
# Stop and remove every Reel login agent (worker, dashboard and optional tunnel).
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
AGENTS="$HOME/Library/LaunchAgents"
for label in com.reel.tunnel com.reel.web com.reel.daemon; do
    if launchctl print "gui/$UID/$label" >/dev/null 2>&1; then
        launchctl bootout "gui/$UID/$label"
    fi
    rm -f "$AGENTS/$label.plist"
done
# The tunnel agent keeps cloudflared alive across watcher restarts, so stop it explicitly.
pkill -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765" 2>/dev/null || true
rm -f "$ROOT/logs/tunnel_url.txt"
echo "Reel is disabled. Project data is retained at $ROOT"
