# WFE-CKPT-2: Checkpoint Helper + `--resume` Mode Design

**Status:** Design  
**Author:** software-architect  
**Date:** 2026-04-22

---

## 1. Helper Script Contract

### Language choice: Python (`.py`)

File: `scripts/checkpoint_phase.py`

Rationale: The entire toolkit is Python (`build-coord-prompt.py`, `sync_workflow.py`). Shell scripts cannot be unit-tested with `pytest` + HTTP mocks. The helper makes HTTP calls; mocking those requires Python infrastructure (`pytest-httpx`). Bash would produce an untestable artifact.

### CLI

```
python3 scripts/checkpoint_phase.py <task-id> <phase-being-entered>
```

| Argument | Positional # | Description |
|---|---|---|
| `task-id` | 1 | UUID or short ID of the task |
| `phase-being-entered` | 2 | Name of the phase being **entered** (e.g. `DESIGN`, `BUILD`, `RE-VERIFY`) |

Phase names must match the `phases:` frontmatter list in the workflow file **verbatim** (case-sensitive). Coordinator is responsible for passing the correct string.

### Environment Variables

| Var | Required | Default | Description |
|---|---|---|---|
| `TASKFORGE_API_KEY` | yes | — | Value of `X-API-Key` request header |
| `TASKFORGE_BASE_URL` | no | `http://taskforge-prod:8000` | API base URL |
| `COORD_WINDOW` | no | derived | tmux window name stored in `by_coord` |

`by_coord` derivation order:
1. `$COORD_WINDOW` env var (preferred; set by orchestrator when launching coordinator).
2. `tmux display-message -p '#W'` (if `$TMUX` is set and `tmux` is on PATH).
3. `"unknown"` (fallback for non-tmux environments, CI, tests).

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success (or idempotent no-op — already on this phase) |
| 1 | API error: PATCH/GET returned non-2xx, or network failure |
| 2 | Task not found (GET returned 404) |
| 3 | `TASKFORGE_API_KEY` not set in environment |

### Algorithm (read-modify-write)

```python
def run(task_id: str, phase: str) -> int:
    api_key = os.environ.get("TASKFORGE_API_KEY")
    if not api_key:
        print("ERROR: TASKFORGE_API_KEY not set", file=sys.stderr)
        return 3

    base = os.environ.get("TASKFORGE_BASE_URL", "http://taskforge-prod:8000").rstrip("/")

    # Step 1: GET current task
    resp = httpx.get(f"{base}/tasks/{task_id}", headers={"X-API-Key": api_key})
    if resp.status_code == 404:
        print(f"ERROR: task {task_id} not found", file=sys.stderr)
        return 2
    resp.raise_for_status()  # exit 1 on other non-2xx
    task = resp.json()

    # Step 2: Extract and mutate attrs.checkpoint
    attrs = dict(task.get("attrs") or {})
    ckpt = dict(attrs.get("checkpoint") or {})

    prev_phase = ckpt.get("current_phase")
    phases_completed = list(ckpt.get("phases_completed") or [])

    # Idempotency: already on this phase — exit without writing
    if prev_phase == phase:
        print(f"checkpoint: already on phase {phase!r}, no-op", flush=True)
        return 0

    # Advance: mark prev_phase done if not already in list
    if prev_phase and prev_phase not in phases_completed:
        phases_completed.append(prev_phase)

    # Build new checkpoint, preserving per-workflow extension keys
    new_ckpt = {
        **ckpt,                                                          # preserve e.g. migration_revision_id
        "workflow": ckpt.get("workflow") or attrs.get("workflow") or "", # see Open question 2
        "workflow_version": (
            ckpt.get("workflow_version") or attrs.get("workflow_version_id") or ""
        ),
        "phases_completed": phases_completed,
        "current_phase": phase,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by_coord": _get_by_coord(),
    }

    attrs["checkpoint"] = new_ckpt

    # Step 3: PATCH full attrs blob back (see Open question 1 for merge vs replace)
    resp = httpx.patch(
        f"{base}/tasks/{task_id}",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        json={"attrs": attrs},
    )
    resp.raise_for_status()  # exit 1 on failure
    print(f"checkpoint: {prev_phase or '(none)'!r} -> {phase!r}", flush=True)
    return 0
```

### Idempotency Rules

