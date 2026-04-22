# ccg_project_orchestration

Reusable CCG AI orchestration layer. Add this repo as a git submodule
to any project that uses [taskforge](https://github.com/DevinWilsonSC/agile_tracker)
for AI-native task coordination.

---

## What this is

This repo contains the periodic orchestrator, coordinator scaffolding, and
supporting tooling that drive the CCG multi-Claude workflow. It is
**taskforge-aware** (calls the taskforge REST/MCP API) but is otherwise
independent of any specific project codebase.

Contents:

| Path | Description |
|------|-------------|
| `commands/orch-start.md` | Claude Code slash command: start the periodic orchestrator loop |
| `commands/orch-stop.md` | Claude Code slash command: stop the loop |
| `commands/setup-orchestration.md` | Claude Code slash command: idempotent bootstrap |
| `commands/sync-persona.md` | Claude Code slash command: pull/propose/check agent personas |
| `docs/periodic-workflow.md` | Canonical orchestrator runtime spec |
| `docs/tmux-delegation.md` | Three-tier tmux delegation architecture |
| `docs/workflows/README.md` | Workflow library reference and chaining spec |
| `docs/attrs-conventions.md` | `task.attrs` key conventions consumed by orchestration |
| `scripts/build-coord-prompt.py` | Coordinator prompt assembler |
| `scripts/orchestration_setup.sh` | Idempotent bootstrap script (entry point) |
| `scripts/sync_persona.py` | Agent persona sync CLI |
| `scripts/session-usage-watcher.py` | Chrome CDP session-usage monitor |
| `scripts/session-usage-check.sh` | Reads session-usage-watcher output |
| `scripts/telegram-mcp-health.sh` | Telegram MCP plugin health check |
| `scripts/launch-chrome-debug.sh` | Launch Chrome in debug mode for CDP |
| `setup.sh` | Thin wrapper: `./scripts/orchestration_setup.sh "$@"` |

---

## Prerequisites

Before using this submodule, ensure:

- A running [taskforge](https://github.com/DevinWilsonSC/agile_tracker) instance
  is reachable from your orchestrator host.
- The following env vars are set:

| Env var | Purpose | Example |
|---------|---------|---------|
| `TASKFORGE_API_KEY` | API key for the `claude_orch` actor | `tfk_...` |
| `TASKFORGE_BASE_URL` | Taskforge instance URL | `http://taskforge-prod:8000` |
| `CLAUDE_ORCH_ACTOR_ID` | UUID of the `claude_orch` actor | `70b1afc3-...` |
| `ORCHESTRATION_DIR` | Path to this submodule from project root | `orchestration` (default) |

**Network note:** Ensure `$TASKFORGE_BASE_URL` is reachable from your orchestrator
host. For CCG deployments this is via Tailscale MagicDNS. For other setups, point
to your taskforge instance's hostname or IP.

---

## How to add as a submodule

```bash
# In the root of your project repo:
git submodule add git@github.com:DevinWilsonSC/ccg_project_orchestration.git orchestration
git submodule update --init --recursive

# Run the idempotent bootstrap:
./orchestration/setup.sh --project-root .

# Commit the bootstrap artifacts:
git add .orchestration/ .claude/commands/ CLAUDE.md .gitmodules
git commit -m "feat: add ccg_project_orchestration orchestration submodule"
```

---

## How to run /setup-orchestration

After adding the submodule, use the Claude Code slash command:

```
/setup-orchestration
```

This runs `./orchestration/setup.sh` with the current project root, creating:

- `.orchestration/agents/` — materialized and overlay agent personas
- `.orchestration/.gitignore` — excludes `*.md`, preserves `*.overlay.md`
- `.claude/commands/sync-persona.md` — slash command for persona management
- Appends an orchestration block to `CLAUDE.md`

**Optional flags (pass via `$ARGUMENTS`):**

| Flag | Purpose |
|------|---------|
| `--agents slug1,slug2` | Pull named agent personas immediately |
| `--taskforge-url <url>` | Override `TASKFORGE_BASE_URL` for this run |
| `--actor-id <uuid>` | Override `CLAUDE_ORCH_ACTOR_ID` for this run |

---

## Extraction history

These files were extracted from
[agile_tracker](https://github.com/DevinWilsonSC/agile_tracker) at commit
tagged `orchestration-extract-2026-04-22`. Full history of each file is
preserved in the taskforge repo (`git log -- scripts/build-coord-prompt.py`
etc. still works there).

---

## License

MIT — see `LICENSE` in the agile_tracker repo.
