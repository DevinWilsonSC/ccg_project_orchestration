#!/usr/bin/env bash
# Exit 0 if the Telegram MCP plugin is connected, 1 otherwise.
set -euo pipefail
claude mcp list 2>/dev/null | grep -q 'plugin:telegram:telegram.*✓ Connected'
