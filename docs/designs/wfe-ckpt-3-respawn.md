# WFE-CKPT-3: SIGTERM-on-Pause + Checkpoint-Aware Respawn

**Status:** Design  
**Author:** software-architect  
**Date:** 2026-04-22  
**Task:** fb683703-974d-4451-84dd-47059a26c893

---

## Assumption Confirmation: No `.py` / `.js` / `.css` Changes

This task is a **docs/policy change only**. All orchestrator logic lives in the Markdown
specs that the orchestrator Claude session interprets at runtime:

- `commands/orch-start.md` — the runnable form (modified here)
- `docs/periodic-workflow.md` — the spec (modified here, in sync)

No Python scripts require changes. `scripts/build-coord-prompt.py` already has `--resume`
(implemented in WFE-CKPT-2). `scripts/checkpoint_phase.py` is unchanged. No new scripts
are needed.

**If a build specialist believes a script change is needed:** stop and flag it. The only
legitimate boundary cases are (a) `session-usage-check.sh` (already parses usage), and
(b) `build-coord-prompt.py --resume` (already implemented). Everything else is orchestrator
prose.

---

## 1. §0a Quota-Pause SIGTERM Protocol

### What changes

Current §0a (≥94% path), step 2:
> "Do NOT kill coordinator tmux windows. Running coordinators continue independently…"

**New behavior:** on pause, SIGTERM every in-flight coord's processes. Keep the tmux window,
worktree, and lease. Coordinators share the orchestrator's Anthropic quota and will be killed
by the quota limit anyway — a controlled SIGTERM is strictly better than an uncontrolled death.

### §0b context gate — NOT changed

§0b fires when the **orchestrator's context window** is near full. That is orthogonal to
quota — coordinators are independent `claude -p` processes and do not share the orchestrator's
context. §0b keeps its current behavior: heartbeat in-flight tasks, no SIGTERM, no wakeup.

Justification: context exhaustion ends the orchestrator session entirely; there is nothing to
resume. The coordinator processes live on in their tmux windows and will be reaped by the next
orchestrator session. SIGTERM-on-context-exit would orphan their work with no respawn path.

### Exact ordering (replaces §0a ≥94% steps 1–7)

```
SIGTERM ORDER:
  1. HEARTBEAT all in-flight tasks (one final lease renewal — leases are 30 min; pause is up to 5 h)
  2. For each in-flight coord window: SIGTERM coord processes (see bash below)
  3. Clear attrs._coordinator_tmux_window on each SIGTERM'd task (one PATCH per task)
  4. Write RESET_EPOCH to /tmp/orch-quota-paused-until
  5. Compute DELAY = min(3600, max(60, RESET_EPOCH + 60 - now))
  6. Telegram: "⏸ Session quota at <N>% — pausing. SIGTERMed <K> coordinators; will respawn on resume."
  7. ScheduleWakeup(DELAY). END TICK.
```

Steps 1 before 2: heartbeat ensures leases survive the entire pause window regardless of SIGTERM
outcome. Step 2 before 3: clear the window attr only after processes are dead (avoids a tick
between kill and clear mis-classifying the task as "in-flight with dead process").

### SIGTERM bash per coord

```bash
COORD_WINDOW="coord-<short>"

# Step A: enumerate all pane PIDs in the coord's window (includes specialist split-panes)
PANE_PIDS=$(tmux list-panes -t "$COORD_WINDOW" -F '#{pane_pid}' 2>/dev/null || echo "")

if [ -n "$PANE_PIDS" ]; then
  # Step B: SIGTERM every pane PID and its child tree
  for PANE_PID in $PANE_PIDS; do
    # Kill children first, then the pane shell itself
    pkill -TERM -P "$PANE_PID" 2>/dev/null || true
    kill -TERM "$PANE_PID" 2>/dev/null || true
  done

  # Step C: bounded grace period (10 s is enough for claude -p to flush)
  sleep 10

  # Step D: SIGKILL any survivors
  for PANE_PID in $PANE_PIDS; do
    pkill -KILL -P "$PANE_PID" 2>/dev/null || true
    kill -KILL "$PANE_PID" 2>/dev/null || true
  done
fi

# Step E: touch the .done file so the Monitor doesn't treat this as a crash
#          (the orchestrator knows it killed this coord — no partial-ship needed)
touch /tmp/coord-<short>.done
printf "99\n" > /tmp/coord-<short>.exit   # non-zero exit; REAP path checks checkpoint before §3b
```