| Situation | Behavior |
|---|---|
| `attrs.checkpoint` absent or `null` | Create fresh: `{phases_completed: [], current_phase: phase, ...}` |
| `current_phase == phase` (re-entering same phase) | Exit 0, **no PATCH** |
| `phase` already in `phases_completed` | Do not re-append; set `current_phase = phase`; still advance normally |
| `prev_phase` already in `phases_completed` | Do not re-append `prev_phase` |

The helper is idempotent with respect to the `current_phase` guard. Re-calling with the same phase is safe. Calling out-of-order phases is not guarded (the coordinator must call in sequence).

---

## 2. `--resume` Mode in `build-coord-prompt.py`

### CLI Delta

Add one boolean flag to `argparse` in `main()`:

```
python3 scripts/build-coord-prompt.py \
    --task-id <uuid> --workflow <slug> --branch <branch> --worktree <path> \
    [--resume]
```

`--resume` has no value; presence means resume mode. Normal mode is unchanged.

### Injection Points

**Part 1 injection** — append to `part1()` output (or accept `resume_block: str` param and append before the separator). Content:

```
**RESUMING WORKFLOW**
This coordinator session is RESUMING from a previous interrupted run.
- Workflow: {checkpoint[workflow]}
- Phases completed: {checkpoint[phases_completed]}
- Current phase (resume here): {checkpoint[current_phase]}

Before doing anything else: run `git status` and `git log --oneline -10` to
assess partial progress from the previous session. Replay `current_phase` from
the beginning — do NOT repeat any phase listed in `phases_completed`.
```

**Part 2 injection** — prepend to the workflow body string (before the body content, after `--- WORKFLOW INSTRUCTIONS ---`):

```
>>> RESUME POINT <<<
Enter at phase "{current_phase}". Phases {phases_completed} are already done —
do not re-execute them. Find the heading matching "{current_phase}" below and
start from there.
---

```

### Part 2 Strategy: Annotate, Don't Trim

Decision: keep the full workflow body and inject a `RESUME POINT` banner at the top.

Rationale:
- Workflow body headings use formats like `## Phase 1 — DESIGN` while `checkpoint.current_phase` stores `"DESIGN"`. Reliably matching the two requires fragile regex against human-authored markdown that can drift.
- The full body gives the coordinator context about phase dependencies (e.g., what BUILD produced that INTEGRATE needs).
- The `RESUME POINT` banner is unambiguous — the coordinator reads it first and skips accordingly.
- Trimming saves some context tokens but the coordinator prompt is already bounded by workflow body size (not a concern in practice).

### Error Conditions

| Condition | Exit | Message |
|---|---|---|
| `attrs.checkpoint` missing or `{}` | 1 | `ERROR: --resume requires attrs.checkpoint but task {id} has none. Run without --resume to start fresh.` |
| `checkpoint.current_phase` null/empty | 1 | `ERROR: attrs.checkpoint.current_phase is empty — cannot determine resume point.` |
| `checkpoint.workflow != --workflow` arg | 1 | `ERROR: checkpoint workflow {ckpt_wf!r} does not match --workflow {arg_wf!r}. Use --workflow {ckpt_wf!r} to resume correctly.` |

### Code Location in `build-coord-prompt.py`

- `parser.add_argument("--resume", action="store_true", default=False)` — in `main()`.
- `_validate_resume(task, workflow_slug)` — reads `task["attrs"]["checkpoint"]`, validates all three error conditions above, returns the `checkpoint` dict or raises `SystemExit(1)`.
- `_resume_part1_block(ckpt: dict) -> str` — returns the Part 1 injection string.
- `_resume_part2_banner(ckpt: dict) -> str` — returns the Part 2 prefix.
- `build()` gains a `resume: bool = False` parameter. When `True`: call `_validate_resume`, prepend `_resume_part1_block()` output to Part 1, prepend `_resume_part2_banner()` to the workflow body.
- `main()` passes `resume=args.resume` to `build()`.

---

## 3. Workflow File Modifications

### Target: `.orchestration/workflows/*.md` (materialized cache)

Rationale: `build-coord-prompt.py` loads workflow bodies from `.orchestration/workflows/<slug>.md` as Tier 1 (lines 141–187 of `build-coord-prompt.py`). `docs/workflows/` contains only `README.md` — no runtime body files exist there. Any instructions placed in `docs/workflows/` would never reach the coordinator. The materialized cache is the only path that works today.

