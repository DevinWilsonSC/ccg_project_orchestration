#!/usr/bin/env bash
# Thin wrapper — delegates to scripts/orchestration_setup.sh.
# Safe to re-run (idempotent).
#
# Usage: ./setup.sh [--project-root <path>] [--agents <slug,...>] [--taskforge-url <url>] [--actor-id <uuid>]
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$(realpath "$0")")" && pwd)
exec bash "$SCRIPT_DIR/scripts/orchestration_setup.sh" "$@"