### What happens to specialist split-panes

Specialists are launched inside the coord's tmux window as additional split-panes:
```bash
tmux split-pane -d -h -t "coord-<short>" "... script -qefc 'claude -p ...' ..."
```
`tmux list-panes -t "coord-<short>"` returns **all** pane PIDs including specialist panes.
The loop above kills every pane process → specialists die alongside the coordinator.
Specialist `.done` files (`/tmp/specialist-*-<short>.done`) accumulate but are swept by §3c
cleanup on the eventual successful reap. The tmux window remains with its (now-dead) panes —
do not kill the window or the panes themselves.

### Worktree and tmux window: preserved

After SIGTERM:
- Worktree (`.worktrees/task-<short>/`) — NOT removed. Contains partial commit progress.
- Tmux window (`coord-<short>`) — NOT killed. Reused on respawn (see §3).
- `attrs._coordinator_tmux_window` — **CLEARED** on Taskforge. This is what makes the next
  tick classify the task as resumable rather than in-flight (see §2).

### Pre-pause freshness check

The existing §0a freshness check (verify `RESET_EPOCH` from `/tmp/orch-session-usage.json` is
< 90s stale, else run `--once` re-poll) runs **before** the SIGTERM sequence, same as today.
SIGTERM is inserted between the freshness check and writing the paused-until file.

---

## 2. Resumable Classification

### Predicate

A task is **resumable** when ALL of:

```
task.status IN ('ready', 'in_progress')
AND attrs._coordinator_tmux_window is unset (or the key is absent)
AND attrs.checkpoint is present
AND attrs.checkpoint.phases_completed is a non-empty list
AND (task.status == 'ready'
     OR (task.status == 'in_progress'
         AND tmux window 'coord-<short>' does not exist OR has no live processes))
```

The `IN ('ready', 'in_progress')` broadening is **required**. The task description says
"Heartbeat the lease one final time" + "Do NOT call release_task" — so paused tasks remain
`in_progress`. Only option (b) (classify `in_progress` tasks with no window + checkpoint as
resumable) works without a `release_task` round-trip. Option (a) (flip to `ready`) is not
used.

### Why `_coordinator_tmux_window` is cleared on SIGTERM

After SIGTERM, the task has no live coord. Clearing the window attr achieves two things:
1. The §2 classification logic (existing: "window attr unset → Fresh") picks up the task.
2. Within the Fresh bucket, we add the checkpoint check to produce the Resumable sub-bucket.

If we kept the window attr set, the next tick would classify the task as "in-flight" (window
attr set + no `.done` + `in_progress`), heartbeat it, and never respawn. Wrong.

### How to emit Resumable from the §2 classification union

The three existing `list_tasks` queries already fetch `status=in_progress` tasks. After
fetching, the existing client-side classification loop routes on `_coordinator_tmux_window`:

```
for task in all_fetched_tasks:
    if window attr set AND terminal status AND completion present → Released
    elif window attr set AND .done exists AND in_progress → Crashed (→ NEW: check checkpoint before §3b)
    elif window attr set → In-flight
    else:  # window attr unset
        if checkpoint.phases_completed is non-empty → RESUMABLE (NEW)
        else → Fresh
```

The Resumable sub-bucket is carved out of what was the monolithic "Fresh" bucket. Fresh tasks
with no checkpoint (or empty `phases_completed`) continue to the existing top-up path unchanged.

### The two-way conflict case

If `_coordinator_tmux_window` is set AND the window exists AND `checkpoint.phases_completed`
is non-empty: classify as **In-flight** (heartbeat only). A live coord is already working from
the checkpoint; the checkpoint is informational. Only when the window attr is cleared (by SIGTERM
or REAP) does the task enter the Resumable bucket.

