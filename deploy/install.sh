#!/bin/bash
# Install Reel as login agents for this macOS user:
#   com.reel.daemon  the processing worker
#   com.reel.web     the dashboard on http://127.0.0.1:8765
#   com.reel.tunnel  optional public Cloudflare quick tunnel kept alive by tools/tunnel_watch.py
#
#   bash deploy/install.sh           worker + dashboard (and refresh the tunnel agent if present)
#   bash deploy/install.sh --share   also expose the dashboard through the tunnel
#
# Re-run it after moving the project folder: every agent stores absolute paths.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SHARE=0
for arg in "$@"; do
    case "$arg" in
        --share) SHARE=1 ;;
        *) echo "Usage: bash deploy/install.sh [--share]" >&2; exit 2 ;;
    esac
done
for required in .venv/bin/python config.yaml secrets/token_youtube.json; do
    if [[ ! -f "$ROOT/$required" ]]; then
        echo "Error: ${ROOT}/${required} is missing. Complete the setup in docs/SETUP.md first." >&2
        exit 1
    fi
done
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo 'Error: .venv/bin/python is not executable. Recreate the environment.' >&2
    exit 1
fi
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/logs"

LABELS=(com.reel.daemon com.reel.web)
if [[ $SHARE == 1 || -f "$AGENTS/com.reel.tunnel.plist" ]]; then
    if ! command -v cloudflared >/dev/null && [[ ! -x /opt/homebrew/bin/cloudflared ]]; then
        echo "Error: cloudflared is not installed (brew install cloudflared)." >&2
        exit 1
    fi
    LABELS+=(com.reel.tunnel)
fi

loaded() { launchctl print "gui/$UID/$1" >/dev/null 2>&1; }

for label in "${LABELS[@]}"; do
    dest="$AGENTS/$label.plist"
    log=$("$ROOT/.venv/bin/python" - "$ROOT" "$ROOT/deploy/$label.plist" "$dest" <<'PY'
import plistlib
import sys
root, template, destination = sys.argv[1:]
with open(template, "rb") as stream:
    config = plistlib.load(stream)
def expand(value):
    if isinstance(value, str):
        return value.replace("__ROOT__", root)
    if isinstance(value, list):
        return [expand(item) for item in value]
    if isinstance(value, dict):
        return {key: expand(item) for key, item in value.items()}
    return value
config = expand(config)
with open(destination, "wb") as stream:
    plistlib.dump(config, stream)
print(config.get("StandardOutPath", ""))
PY
)
    chmod 644 "$dest"
    # Inside a privacy-protected folder (Desktop, Documents, iCloud Drive) launchd may only open
    # log files it created itself (they carry a com.apple.macl grant). A log written first by
    # another process fails with exit 78 (EX_CONFIG), so set it aside for launchd to recreate.
    if [[ -n "$log" && -f "$log" ]] && ! xattr -p com.apple.macl "$log" >/dev/null 2>&1; then
        mv -f "$log" "$log.old"
    fi
    if loaded "$label"; then
        launchctl bootout "gui/$UID/$label" || true
        for _ in $(seq 1 20); do loaded "$label" || break; sleep 0.5; done
    fi
    if [[ $label == com.reel.web ]]; then
        # A dashboard started by hand (tools/share.sh, uvicorn) would hold the port.
        pkill -f "uvicorn reel.web.app:app --host 127.0.0.1 --port 8765" 2>/dev/null || true
        for _ in $(seq 1 30); do
            pgrep -f "uvicorn reel.web.app:app --host 127.0.0.1 --port 8765" >/dev/null || break
            sleep 0.5
        done
    fi
    launchctl enable "gui/$UID/$label"
    launchctl bootstrap "gui/$UID" "$dest"
done

echo "Reel is enabled: ${LABELS[*]}"
echo "Logs: $ROOT/logs/daemon.log, web.log$([[ " ${LABELS[*]} " == *" com.reel.tunnel "* ]] && echo ", tunnel_watch.log")"
