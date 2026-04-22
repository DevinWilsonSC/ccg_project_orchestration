# Tmux-Based Specialist Delegation

**Status:** v1, 2026-04-19.

**Problem:** The Agent tool does not propagate into sub-agent sessions.
A coordinator spawned via `Agent(run_in_background=true)` cannot itself
call `Agent` to fan out to specialists (python-expert, frontend-ux,
frontend-ui, software-architect). This means coordinators running
multi-phase workflows (e.g., six-phase-build) are forced to do all
phases inline rather than delegating to the specialist team.

**Solution:** Use `tmux` + `claude -p` to spawn full-fidelity Claude
sessions at every tier. Each session is a top-level `claude` process
with the complete tool set — including Agent, Bash, Read, Edit, Grep,
and any MCP servers configured in the environment.

---

## Three-Tier Architecture

```
Tier 1: ORCHESTRATOR (this Claude Code session)
  │  Runs /orch-start, polls taskforge, claims tasks
  │  Uses ScheduleWakeup for tick cadence
  │  Ships PRs via gh CLI
  │
  ├── tmux new-window → Tier 2: COORDINATOR (claude -p, one per task)
  │     │  Runs the selected workflow (six-phase-build, etc.)
  │     │  Has full tool set including Bash
  │     │
  │     ├── tmux split-pane → Tier 3: SPECIALIST (claude -p)
  │     │     python-expert: .py files, tests, alembic
  │     │
  │     ├── tmux split-pane → Tier 3: SPECIALIST (claude -p)
  │     │     frontend-ux: JS, data-* attrs, ARIA, a11y
  │     │
  │     └── tmux split-pane → Tier 3: SPECIALIST (claude -p)
  │           frontend-ui: CSS, Tailwind classes, visual
  │
  ├── tmux new-window → Tier 2: COORDINATOR (task 2)
  │     └── ...
  │
  └── tmux new-window → Tier 2: COORDINATOR (task 3)
        └── ...
```

---

## Launching the Orchestrator

The orchestrator session must run inside tmux. Start it from the host
terminal:

```bash
# Start a named tmux session for the orchestrator
tmux new-session -d -s orch -c /path/to/agile_tracker

# Attach and run Claude Code interactively
tmux attach -t orch
# Then inside: claude
# Then inside claude: /orch-start
```

Or launch non-interactively for headless operation:

```bash
tmux new-session -d -s orch -c /path/to/agile_tracker \
  "claude -p '/orch-start' --dangerously-skip-permissions"
```

The orchestrator uses `ScheduleWakeup` for ticks as before. The only
change is that coordinator launch uses `tmux new-window` + `claude -p`
instead of `Agent(run_in_background=true)`.

---

## Coordinator Launch (replaces Agent tool)

When the orchestrator claims a task and needs a coordinator:

### Step 1 — Write the prompt to a file

The coordinator prompt (Part 1 + Part 2 + Part 3, as specified in
`orch-start.md` §6d) is written to a temp file:

```
/tmp/coord-<short-id>.prompt
```

### Step 2 — Launch via tmux

```bash
tmux new-window -d -n "coord-<short-id>" \
  "cd <worktree-path> && \
   script -qefc 'claude -p \"\$(cat /tmp/coord-<short-id>.prompt)\" --model opus --dangerously-skip-permissions --max-budget-usd 10 --no-session-persistence' /tmp/coord-<short-id>.log; \
   echo \$? > /tmp/coord-<short-id>.exit; \
   touch /tmp/coord-<short-id>.done"
```

The `script(1)` wrapper is non-negotiable: redirecting `claude -p`
stdout to a file (`> FILE 2>&1`) causes full-buffered stdio — nothing
reaches disk or the tmux pane until the session exits. `script -qefc
'<cmd>' <log>` allocates a pty for the child, preserving line-buffered
output that streams to both the log **and** the tmux pane in real
time. Attaching via `tmux attach -t orch` and cycling windows with
`C-b n` shows each coordinator's live fanout. Flags: `-q` suppresses
the banner, `-e` propagates the child's exit code, `-f` flushes on
every write.

### Step 3 — Record the coordinator reference

Set `attrs._coordinator_tmux_window` to `coord-<short-id>` on the
Taskforge task. The orchestrator uses this during REAP to find the
coordinator's output.

---

## Specialist Launch (from within coordinator)

The coordinator is a full Claude session. During BUILD and REVIEW
phases, it fans out to specialists by running tmux commands via Bash:

### Step 1 — Write specialist prompts to files

```bash
cat > /tmp/specialist-py-<short-id>.prompt <<'PROMPT'
You are the python-expert specialist for taskforge task <task-id>.
Working directory: <worktree-path>
Branch: <branch>

<design doc contents>

Build the backend implementation: services, routers, models, schemas,
alembic migrations, and tests. You own all .py files in app/, mcp_server/,
tests/, and alembic/.

<specific instructions from coordinator>
PROMPT
```

### Step 2 — Launch parallel specialists