---

## 3. Top-Up Branching for Resumable

### Fresh vs Resumable shell invocation delta

**Fresh (existing):**
```bash
python3 ${ORCHESTRATION_DIR:-orchestration}/scripts/build-coord-prompt.py \
  --task-id <full-uuid> \
  --workflow <workflow-slug> \
  --branch task/<short>-<slug> \
  --worktree <worktree-path>
```

**Resumable (new — adds `--resume`):**
```bash
python3 ${ORCHESTRATION_DIR:-orchestration}/scripts/build-coord-prompt.py \
  --task-id <full-uuid> \
  --workflow <checkpoint.workflow> \
  --branch task/<short>-<slug> \
  --worktree <worktree-path> \
  --resume
```

Key difference: `--workflow` is taken from `attrs.checkpoint.workflow` (not re-scored). The
checkpoint records which workflow was running when the phase was saved; re-scoring could pick
a different workflow, which `_validate_resume` would reject anyway. Use the checkpoint's
recorded value.

### Worktree reuse (idempotent)

The worktree was preserved on SIGTERM. `git worktree add` would fail with "already exists".
Use existence-check:

```bash
WORKTREE_PATH=<repo_path>/.worktrees/task-<short>
if [ ! -d "$WORKTREE_PATH" ]; then
  # Defensive: worktree was pruned manually
  cd <repo_path>
  git fetch origin dev
  git worktree add -b task/<short>-<slug> "$WORKTREE_PATH" origin/dev
  add_note(<task>, "resumable: worktree was absent — recreated from origin/dev")
fi
# else: reuse existing worktree (normal case)
```

### tmux window reuse

The tmux window `coord-<short>` still exists (dead panes from the killed coord). Use
`tmux respawn-window -k` to kill the dead pane(s) and start fresh in the same window slot:

```bash
COORD_WINDOW="coord-<short>"
if tmux has-window -t "$COORD_WINDOW" 2>/dev/null; then
  tmux respawn-window -k -t "$COORD_WINDOW" \
    "cd <worktree-path> && \
     script -qefc 'claude -p \"$(cat /tmp/coord-<short>.prompt)\" --model ${MODEL} \
       --dangerously-skip-permissions --max-budget-usd 10 --no-session-persistence' \
       /tmp/coord-<short>.log; \
     echo \$? > /tmp/coord-<short>.exit; \
     touch /tmp/coord-<short>.done"
else
  # Window was manually killed — create fresh
  tmux new-window -d -n "$COORD_WINDOW" \
    "cd <worktree-path> && \
     script -qefc 'claude -p \"$(cat /tmp/coord-<short>.prompt)\" --model ${MODEL} \
       --dangerously-skip-permissions --max-budget-usd 10 --no-session-persistence' \
       /tmp/coord-<short>.log; \
     echo \$? > /tmp/coord-<short>.exit; \
     touch /tmp/coord-<short>.done"
fi
```

### Stale sidecar cleanup (before respawn)

Before building the new prompt or launching the window, clean up stale markers from the
previous run. Order matters:

```bash
# 1. Prompt — overwrite (build-coord-prompt.py writes it)
#    No explicit rm needed; the script always overwrites.

# 2. Done marker — MUST remove before new launch to prevent Monitor firing old event
rm -f /tmp/coord-<short>.done

# 3. Exit code — clear stale non-zero
rm -f /tmp/coord-<short>.exit

# 4. Log — rename previous run's log for diagnostic retention (optional but helpful)
[ -f /tmp/coord-<short>.log ] && mv /tmp/coord-<short>.log /tmp/coord-<short>.log.prev

# 5. Specialist sidecars — leave in place; 3c cleanup sweeps them after final reap
#    Do NOT rm specialist .done files — a surviving specialist from a prior phase
#    is not expected but leaving them doesn't cause harm.
```

### Where the new prompt goes

