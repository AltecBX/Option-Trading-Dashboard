#!/usr/bin/env bash
# Start Jerry's Options Dashboard inside a Codespace.
#
# Binds to 0.0.0.0 so Codespaces port forwarding can reach it, on the
# port Codespaces expects (8765, matching forwardPorts in
# devcontainer.json). Idempotent: if the server is already listening on
# 8765 it does nothing, so re-attaching to the Codespace won't spawn a
# second copy.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8765}"

# Already running? Leave it alone.
if curl -s -o /dev/null "http://127.0.0.1:${PORT}/" 2>/dev/null; then
  echo "Dashboard already running on port ${PORT}."
  exit 0
fi

echo "Starting Options Dashboard on port ${PORT}..."
nohup python options_dashboard.py --serve --host 0.0.0.0 --port "${PORT}" \
  > dashboard.log 2>&1 &

echo "Started (PID $!). Logs: dashboard.log"
echo "Open the forwarded port ${PORT} in the Ports tab to view the app."
