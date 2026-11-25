#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
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
DEST="$AGENTS/com.reel.daemon.plist"
mkdir -p "$AGENTS" "$ROOT/logs"
"$ROOT/.venv/bin/python" - "$ROOT" "$DEST" <<'PY'
import pathlib
import plistlib
import sys
root, destination = sys.argv[1:]
with open(pathlib.Path(root) / "deploy/com.reel.daemon.plist", "rb") as stream:
    config = plistlib.load(stream)
def expand(value):
    if isinstance(value, str):
        return value.replace("__ROOT__", root)
    if isinstance(value, list):
        return [expand(item) for item in value]
    if isinstance(value, dict):
        return {key: expand(item) for key, item in value.items()}
    return value
with open(destination, "wb") as stream:
    plistlib.dump(expand(config), stream)
PY
chmod 644 "$DEST"
if launchctl print "gui/$UID/com.reel.daemon" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID" "$DEST"
fi
launchctl bootstrap "gui/$UID" "$DEST"
echo "Reel is enabled. Logs: $ROOT/logs/daemon.log"