`build-coord-prompt.py` output always writes to `/tmp/coord-<short>.prompt`.
Overwriting is safe — the previous prompt is stale. No special handling needed.

### `attrs._coordinator_tmux_window` after respawn

After `tmux respawn-window` (or `new-window`), write the window name back:
```
PATCH attrs._coordinator_tmux_window = "coord-<short>"
```
Same as fresh spawn (§6d step 3 of `orch-start.md`).

---

## 4. Quota-Headroom Gate Before Respawn

### Default threshold

`ORCH_RESUME_USAGE_HEADROOM` env var. Default: `75`.

Rationale: 75% leaves 19 percentage points before the 94% pause threshold. A resumable coord
that runs 3+ phases before hitting quota again will consume meaningfully less quota than a
fresh start. But respawning at 80–93% means the coord dies again within minutes; at 75% there's
reasonable headroom for at least one full phase.

### Pseudocode in TOP-UP

```
resumable_queue = [t for t in fresh_candidates if checkpoint non-empty]
truly_fresh_queue = [t for t in fresh_candidates if no checkpoint]

HEADROOM = int(os.environ.get("ORCH_RESUME_USAGE_HEADROOM", 75))

for candidate in resumable_queue:
    if USAGE_PERCENT >= HEADROOM:
        add_note(candidate.id,
            f"deferred resume: USAGE_PERCENT={USAGE_PERCENT}% >= headroom={HEADROOM}% — "
            "will retry on next tick when quota drops")
        pending_resume_count += 1
        continue  # do NOT consume a SLOT
    # proceed with respawn
    spawn_resumable(candidate)
    SLOTS -= 1
    if SLOTS == 0:
        break
```

Walk resumable queue first (oldest-`updated_at`), then truly-fresh. This ensures tasks that
were mid-flight get priority over tasks that haven't started — they already consumed quota and
their partial work is valuable.

### Relationship to §0a-pre during quota pause

During a quota-pause chain (minimal ticks), USAGE_PERCENT ≥ 94% and the headroom gate
(default 75%) will also fire — resumables are deferred. This is correct and expected: the
minimal-tick skips §5 (TOP-UP) entirely, so the gate is never even reached. The headroom gate
only matters on the first full tick after quota clears, when USAGE_PERCENT might still be
70–85%.

### Tick log counters

Add to the §11 reschedule `reason` string:
```
"tick complete — reaped R, heartbeat H, launched L (fresh=F resume=Rs), deferred D "
"(fresh=DF resume=DR headroom=<N>%), auto-queued Q; now I in-flight; next poll in 20m"
```
Two new counters:
- `Rs` = resumable tasks spawned this tick
- `DR` = resumable tasks deferred due to headroom gate (with the headroom % shown)

This makes it immediately visible in the wakeup reason why resumable tasks are accumulating.

---

## 5. REAP COORD_DONE Decision Tree

### Current §3b path (crashed bucket)

Today: `_coordinator_tmux_window` set AND `.done` exists AND `task.status == in_progress`
→ `add_note`, `release_task('blocked')`, partial-ship, Telegram, self-improvement task.

### New decision tree (insert before §3b fall-through)

```
On COORD_DONE <short> exit≠0 last≠"RELEASED <status>":

  Re-fetch task from Taskforge.

  IF attrs.checkpoint.phases_completed is non-empty:
    # This coord checkpointed at least once — resume is possible
    1. DO NOT release_task. Task stays in_progress.
    2. Clear attrs._coordinator_tmux_window (PATCH)
    3. Run §3c tempfile cleanup: rm /tmp/coord-<short>.{prompt,exit}
       rename /tmp/coord-<short>.log → /tmp/coord-<short>.log.prev
       DO NOT remove worktree.
       DO NOT rm /tmp/coord-<short>.done (leave it — the Monitor already fired; no second event)
    4. add_note(task, "coord died after phases_completed={checkpoint.phases_completed} — "
                      "resume queued for next tick (will respawn from {checkpoint.current_phase})")
    5. Telegram: "♻ <short> died after {checkpoint.phases_completed[-1]} — "
                 "will resume next tick from {checkpoint.current_phase}"
    6. DO NOT partial-ship. DO NOT file self-improvement task (this is expected behavior).
    7. END — next tick classifies task as Resumable and respawns.

  ELSE:
    # No checkpoint — fall through to existing §3b (last-resort salvage)
    → add_note, release_task('blocked'), partial-ship, Telegram blocker, self-improvement
```

