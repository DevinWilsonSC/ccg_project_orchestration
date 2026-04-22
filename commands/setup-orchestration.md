---
description: Bootstrap .orchestration/ and stamp CLAUDE.md for CCG AI orchestration
argument-hint: [--agents slug1,slug2] [--taskforge-url <url>]
---

Run the orchestration setup script. This is safe to re-run (idempotent).

Locate the script (handles taskforge as a submodule at any depth, or running from inside the taskforge repo itself):

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
SCRIPT=$(find "$PROJECT_ROOT" -name "orchestration_setup.sh" 2>/dev/null | head -1)
if [[ -z "$SCRIPT" ]]; then
  echo "ERROR: orchestration_setup.sh not found under $PROJECT_ROOT" >&2
  exit 1
fi
bash "$SCRIPT" --project-root "$PROJECT_ROOT" $ARGUMENTS
```

**What this creates in the project:**

| Path | Description |
|---|---|
| `.orchestration/agents/` | Directory for materialized and overlay personas |
| `.orchestration/.gitignore` | Excludes `*.md`, preserves `*.overlay.md` |
| `.claude/commands/sync-persona.md` | Slash command to pull/propose/check personas |
| `CLAUDE.md` (appended) | Documents orchestration env vars and slash commands |

**Optional flags (pass via $ARGUMENTS):**
- `--agents slug1,slug2` — pull named agent personas immediately (requires `TASKFORGE_API_KEY` + `TASKFORGE_BASE_URL`)
- `--taskforge-url <url>` — override `TASKFORGE_BASE_URL` for this run

**After running:**

1. Check that `TASKFORGE_API_KEY` and `TASKFORGE_BASE_URL` are set in your env.
2. Commit the bootstrap artifacts: `.orchestration/.gitignore`, `.claude/commands/sync-persona.md`, and the appended `CLAUDE.md`.
3. Pull agent personas with `/sync-persona pull --slug <slug>`.
