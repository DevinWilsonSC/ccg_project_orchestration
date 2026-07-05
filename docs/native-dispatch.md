# Native Dispatch Spec — subagents + Workflow tool + background agents

**Status:** v1 (orch-v2-rebaseline), authoritative.
**Supersedes:** `docs/teams-delegation.md` (Teams/`claude -p`) and the retired
`docs/tmux-delegation.md`.
**See also:** `commands/orch-start.md` §6d (runnable form),
`docs/periodic-workflow.md` §5 (design/why), `docs/attrs-conventions.md`
(`_dispatch_ref`, `checkpoint`).

This is the canonical usage spec for how the periodic orchestrator delegates
work in v2. It replaces the tmux-window / `claude -p` / Teams-teammate
coordinator scaffolding with **native Claude Code primitives**: typed
subagents (the `Agent` tool), the `Workflow` tool, and background agents.
Workflow files and `orch-start.md` defer to this doc for delegation behaviour;
if they conflict, fix both in the same commit.

---

## 1. Roles (a two-hop delegation tree, not three tiers of processes)

```
ORCHESTRATOR  (ScheduleWakeup-driven Claude Code session, /orch-start)
  │  Polls Taskforge, claims ready+assigned tasks, dispatches each as a
  │  native delegated unit, heartbeats leases, reaps, ships PRs to dev.
  │
  ├── Agent(subagent_type=<persona>) ─────────── trivial task → one subagent
  │
  └── Agent(subagent_type="coordinator", run_in_background=true)  non-trivial
        │  Runs the selected workflow's phases via the Workflow tool.
        │  Fans out to typed specialist subagents:
        │
        ├── Agent(subagent_type="python-expert")     .py, tests, alembic
        ├── Agent(subagent_type="frontend-ux")       JS, data-*, ARIA, a11y
        ├── Agent(subagent_type="frontend-ui")       CSS, Tailwind, visual
        └── Agent(subagent_type="software-architect") design + review
```

There are **no** tmux windows, **no** panes, **no** `claude -p` children, and
**no** Teams teammates. "Coordinator" and "specialist" are *roles*, not OS
processes — they are native subagents dispatched with the `Agent` tool.

**Depth:** the orchestrator dispatches a coordinator unit; the coordinator fans
out to specialist subagents. Specialists do not fan out further.

**Isolation:** subagents share the worktree filesystem but not context. One
specialist's edits are visible to another only through the file system. All
cross-specialist coordination flows through the coordinator (via `Agent`
results and `SendMessage` follow-ups).

---

## 2. Dispatch mechanisms

Pick the cheapest mechanism that reliably does the job (design §2b, §2d).

| Task shape | Mechanism | Lane |
|---|---|---|
| Trivial (typo / one-liner / copy — `lightweight`) | one **typed subagent** (`Agent`, no phases) | cheap (low effort) |
| Non-trivial | **coordinator unit** running the **Workflow tool**, fanning out to typed specialists | capable |
| Long-running (default for both) | **background agent** (`run_in_background: true`) | as above |

### 2a. Typed subagents (the `Agent` tool)

```
Agent({
  subagent_type: "<persona-slug>",   // python-expert, software-architect, ...
  run_in_background: true,
  prompt: "<self-contained prompt: task context, worktree, branch, AC>"
})
```

`subagent_type` is the persona slug — the same personas as the tmux/Teams era,
now invoked natively. Personas remain **DB-authoritative** (`Actor.persona_md`);
`build-coord-prompt.py` pre-stages `/tmp/specialist-{tag}-<short>.persona` for
every agent named in the workflow's `specialists:` frontmatter, and native
`.claude/agents/*.md` are an additive surface that must not silently diverge
from the DB persona (WS-E owns the sync story).

### 2b. The Workflow tool (deterministic multi-phase pipelines)

For non-trivial tasks the coordinator encodes the selected workflow's phases as
deterministic control flow — `phase()`, `pipeline()`, `parallel()` — instead of
free-form prose. The **Workflow is the coordinator**. Parallel phases (e.g.
BUILD) issue multiple `Agent` calls in one step; sequential phases spawn each
specialist after the previous returns.

**Structured hand-back:** use the Workflow `schema` option so specialist and
phase results are validated tool output the orchestrator can read directly —
replacing the retired pane-scrape of a `RELEASED <status>` stdout marker.

### 2c. Background agents (keep the loop responsive)