### Monitor event vs scheduled-tick COORD_DONE

The Monitor fires `COORD_DONE <short>` on any `.done` file appearance — including the
SIGTERM-triggered `touch /tmp/coord-<short>.done` from §1 above. The orchestrator must
distinguish:

- SIGTERM'd on pause → `.done` appears AND task `attrs._coordinator_tmux_window` was
  already cleared (by the SIGTERM sequence, which clears the attr before touching `.done`).
  On re-fetch the task has no window attr. Classification: skip — no REAP action needed,
  the SIGTERM sequence already handled it.
- True crash → `.done` appears AND window attr is still set (coord died without the
  orchestrator's SIGTERM). Classification: run the decision tree above.

The early-reap handler (preflight Monitor) already re-fetches the task and checks window
attr — this distinction falls out naturally.

---

## 6. Salvage Path Decision

**Keep §3b as last-resort fallback.** Do not delete.

Rationale:
1. Tasks that die before the first phase checkpoint (`phases_completed == []`) have no resume
   point. They need salvage.
2. Pre-CKPT-2 tasks in flight (checkpoint block not yet inserted into the workflow) have no
   `attrs.checkpoint` at all. They need salvage.
3. Removing salvage entirely would strand these tasks permanently (no release, no PR, no
   owner notification).

### Updated §3b framing

Rename from "Crashed bucket" to "Crashed (last-resort salvage)" and add the guard:

```
§3b applies ONLY when:
  attrs.checkpoint is absent OR attrs.checkpoint.phases_completed is empty

If attrs.checkpoint.phases_completed is non-empty → §5 decision tree above (resume path).
```

The salvage steps themselves (add_note, release blocked, partial-ship, Telegram, self-improvement)
are unchanged. Checkpoint coverage reaching 100% will naturally shrink the salvage path to
zero-frequency — it becomes a dead letter without needing to delete the spec. When coverage
is confirmed 100% (all workflow files have checkpoint calls inserted per WFE-CKPT-2), document
that §3b is vestigial but keep it for safety.

---

## 7. Manual Smoke Test

Paste into the PR description. Assumes a test task exists with `status=ready`,
`assigned_to=claude_orch`, `attrs.repo_path` set, and a workflow that has checkpoint calls.

```bash
#!/usr/bin/env bash
# WFE-CKPT-3 smoke test: pause → SIGTERM → resume round-trip
# Prerequisites: orchestrator session running; TASKFORGE_API_KEY and TASKFORGE_BASE_URL set;
#                tmux session with the orchestrator active.
set -e
SHORT="<8-char task id>"           # EDIT: the test task's short id
TASK_ID="<full uuid>"              # EDIT: the test task's full uuid
COORD_WINDOW="coord-${SHORT}"

# (a) Start a coord on the test task — trigger via orchestrator tick or manual top-up
echo "=== (a) Waiting for coord to appear in tmux..."
while ! tmux has-window -t "$COORD_WINDOW" 2>/dev/null; do sleep 5; done
echo "Coord window found: $COORD_WINDOW"

# (b) Wait until BUILD checkpoint written (phases_completed includes DESIGN)
echo "=== (b) Waiting for BUILD checkpoint..."
while true; do
  PHASES=$(curl -s -H "X-API-Key: $TASKFORGE_API_KEY" \
    "${TASKFORGE_BASE_URL:-http://taskforge-prod:8000}/tasks/$TASK_ID" \
    | python3 -c "import sys,json; t=json.load(sys.stdin); \
      print(json.dumps(t.get('attrs',{}).get('checkpoint',{}).get('phases_completed','NONE')))")
  echo "  phases_completed=$PHASES"
  echo "$PHASES" | grep -q '"DESIGN"' && break
  sleep 15
done
echo "BUILD checkpoint written."

# (c) Simulate quota pause: set USAGE_PERCENT high + write paused-until file
echo "=== (c) Simulating quota pause..."
FUTURE=$(python3 -c "import time; print(int(time.time()) + 3600)")
echo "$FUTURE" > /tmp/orch-quota-paused-until
# The orchestrator's §0a-pre will see this on the next tick.
# Manually trigger the SIGTERM sequence for testing:
PANE_PIDS=$(tmux list-panes -t "$COORD_WINDOW" -F '#{pane_pid}' 2>/dev/null || echo "")
for PID in $PANE_PIDS; do pkill -TERM -P "$PID" 2>/dev/null || true; kill -TERM "$PID" 2>/dev/null || true; done
sleep 10
for PID in $PANE_PIDS; do pkill -KILL -P "$PID" 2>/dev/null || true; kill -KILL "$PID" 2>/dev/null || true; done
touch /tmp/coord-${SHORT}.done
printf "99\n" > /tmp/coord-${SHORT}.exit
# Clear the window attr (orchestrator does this; simulate it here)
curl -s -X PATCH -H "X-API-Key: $TASKFORGE_API_KEY" -H "Content-Type: application/json" \
  -d '{"attrs": {"_coordinator_tmux_window": null}}' \
  "${TASKFORGE_BASE_URL:-http://taskforge-prod:8000}/tasks/$TASK_ID" > /dev/null
echo "SIGTERM sent; window attr cleared."

# (d) Confirm task is in_progress with checkpoint but no window attr
echo "=== (d) Checking task state..."
curl -s -H "X-API-Key: $TASKFORGE_API_KEY" \
  "${TASKFORGE_BASE_URL:-http://taskforge-prod:8000}/tasks/$TASK_ID" \
  | python3 -c "
import sys,json
t=json.load(sys.stdin)
a=t.get('attrs',{})
print('status:', t['status'])
print('_coordinator_tmux_window:', a.get('_coordinator_tmux_window','(absent)'))
print('checkpoint.phases_completed:', a.get('checkpoint',{}).get('phases_completed'))
print('checkpoint.current_phase:', a.get('checkpoint',{}).get('current_phase'))
"

# (e) Lower usage below headroom (75%)
echo "=== (e) Simulating quota recovery..."
rm -f /tmp/orch-quota-paused-until
# If using manual override: export ORCH_MANUAL_RESET_EPOCH= to clear
# Watcher will report fresh low usage on next --once call.

# (f) Trigger next tick (or wait 20 min; for testing, trigger manually)
echo "=== (f) Triggering next tick — check orchestrator session for respawn..."
# Wait for the new coord window to appear
rm -f /tmp/coord-${SHORT}.done  # clear stale .done so Monitor doesn't fire old event
sleep 30  # give orchestrator time to tick
while ! tmux has-window -t "$COORD_WINDOW" 2>/dev/null; do sleep 5; done
echo "Coord window respawned."

# (g) Observe resume banner in new log
echo "=== (g) Checking new coord log for RESUME POINT banner..."
sleep 10  # give claude -p time to start
grep -m1 "RESUME POINT\|RESUMING WORKFLOW" /tmp/coord-${SHORT}.log \
  && echo "PASS: resume banner found" \
  || echo "FAIL: resume banner not found — check /tmp/coord-${SHORT}.log"
grep "current_phase.*BUILD\|BUILD" /tmp/coord-${SHORT}.log | head -3
echo "Done. Monitor /tmp/coord-${SHORT}.log for continued BUILD execution."
```

---

## 8. Edge Cases

### 8a. Task has checkpoint but worktree was pruned

If `.worktrees/task-<short>/` is absent at respawn time (should not happen — we never prune
on pause, but a manual `git worktree remove` is possible):

```bash
if [ ! -d "$WORKTREE_PATH" ]; then
  git fetch origin dev
  git worktree add -b task/<short>-<slug> "$WORKTREE_PATH" origin/dev
  add_note(task, "resumable: worktree was absent — recreated. "
                 "Coordinator will resume at checkpoint.current_phase but prior commits "
                 "on the branch may be intact on the remote (check git log).")
fi
```

The branch `task/<short>-<slug>` likely still exists on origin (was never pushed during
this partial run, but let's check). Use `git fetch` to pull any existing remote refs.
If the branch exists on origin, `git worktree add ... origin/task/<short>-<slug>` preserves
partial commit history. If not, the coordinator resumes from `origin/dev` with an empty
working tree — the checkpoint says "BUILD started" but no BUILD commits exist. The coordinator
will redo BUILD from scratch, which is correct (idempotent).

### 8b. Branch no longer exists on disk (worktree present, branch gone)

If someone ran `git worktree remove --force .worktrees/task-<short>` manually (removes both
the worktree directory AND the local branch ref), treat as case 8a — same recovery path.
The worktree directory absence is the detection signal; branch existence is secondary.

### 8c. `_coordinator_tmux_window` set, window exists, `phases_completed` non-empty

This is the "coordinator is still alive after a previous checkpoint." Classify as **In-flight**
(heartbeat only). The coordinator is making progress from the checkpoint; no intervention
needed. The resumable path fires only after the window attr is cleared.

The REAP crashed-bucket check (step 5 decision tree) is gated on `.done` existing AND `status
== in_progress`. If the coord is alive, `.done` has not been written — neither §3b nor the
resume path fires. Correct.

### 8d. Checkpoint older than workflow version (`checkpoint.workflow_version` ≠ `attrs.workflow_version_id`)

This means the workflow was re-published mid-run. The coordinator was running an older version;
the checkpointed phases may not correspond to phases in the current version.

**Detection:** at respawn time, before calling `build-coord-prompt.py --resume`, compare:
```bash
CKPT_WF_VERSION=$(curl ... | jq -r '.attrs.checkpoint.workflow_version')
TASK_WF_VERSION=$(curl ... | jq -r '.attrs.workflow_version_id')
if [ "$CKPT_WF_VERSION" != "$TASK_WF_VERSION" ]; then
  # version mismatch
fi
```

**Resolution (chosen):** warn + block (do not restart from beginning automatically).

Rationale: "restart from beginning" silently discards completed phases and may re-run
expensive BUILD specialists. "bail to blocked" surfaces the mismatch to the owner who can
decide: force-resume with the old version, or close the task and requeue. The owner has the
context; the orchestrator does not.

Steps on mismatch:
1. `add_note(task, "checkpoint workflow_version {ckpt_ver} does not match current "
                   "attrs.workflow_version_id {task_ver} — blocking for owner review")`
2. `release_task('blocked')`
3. Partial-ship (push branch + open PR with partial work).
4. Telegram: `"⚠ <short> checkpoint/workflow version mismatch — cannot auto-resume. "
              "Review and unblock manually or requeue."`

This path is expected to be rare (workflow re-publish during an active run) and worth a human
decision.

---

## 9. State-Machine Summary

```
                  ┌─────────────────────────────────────────────────────────────────────┐
                  │                    TASK STATUS IN TASKFORGE                         │
                  └─────────────────────────────────────────────────────────────────────┘

  ready ──[top-up, no checkpoint]──► in_progress (running coord)
                                            │
                          ┌─────────────────┼──────────────────────────────────┐
                          │                 │                                    │
              [§0a ≥94%   │    [coord crash WITH checkpoint]    [coord crash WITHOUT checkpoint]
              SIGTERM]    │    §5 decision tree                 §3b salvage
                          ▼    SIGTERM-equivalent outcome       add_note + release(blocked) +
                   in_progress (no tmux window,                 partial-ship + Telegram + WFH
                   checkpoint present)
                          │  ← "pending_resume bucket"
                          │
              ┌───────────┴───────────────────┐
              │ USAGE_PERCENT >= headroom (75%)│ USAGE_PERCENT < headroom
              ▼                                ▼
          deferred               in_progress (running coord, resumed)
          (next tick)                         │
                                    [release done]
                                              ▼
                                     done → ship path (§7)
                                     → PR + auto-merge


  Legend:
  ─── status transitions (arrows show new state)
  [...] edge condition / trigger

  Key invariants:
  - task.status=in_progress WITH no window attr AND non-empty checkpoint = "resumable"
  - task.status=in_progress WITH window attr set = "in-flight" (live coord running)
  - task.status=in_progress WITH no window attr AND empty/no checkpoint = "legacy orphan"
    (treated as fresh, spawns a new coord from scratch — existing §2 orphan handling)
```

### Status choices: why `in_progress` not `ready` on pause

The task description says "Heartbeat the lease one final time" + "Do NOT call `release_task`".
`release_task` is the only mechanism to transition `in_progress → ready`. Without it, the task
stays `in_progress` — which is correct. The orchestrator holds the lease; heartbeat renews it.
The resumable classification broadens §2 to include `in_progress` tasks with no window attr.

Option (a) (flip to `ready`) was rejected because:
- `release_task` requires specifying a final status; using a non-terminal status to signal
  "not done, just paused" would require a new API concept.
- Clearing the lease and re-acquiring (claim_task on resume) is possible but introduces a
  window where the lease-sweeper could revert the task to `todo` if the next tick is delayed.
  Keeping the lease via heartbeat is safer.

---

## 10. Spec Sync Rule

**Both files must be updated in the same commit:**

- `commands/orch-start.md` — the runnable orchestrator command (what the orchestrator Claude
  reads and executes)
- `docs/periodic-workflow.md` — the spec (the why, the edges, the design rationale)

These two must never disagree. Per the repo README and the `orch-start.md` preamble:
> "Canonical spec: `orchestration/docs/periodic-workflow.md`. Read it if anything in this
> command is ambiguous — that doc is the source of truth and this command is a runnable summary."

The BUILD specialist making the edits must update both in a single commit. The review phase
(software-architect) will check both files for consistency. A diff that touches only one file
is incomplete and will be sent back.

### Specific sections to update in each file

**`commands/orch-start.md`:**
- §0a (≥94% path): replace "Do NOT kill coordinator tmux windows" + steps 1–7 with the new
  SIGTERM sequence from §1 of this design.
- §2 (classification): add Resumable sub-bucket below Fresh.
- §3 (REAP): add checkpoint-check guard before §3b fall-through.
- §5 (TOP-UP): add resumable queue, headroom gate, `--resume` spawn branch.

**`docs/periodic-workflow.md`:**
- §4 Session quota gate (§0a summary): update to reflect SIGTERM behavior.
- §4a REAP: add resumable classification to the classification table; add checkpoint decision
  tree to the crashed-bucket description.
- §4c TOP-UP: add resumable sub-queue, headroom gate, `--resume` spawn note.
- §13 Known limitations: mark or close the "coord deaths become invisible" item once this lands.

---

## Appendix: Checklist for BUILD Specialist

- [ ] §0a SIGTERM sequence in `commands/orch-start.md` replaces "Do NOT kill" language
- [ ] §0b context gate unchanged (no SIGTERM — orthogonal to quota)
- [ ] §2 classification adds Resumable sub-bucket with exact predicate
- [ ] `_coordinator_tmux_window` cleared on SIGTERM (not on §3c — §3c is for completed tasks)
- [ ] §3 REAP decision tree: checkpoint guard before §3b
- [ ] §3b retitled "last-resort salvage"; guard condition added (`phases_completed empty`)
- [ ] §5 TOP-UP: resumable queue walked first; headroom gate; `--resume` spawn
- [ ] `ORCH_RESUME_USAGE_HEADROOM` env var documented with default 75
- [ ] Tick reschedule reason updated with `resume=Rs deferred=DR` counters
- [ ] `docs/periodic-workflow.md` updated in same commit (all corresponding sections)
- [ ] Smoke test documented in PR body