After the build phase, run `sync_workflow.py propose --slug <slug>` for each modified workflow to push edits back to the Taskforge API for owner review and publish. The materialized files in this repo are authoritative until the API route is live.

### Injection Pattern

At the **start** of each phase section — after the phase heading line, before any instructions — insert this block:

```markdown
**CHECKPOINT (run this first, before any other action in this phase):**
```bash
python3 scripts/checkpoint_phase.py "{{ task_id }}" "PHASE_NAME"
```
If this command exits non-zero, stop: `add_note` the error output, then `release(blocked)`.
```

The `{{ task_id }}` token is substituted by `render_workflow()` in `build-coord-prompt.py` (line 190–199).

### Example Block (verbatim) — `six-phase-build.md`, Phase 1

Locate the line beginning with `## Phase 1 — DESIGN` (approximately line 89). Insert immediately after that heading line:

```markdown
**CHECKPOINT (run this first, before any other action in this phase):**
```bash
python3 scripts/checkpoint_phase.py "{{ task_id }}" "DESIGN"
```
If this command exits non-zero, stop: `add_note` the error output, then `release(blocked)`.
```

### Phase Boundary Call Matrix

The phase names below are taken verbatim from each file's `phases:` frontmatter list.

| Workflow file | Phase names (verbatim from frontmatter) | Calls to insert |
|---|---|---|
| `six-phase-build.md` | DESIGN, BUILD, INTEGRATE, REVIEW, RE-VERIFY, COMMIT | 6 |
| `lightweight.md` | `BUILD (inline or single specialist)`, COMMIT | 2 |
| `infra-change.md` | `DESIGN (aws-solutions-architect)`, `SECURITY REVIEW (aws-security)`, `BUILD (Terraform + scripts)`, `PLAN REVIEW`, COMMIT | 5 |
| `schema-migration.md` | `DESIGN (python-expert migration plan)`, `MIGRATION REVIEW (software-architect gate)`, BUILD, `INTEGRATE (alembic heads gate mandatory)`, REVIEW, RE-VERIFY, COMMIT | 7 |
| `doc-only.md` | `EDIT (inline or lightweight)`, LINT, COMMIT | 3 |
| `security-audit.md` | AUDIT, `FINDINGS REVIEW (optional aws-security for infra scope)`, REPORT, COMMIT | 4 |

**Note for python-expert**: `lightweight.md` uses `"BUILD (inline or single specialist)"` as the phase name. This verbatim string (including parenthetical) is what must be stored in `checkpoint.current_phase`. Consider whether to trim to `"BUILD"` for consistency — see Open question 3.

---

## 4. Part 0 Update

### Location

Function `part0()` in `build-coord-prompt.py` (lines 202–240). Add a new rule **after** the existing absolute rules (after the ScheduleWakeup prohibition and the tmux `-t` targeting rule).

### Signature Change

`part0()` currently takes no arguments. It must gain a `task_id: str` parameter so the checkpoint call in the rule can embed the actual task UUID (Part 0 is not processed by `render_workflow()` token substitution).

In `build()`, change `part0()` → `part0(task["id"])`.

### Exact Wording to Add

```python
lines.append(
    "CRITICAL — Checkpoint discipline:\n"
    "At the VERY START of every phase — before spawning any specialist or writing any file —\n"
    "run the checkpoint helper as your first Bash tool call:\n"
    f"  python3 scripts/checkpoint_phase.py \"{task_id}\" \"<PHASE_NAME>\"\n"
    "where <PHASE_NAME> matches the phase heading label exactly (e.g. DESIGN, BUILD, INTEGRATE).\n"
    "If the command exits non-zero: call add_note with the stderr output, then release(blocked).\n"
    "NEVER skip this call — it is what enables safe resume if this session is interrupted."
)
```

This block goes after the existing tmux rule and before the closing of the Part 0 section separator.

---

## 5. Test Plan

### File Layout

```
tests/
    __init__.py
    conftest.py                   # shared fixtures
    test_checkpoint_phase.py      # unit tests for checkpoint_phase.py
    test_build_coord_prompt.py    # unit tests for --resume mode
    test_smoke.py                 # e2e against stubbed local API
requirements-dev.txt              # new: pytest, pytest-httpx, httpx
```

### `requirements-dev.txt` (new file)

```
pytest>=8.0
pytest-httpx>=0.30
httpx>=0.27
pytest-httpserver>=1.0
```

