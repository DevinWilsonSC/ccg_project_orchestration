# ccg_project_orchestration

Reusable CCG AI orchestration layer. Add this repo as a git submodule
to any project that uses [taskforge](https://github.com/DevinWilsonSC/agile_tracker)
for AI-native task coordination.

---

## Quickstart

```bash
# 1. Add as a submodule (from your project root):
git submodule add git@github.com:DevinWilsonSC/ccg_project_orchestration.git orchestration
git submodule update --init --recursive

# 2. Bootstrap (creates .orchestration/, symlinks .claude/commands/, stamps CLAUDE.md):
./orchestration/setup.sh --project-root .

# 3. Set required env vars (e.g. in ~/.bashrc or .env):
export TASKFORGE_API_KEY=tfk_...
export TASKFORGE_BASE_URL=http://taskforge-prod:8000
export CLAUDE_ORCH_ACTOR_ID=<uuid-of-claude_orch-actor>

# 4. Commit the bootstrap artifacts:
git add .orchestration/ .claude/commands/ CLAUDE.md .gitmodules
git commit -m "feat: add ccg_project_orchestration orchestration submodule"

# 5. Start the orchestrator loop in a Claude Code session:
/orch-start
```

---

## Architecture

The orchestration layer drives a **three-tier tmux hierarchy**. The
orchestrator session runs at the top; each claimed task gets its own
coordinator child; each coordinator fans out to specialist children for
parallel build/review phases.

```
Tier 1: ORCHESTRATOR  (Claude Code session, /orch-start)
  │  Polls taskforge every 20 min for ready tasks assigned to claude_orch.
  │  Ships finished coordinators as PRs; routes decisions via Telegram.
  │
  ├── tmux new-window → Tier 2: COORDINATOR  (claude -p, one per task)
  │     │  Runs the selected workflow (six-phase-build, doc-only, etc.)
  │     │  Writes task.attrs.completion; releases lease on finish.
  │     │
  │     ├── tmux split-pane → Tier 3: SPECIALIST  (claude -p)
  │     │     e.g. python-expert: .py files, tests, alembic
  │     │
  │     └── tmux split-pane → Tier 3: SPECIALIST  (claude -p)
  │           e.g. frontend-ux / frontend-ui: templates, JS, CSS
  │
  └── tmux new-window → Tier 2: COORDINATOR  (next task, up to 10 in-flight)
```

**Workflows** are stored in the taskforge DB (`WorkflowVersion.body_template`),
materialized locally at `.orchestration/workflows/<slug>.md` on pull.
See `docs/workflows/README.md` for the available slugs and selection heuristics.

**Agent personas** are stored in the taskforge DB, materialized locally at
`.orchestration/agents/<slug>.md` on pull. Local overlays at
`.orchestration/agents/<slug>.overlay.md` are merged on pull and committed.

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

**Submodule-first invocation rule:** `setup.sh` (and `/setup-orchestration`) must
be invoked from inside the target project, with `ccg_project_orchestration`
already added as a submodule at `orchestration/`. Cross-project invocation —
running `setup.sh` from one project's checkout (or worktree) with
`--project-root` pointing at a different project — is unsupported: the symlink
and `CLAUDE.md` paths it stamps will reference the source project's path on
disk (often a transient worktree) and break as soon as that source is moved or
cleaned up. Always add the submodule to the target project first, then run
`./orchestration/setup.sh --project-root .` from that project's root.

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
- `.orchestration/workflows/` — materialized workflow cache (pull separately)
- `.orchestration/.gitignore` — excludes `*.md`, preserves `*.overlay.md`
- `.claude/commands/sync-persona.md` — slash command for persona management
- `.claude/commands/sync-workflow.md` — slash command for workflow management
- Appends an orchestration block to `CLAUDE.md`

**Optional flags (pass via `$ARGUMENTS`):**

| Flag | Purpose |
|------|---------|
| `--agents slug1,slug2` | Pull named agent personas immediately |
| `--taskforge-url <url>` | Override `TASKFORGE_BASE_URL` for this run |
| `--actor-id <uuid>` | Override `CLAUDE_ORCH_ACTOR_ID` for this run |

---

## Customization

### Agent persona overlays

Each agent's canonical persona lives in the taskforge DB. The local
`.orchestration/agents/<slug>.md` is a materialized cache — regenerate it
with `/sync-persona pull --slug <slug>`. It is gitignored.

To add project-specific guidance (e.g. "always use our internal lint config"):
create `.orchestration/agents/<slug>.overlay.md`. Overlays are committed and
merged into the materialized copy on every pull.

```bash
# Refresh a persona from DB
/sync-persona pull --slug python-expert

# Propose a local overlay back to taskforge for team review
/sync-persona propose --slug python-expert

# Check local vs DB drift
/sync-persona status --slug python-expert
```

### Workflow overlays

Workflow bodies live in the taskforge DB. Materialize them locally with
`/sync-workflow pull --slug <slug>`. To add project-specific phase guidance,
create `.orchestration/workflows/<slug>.overlay.md` — it is committed and
merged on pull.

### Concurrency and tick cadence

Set `ORCH_MAX_IN_FLIGHT` (default: 10) and `ORCH_TICK_MINUTES` (default: 20)
in your environment to tune the orchestrator's parallelism and polling rate.

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
