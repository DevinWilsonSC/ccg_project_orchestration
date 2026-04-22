---
description: Sync CCG agent personas from taskforge
argument-hint: pull --slug <slug> | propose --slug <slug> | status --slug <slug>
---

Run the sync-persona CLI. Agent personas live in `.orchestration/agents/`.

```bash
ORCH_DIR="${ORCHESTRATION_DIR:-orchestration}"
python3 "${ORCH_DIR}/scripts/sync_persona.py" $ARGUMENTS --repo-root "$(git rev-parse --show-toplevel)"
```

Requires env vars `TASKFORGE_API_KEY` and `TASKFORGE_BASE_URL`.
If either is missing, surface the error to the user before running.

Subcommands:
- `pull --slug <slug>`    — fetch latest persona from taskforge and write `.orchestration/agents/<slug>.md`
- `propose --slug <slug>` — submit local `.orchestration/agents/<slug>.overlay.md` as a persona proposal
- `status --slug <slug>`  — check whether the materialized persona has drifted from the taskforge source