### `conftest.py`

```python
import pytest

FAKE_TASK_ID = "aaaaaaaa-0000-0000-0000-000000000000"
FAKE_WORKFLOW_VERSION_ID = "bbbbbbbb-0000-0000-0000-000000000000"

@pytest.fixture
def base_task():
    return {
        "id": FAKE_TASK_ID,
        "title": "Test task",
        "description": "desc",
        "acceptance_criteria": None,
        "attrs": {
            "workflow": "six-phase-build",
            "workflow_version_id": FAKE_WORKFLOW_VERSION_ID,
        },
    }

@pytest.fixture
def task_with_checkpoint(base_task):
    t = dict(base_task)
    t["attrs"] = dict(base_task["attrs"])
    t["attrs"]["checkpoint"] = {
        "workflow": "six-phase-build",
        "workflow_version": FAKE_WORKFLOW_VERSION_ID,
        "phases_completed": ["DESIGN"],
        "current_phase": "BUILD",
        "updated_at": "2026-04-22T10:00:00Z",
        "by_coord": "coord-aaaaaaaa",
    }
    return t
```

### `test_checkpoint_phase.py` (unit, mocked HTTP via `pytest-httpx`)

| Test name | Scenario | Assert |
|---|---|---|
| `test_first_call_creates_checkpoint` | No `attrs.checkpoint`; call `"DESIGN"` | PATCH body has `checkpoint.phases_completed == []`, `current_phase == "DESIGN"` |
| `test_advances_phase` | `checkpoint={current_phase:"DESIGN", phases_completed:[]}` → call `"BUILD"` | PATCH: `phases_completed == ["DESIGN"]`, `current_phase == "BUILD"` |
| `test_idempotent_same_phase` | `checkpoint.current_phase == "DESIGN"` → call `"DESIGN"` | No PATCH issued; return 0 |
| `test_no_duplicate_in_phases_completed` | `phases_completed=["DESIGN"]`, `current_phase="BUILD"` → call `"INTEGRATE"` | PATCH: `phases_completed == ["DESIGN", "BUILD"]` (no dup) |
| `test_patch_failure_exits_1` | PATCH returns 500 | `run()` returns 1 |
| `test_missing_api_key_exits_3` | `TASKFORGE_API_KEY` unset | returns 3; no HTTP call |
| `test_task_not_found_exits_2` | GET returns 404 | returns 2 |
| `test_preserves_extension_keys` | `checkpoint` has `migration_revision_id: "rev123"` → call `"INTEGRATE"` | PATCH checkpoint still contains `migration_revision_id: "rev123"` |
| `test_updated_at_is_utc_iso8601` | Normal call | `checkpoint.updated_at` matches `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z` |

### `test_build_coord_prompt.py` (unit, no network)

Feed the `build()` function directly with a pre-built task dict and a workflow body string read from `.orchestration/workflows/six-phase-build.md`.

| Test name | Scenario | Assert |
|---|---|---|
| `test_resume_injects_part1_block` | Task with valid checkpoint; `resume=True` | Part 1 contains `"RESUMING WORKFLOW"` and `"BUILD"` (current_phase) |
| `test_resume_phases_listed_in_part1` | Valid checkpoint with `phases_completed=["DESIGN"]` | Part 1 contains `"DESIGN"` in phases completed |
| `test_resume_missing_checkpoint_exits_1` | No `attrs.checkpoint`; `resume=True` | `SystemExit(1)` raised; message contains `"has none"` |
| `test_resume_empty_current_phase_exits_1` | `checkpoint.current_phase == ""` | `SystemExit(1)`; message contains `"current_phase is empty"` |
| `test_resume_workflow_mismatch_exits_1` | `checkpoint.workflow="lightweight"`, `--workflow six-phase-build` | `SystemExit(1)`; message contains both workflow slugs |
| `test_resume_banner_at_top_of_part2` | Valid checkpoint | Part 2 string starts with `">>> RESUME POINT <<<"` |
| `test_resume_full_body_retained` | Valid checkpoint | Part 2 still contains heading for each phase (not truncated) |
| `test_normal_mode_unaffected` | `resume=False` | Output contains no `"RESUMING"` or `"RESUME POINT"` text |

### `test_smoke.py` (e2e against `pytest-httpserver`)

