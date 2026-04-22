---
description: Sync CCG workflow definitions from taskforge
argument-hint: pull --slug <slug> | propose --slug <slug> | status --slug <slug>
---

Run the sync-workflow CLI. Workflow definitions live in `.orchestration/workflows/`.

```bash
ORCH_DIR="${ORCHESTRATION_DIR:-orchestration}"
python3 "${ORCH_DIR}/scripts/sync_workflow.py" $ARGUMENTS --repo-root "$(git rev-parse --show-toplevel)"
```

Requires env vars `TASKFORGE_API_KEY` and `TASKFORGE_BASE_URL`.
If either is missing, surface the error to the user before running.

Subcommands:
- `pull --slug <slug>`    — fetch published workflow from taskforge and write `.orchestration/workflows/<slug>.md`
- `propose --slug <slug>` — submit local `.orchestration/workflows/<slug>.overlay.md` as a new draft version
- `status --slug <slug>`  — check whether the materialized workflow has drifted from the taskforge source
