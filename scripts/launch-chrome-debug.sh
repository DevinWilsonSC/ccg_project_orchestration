#!/usr/bin/env bash
# Launch WSL google-chrome with remote-debugging enabled against the
# `ccg` profile (Profile 2 in ~/.config/google-chrome). Used by the
# periodic orchestrator's session-usage watcher: the browser stays
# logged into claude.ai, and a CDP client (either chrome-devtools-mcp
# from Claude Code or scripts/session-usage-watcher.py) reads the
# usage indicator through port 9222.
#
# Usage:
#   ./scripts/launch-chrome-debug.sh            # start, open claude.ai
#   ./scripts/launch-chrome-debug.sh --detach   # start, return control immediately
#
# If another google-chrome is already running the remote-debug flag is
# ignored by subsequent invocations. Close other Chrome windows (WSL-side)
# before running this.

set -euo pipefail

PORT="${CHROME_DEBUG_PORT:-9222}"
# Non-default user-data-dir is REQUIRED by recent Chrome for
# --remote-debugging-port to be honored. This is a fresh Chrome profile
# (separate from the ccg profile in ~/.config/google-chrome/Profile 2).
# You'll need to sign in to claude.ai once here; it will persist.
DEBUG_UDD="${CHROME_DEBUG_USER_DATA_DIR:-${HOME}/.config/chrome-debug-ccg}"

if curl -s -m 1 "http://localhost:${PORT}/json/version" >/dev/null 2>&1; then
  echo "Chrome already exposing debug on port ${PORT}. Reusing."
  exit 0
fi

# Block if a Chrome is already using DEBUG_UDD (that'd collide regardless
# of other running Chromes, which are fine if they use a different UDD).
if pgrep -af "/opt/google/chrome/chrome " 2>/dev/null \
     | grep -v -- "--type=" \
     | grep -v "_crashpad_handler" \
     | grep -qF -- "--user-data-dir=${DEBUG_UDD}"; then
  echo "ERROR: a Chrome is already using ${DEBUG_UDD}. Close it first:"
  echo "  pkill -f 'google-chrome.*--user-data-dir=${DEBUG_UDD}'"
  exit 1
fi

DETACH=0
for arg in "$@"; do
  [[ "$arg" == "--detach" ]] && DETACH=1
done

mkdir -p "${DEBUG_UDD}"

CMD=(
  google-chrome
  --remote-debugging-port="${PORT}"
  --user-data-dir="${DEBUG_UDD}"
  --no-first-run
  --no-default-browser-check
  "https://claude.ai"
)

if [[ $DETACH -eq 1 ]]; then
  nohup "${CMD[@]}" >/tmp/chrome-debug.log 2>&1 &
  echo "Launched in background (pid $!); log: /tmp/chrome-debug.log"
else
  exec "${CMD[@]}"
fi