```bash
# Python expert — same script(1) wrapper as the coordinator so specialist
# output streams to the split pane in real time (not just the log file).
# The -t "coord-<short-id>" flag is mandatory: without it, tmux targets
# whatever pane is currently active (often the orchestrator's pane) and
# specialists land in the wrong window.
tmux split-pane -d -h -t "coord-<short-id>" \
  "cd <worktree-path> && \
   script -qefc 'claude -p \"\$(cat /tmp/specialist-py-<short-id>.prompt)\" --model sonnet --dangerously-skip-permissions --max-budget-usd 5 --no-session-persistence' /tmp/specialist-py-<short-id>.log; \
   touch /tmp/specialist-py-<short-id>.done"

# Frontend UX (if needed)
tmux split-pane -d -h -t "coord-<short-id>" \
  "cd <worktree-path> && \
   script -qefc 'claude -p \"\$(cat /tmp/specialist-feux-<short-id>.prompt)\" --model sonnet --dangerously-skip-permissions --max-budget-usd 5 --no-session-persistence' /tmp/specialist-feux-<short-id>.log; \
   touch /tmp/specialist-feux-<short-id>.done"
```

All three tiers — coordinator window and each specialist pane — use
the same `script -qefc '<cmd>' <log>` wrapper. Without it the panes
are blank until exit (full-buffered pipe); with it they stream live
and the log file is written incrementally.

### Step 3 — Wait for completion

The coordinator runs inside `claude -p` — a **single-turn session**.
There is no `ScheduleWakeup`, no "resume next tick", no way to pause
and come back. Waiting for specialists MUST be a synchronous shell
poll loop run through the Bash tool. The `sleep` blocks inside the
tool call; when the `.done` files appear, the loop returns and the
coordinator's session continues through the remaining phases.

```bash
# Poll for completion (coordinator runs this via Bash)
while [ ! -f /tmp/specialist-py-<short-id>.done ] || \
      [ ! -f /tmp/specialist-feux-<short-id>.done ]; do
  sleep 15
done
```

Do NOT use `tmux wait-for` from inside the coordinator — the
coordinator's own Bash tool call would block for the full specialist
runtime regardless, and `tmux wait-for` adds no value over a plain
`.done` file check. Do NOT emit "sleeping until next check" or
"wakeup scheduled" narration and exit — the coordinator has no such
capability and the session will simply end with unreleased work.

### Step 4 — Read results

The coordinator reads specialist output from the log files and
inspects the worktree for changes.

---

## REAP Mechanism (orchestrator side)

During the REAP phase (step 3 of the tick protocol), the orchestrator
detects coordinator completion by checking for `.done` files:

```bash
# Check if coordinator finished
if [ -f /tmp/coord-<short-id>.done ]; then
  # Read the coordinator's output log
  LAST_LINE=$(tail -1 /tmp/coord-<short-id>.log)
  # Extract RELEASED status
  # Proceed with ship path
fi
```

Classification (replaces the TaskList/TaskGet approach):

- **In-flight:** `_coordinator_tmux_window` is set AND no
  `/tmp/coord-<short-id>.done` file exists.
- **Completed:** `_coordinator_tmux_window` is set AND
  `/tmp/coord-<short-id>.done` exists.
- **Fresh:** `_coordinator_tmux_window` is unset.

The reconciliation logic (final-line marker vs Taskforge status)
remains identical to the current spec in `orch-start.md` §3.

---

## Key CLI Flags

| Flag | Purpose | Tier |
|---|---|---|
| `--model opus` | Complex coordination | Coordinator |
| `--model sonnet` | Focused specialist work | Specialist |
| `--dangerously-skip-permissions` | No interactive approval prompts | Both |
| `--max-budget-usd N` | Cost cap per session | Both |
| `--no-session-persistence` | Don't clutter session storage | Both |
| `--bare` | Skip auto-discovery overhead | Optional |
| `--allowedTools "Bash Edit Read Write Grep Glob"` | Restrict tool set | Optional |
| `--add-dir <path>` | Expose CLAUDE.md from a specific directory | Optional |

---

## Cost Control

Budget caps per session:

| Tier | Default budget | Model |
|---|---|---|
| Orchestrator | Uncapped (interactive) | opus |
| Coordinator | $10 | opus |
| Specialist | $5 | sonnet |
| Specialist (review) | $3 | sonnet |

The `--max-budget-usd` flag on `claude -p` enforces a hard cap. If a
session exceeds its budget, it exits with an error. The coordinator
detects this via the exit code and marks the task as blocked.

---

## Cleanup

After the orchestrator ships a PR (or handles a blocker), it cleans up:

```bash
# Remove coordinator artifacts
rm -f /tmp/coord-<short-id>.{prompt,log,exit,done}
rm -f /tmp/specialist-*-<short-id>.{prompt,log,done}

# Remove worktree (same as current spec)
git worktree remove <worktree-path>
```

---

## Migration from Agent-tool Model

The transition is backward-compatible:

1. **Orchestrator** continues to use `ScheduleWakeup` for ticks. No
   change to the tick cadence or the reap/heartbeat/top-up structure.
2. **Step 6d** of `orch-start.md` changes from `Agent(run_in_background=true)`
   to `tmux new-window` + `claude -p`.
3. **Step 2 classification** uses `.done` files instead of `TaskList`.
4. **Step 3 REAP** reads `.log` files instead of `TaskOutput`.
5. **Workflow files** (`docs/orchestrator/workflows/*.md`) update
   specialist invocation from "invoke via Agent tool" to "invoke via
   tmux + claude -p".
6. **`attrs._coordinator_task_id`** is replaced by
   `attrs._coordinator_tmux_window` for the tmux window name.

Existing tasks with `_coordinator_task_id` set are treated as orphans
(same as current orphan handling in `orch-start.md` §2).
