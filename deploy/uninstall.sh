#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
DEST="$HOME/Library/LaunchAgents/com.reel.daemon.plist"
if launchctl print "gui/$UID/com.reel.daemon" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID/com.reel.daemon"
fi
rm -f "$DEST"
echo "Reel is disabled. Project data is retained at $ROOT"