Both mechanisms run with `run_in_background: true` by default. The orchestrator
returns to `ScheduleWakeup` immediately; the unit is reaped on a later tick via
terminal `task.status` + `attrs.completion`, or earlier via a native completion
notification (out-of-band REAP on that one task, no reschedule). Background
agents are independent of the orchestrator's turn — they survive an
orchestrator context reset and are reaped by the next `/orch-start` session.

---

## 3. Lifecycle

### 3a. Dispatch — orchestrator → coordinator

The orchestrator builds the unit prompt with the canonical assembler
(`scripts/build-coord-prompt.py` — see `orch-start.md` §6d "Unit prompt
assembly"), dispatches the unit as a background agent, and writes
`attrs._dispatch_ref = {kind, id, started_at}` on the Taskforge task. It does
**not** await the unit.

### 3b. Work — coordinator → specialists

At each phase the coordinator runs the checkpoint helper
(`python3 scripts/checkpoint_phase.py "<task_id>" "<PHASE>"`) as its first Bash
call, then fans out to the specialists that phase needs via the `Agent` tool.
File-ownership lanes keep parallel specialists off each other:

| Specialist | Owns |
|---|---|
| `python-expert` | `.py` under `app/`, `mcp_server/`, `tests/`, `alembic/` |
| `frontend-ux` | `app/static/js/*`, JS-facing `data-*` + ARIA attrs in templates |
| `frontend-ui` | `app/static/css/*`, Tailwind class attrs in templates |
| `software-architect` | design docs; cross-layer tie-breaking; primary reviewer |

Only invoke the specialists a task actually needs (a pure-backend task skips the
frontend roles). Follow-ups to a running specialist use
`SendMessage(to="<name>", message="…")`.

### 3c. Completion — coordinator → Taskforge

The coordinator's final steps (Part 3 of the assembled prompt) are: commit on
the branch (no Claude attribution), PATCH `attrs.completion`, then **MCP-first**
`release_task(final_status="<done|blocked|waiting_on_human>")` (curl fallback).
Terminal `task.status` + `attrs.completion` is the ship signal — there is no
`RELEASED` stdout marker and no `.done` file. The coordinator does **not** push,
open a PR, or run `gh`; the orchestrator owns the ship path.

### 3d. Reap — orchestrator ships

On the next tick the orchestrator classifies the task Released (`_dispatch_ref`
set + terminal status + `completion`), runs the ship path (§7 of `orch-start.md`),
and clears `_dispatch_ref`. No temp files, logs, windows, or teammates to sweep.

---

## 4. Failure modes

- **Unit dies / returns null.** Left stuck `in_progress` with no checkpoint →
  `release_task blocked` + `add_note` (last-resort salvage). With a checkpoint →
  lease expiry reclassifies it **Resumable**; it re-dispatches with
  `build-coord-prompt.py --resume` from its last phase. Use
  `.filter(Boolean)` at the orchestrator boundary so a null unit never silently
  strands a task.
- **Orphan.** `_dispatch_ref` names a unit not visible this session → treat as
  Fresh; `sweep_expired_leases` reverts the lease in ≤30 min. Clear the stale
  ref (and any retired `_coordinator_*` marker) on reclaim.
- **Gate violations surface as-is.** A unit that tries `in_progress` without a
  bound published `WorkflowVersion`, or an AI task missing `repo_path` /
  `acceptance_criteria`, still raises the existing 409/422. v2 does not soften
  these; the orchestrator routes the failure to Telegram if it can't self-heal.
- **Quota pause.** v2 does **not** SIGTERM / `TeamDelete` in-flight units on a
  quota pause. It heartbeats leases and reschedules; background units continue
  or, if they die, re-enter as Fresh/Resumable. (The retired mechanism SIGTERMed
  heavyweight `claude -p` coords because they shared the orchestrator's quota.)

---

## 5. Invariants preserved

Leases (`claim` / `next` / `heartbeat` / `release` / `sweep_expired_leases`);
an `AuditEvent` on every mutation tied to the acting `Actor`; the DB
`WorkflowVersion` gate; the AI-field gate; DAG cycle detection;
`client_request_id` idempotency; Telegram/PushNotification HITL with the fixed
command allow-list; and prompt-injection hygiene (`task.description`,
`task.attrs`, `Note.body_markdown` are untrusted data — fence them into
sub-prompts, HTML-escape for the GUI). Native subagents act as the same actors
and inherit these rules; no mutation path bypasses audit.
