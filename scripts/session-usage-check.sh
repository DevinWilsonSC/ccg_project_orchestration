#!/usr/bin/env bash
# Report claude.ai session quota usage for the periodic orchestrator.
#
# Output contract (stdout, three lines, always printed):
#   USAGE_PERCENT=<0-100>       integer
#   RESET_EPOCH=<unix ts>       0 if unknown
#   SOURCE=<browser|manual|unknown>
# Exit code: 0 on success, always. The orchestrator treats SOURCE=unknown
# as fail-open (proceed normally) so a missing watcher never blocks work.
#
# The data comes from /tmp/orch-session-usage.json written by a browser
# watcher (Chrome/Chromium extension or MCP-driven poll) that reads
# claude.ai's in-UI usage indicator. Format:
#   {"usage_percent": 47, "reset_epoch": 1745150400, "updated_epoch": 1745132600}
#
# If the file is absent or its updated_epoch is older than STALE_AFTER_SEC
# seconds, we report SOURCE=unknown so the gate fails open.
#
# Manual override: export ORCH_MANUAL_USAGE_PERCENT=<N> to bypass the
# file (emits SOURCE=manual). Useful for testing the pause path.

set -u

STATE_FILE="${ORCH_USAGE_FILE:-/tmp/orch-session-usage.json}"
STALE_AFTER_SEC="${ORCH_USAGE_STALE_AFTER_SEC:-600}"

if [[ -n "${ORCH_MANUAL_USAGE_PERCENT:-}" ]]; then
  echo "USAGE_PERCENT=${ORCH_MANUAL_USAGE_PERCENT}"
  echo "RESET_EPOCH=${ORCH_MANUAL_RESET_EPOCH:-0}"
  echo "SOURCE=manual"
  exit 0
fi

if [[ ! -f "$STATE_FILE" ]]; then
  echo "USAGE_PERCENT=0"
  echo "RESET_EPOCH=0"
  echo "SOURCE=unknown"
  exit 0
fi

python3 - "$STATE_FILE" "$STALE_AFTER_SEC" <<'PY'
import json, sys, time, os
path, stale = sys.argv[1], int(sys.argv[2])
now = int(time.time())
try:
    with open(path) as f:
        d = json.load(f)
    updated = int(d.get("updated_epoch", 0))
    if updated <= 0 or (now - updated) > stale:
        print("USAGE_PERCENT=0")
        print("RESET_EPOCH=0")
        print("SOURCE=unknown")
        sys.exit(0)
    pct = int(round(float(d.get("usage_percent", 0))))
    reset = int(d.get("reset_epoch", 0))
    pct = max(0, min(100, pct))
    print(f"USAGE_PERCENT={pct}")
    print(f"RESET_EPOCH={reset}")
    print("SOURCE=browser")
except Exception:
    print("USAGE_PERCENT=0")
    print("RESET_EPOCH=0")
    print("SOURCE=unknown")
PY