```python
def test_checkpoint_advance_and_resume(httpserver, base_task, task_with_checkpoint, tmp_path):
    # Phase 1: first checkpoint call
    httpserver.expect_request(f"/tasks/{FAKE_TASK_ID}", method="GET").respond_with_json(base_task)
    httpserver.expect_request(f"/tasks/{FAKE_TASK_ID}", method="PATCH").respond_with_json(base_task)
    result = run_checkpoint(FAKE_TASK_ID, "DESIGN", base_url=httpserver.url_for(""))
    assert result == 0

    # Phase 2: advance to BUILD
    httpserver.clear()
    mid_task = build_task_with_checkpoint(["DESIGN"], "DESIGN")  # task after phase 1 checkpoint
    httpserver.expect_request(...GET...).respond_with_json(mid_task)
    captured_patch = capture_patch_body(httpserver)
    result = run_checkpoint(FAKE_TASK_ID, "BUILD", base_url=httpserver.url_for(""))
    assert result == 0
    assert captured_patch["attrs"]["checkpoint"]["phases_completed"] == ["DESIGN"]
    assert captured_patch["attrs"]["checkpoint"]["current_phase"] == "BUILD"

    # Phase 3: --resume builds correct prompt
    prompt = build_prompt(task_with_checkpoint, "six-phase-build", resume=True, ...)
    assert "RESUMING WORKFLOW" in prompt
    assert "BUILD" in prompt  # current_phase

def test_resume_missing_checkpoint_error(httpserver, base_task):
    # task with no checkpoint + --resume -> exit 1
    with pytest.raises(SystemExit) as exc:
        build_prompt(base_task, "six-phase-build", resume=True, ...)
    assert exc.value.code == 1
```

---

## 6. Open Questions

**OQ-1 — PATCH attrs semantics (MUST resolve before implementing):**  
Does `PATCH /tasks/{id}` with `{"attrs": {...}}` replace the entire `attrs` JSONB object, or does it deep-merge individual keys? Current design sends the full attrs blob (safe for either). If the API merges, the helper can send only `{"attrs": {"checkpoint": ...}}` (simpler, smaller). Probe before implementing:
```bash
curl -s -X PATCH "$TASKFORGE_BASE_URL/tasks/<test-id>" \
  -H "X-API-Key: $TASKFORGE_API_KEY" -H "Content-Type: application/json" \
  -d '{"attrs": {"_probe_key": "hello"}}' | jq .attrs
```
If `_probe_key` appears alongside existing keys → merge semantics (send only checkpoint). If existing keys are gone → replace semantics (send full attrs). Remove `_probe_key` afterward.

**OQ-2 — `checkpoint.workflow` when best-fit selected:**  
If `attrs.workflow` is unset (orchestrator used best-fit scoring), the helper will store `""` for `checkpoint.workflow`. This breaks `--resume` workflow-mismatch validation (empty string will never match `--workflow six-phase-build`). Options: (a) coordinator passes the resolved slug as a third arg `--workflow-slug` to the helper; (b) orchestrator always writes the resolved slug into `attrs.workflow` at claim time. Option (b) is cleaner — the python-expert should confirm with the orchestrator logic in `periodic-workflow.md` §4c.

**OQ-3 — Phase name verbatim vs normalized:**  
`lightweight.md` frontmatter has `"BUILD (inline or single specialist)"` as a phase name. Stored verbatim in checkpoint, this creates a long key that's awkward to type and match. The python-expert should decide: (a) store verbatim (no change to frontmatter); (b) normalize by stripping parenthetical content in the helper (`re.sub(r'\s*\(.*?\)', '', phase).strip()`); or (c) simplify the phase names in `lightweight.md` frontmatter to `BUILD`, `COMMIT`. Option (c) is cleanest. Same concern applies to `infra-change.md` phase names.

**OQ-4 — `part0()` signature change:**  
`part0()` currently takes no args. Adding `task_id: str` is a one-line change in `build()`. Confirm there are no callers outside `build()` before changing the signature (grep for `part0(`).

**OQ-5 — `requirements-dev.txt` vs `pyproject.toml`:**  
This repo has neither. A `requirements-dev.txt` is the minimal path. If the python-expert introduces `pyproject.toml`, use `[project.optional-dependencies] dev = [...]`. Do not add runtime dependencies to a dev file (the scripts use only stdlib + `httpx`; `httpx` may already be available in the environment via the agile_tracker venv — verify before adding).
