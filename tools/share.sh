#!/usr/bin/env bash
# Start the Reel dashboard (if not running) and a public HTTPS link (Cloudflare quick tunnel,
# free, no account). The link changes every time this script runs.
#   bash tools/share.sh        start (or restart the tunnel) and print the link
#   bash tools/share.sh stop   stop the dashboard and the tunnel
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8765}"
# Local readiness endpoint (http://127.0.0.1:$TUNNEL_METRICS_PORT/ready) used by tools/tunnel_watch.py.
TUNNEL_METRICS_PORT="${TUNNEL_METRICS_PORT:-20241}"
mkdir -p logs
if [ "${1:-}" = "stop" ]; then
  # Agents installed with `deploy/install.sh --share` would bring these straight back; unload them
  # for this session (they load again at the next login, or with deploy/install.sh).
  for label in com.reel.tunnel com.reel.web; do
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  done
  pkill -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT" 2>/dev/null || true
  pkill -f "uvicorn reel.web.app:app --host 127.0.0.1 --port $PORT" 2>/dev/null || true
  # wait for the graceful shutdown: a quick "stop; start" must not see the old server as alive
  for _ in $(seq 1 30); do pgrep -f "uvicorn reel.web.app:app --host 127.0.0.1 --port $PORT" >/dev/null || break; sleep 0.5; done
  rm -f logs/tunnel_url.txt
  echo "Reel dashboard stopped"
  exit 0
fi
if ! curl -fsS -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  nohup .venv/bin/python -m uvicorn reel.web.app:app --host 127.0.0.1 --port "$PORT" >> logs/web.log 2>&1 &
  for _ in $(seq 1 30); do curl -fsS -m 1 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
fi
pkill -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT" 2>/dev/null || true
: > logs/tunnel.log
nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" \
  --metrics "127.0.0.1:$TUNNEL_METRICS_PORT" > logs/tunnel.log 2>&1 &
url=""
for _ in $(seq 1 60); do
  url=$(grep -aEo 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1 || true)
  [ -n "$url" ] && break
  sleep 1
done
[ -n "$url" ] || { echo "Tunnel failed — see logs/tunnel.log"; exit 1; }
printf '%s\n' "$url" > logs/tunnel_url.txt.tmp
mv logs/tunnel_url.txt.tmp logs/tunnel_url.txt
echo "Reel dashboard: $url"
echo "Passwords: secrets/web.json (viewer = read-only, admin = can retry)"
