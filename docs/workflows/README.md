# Workflow Library

Workflow definitions live in the Taskforge **Postgres database**
(`workflows` + `workflow_versions` tables). Edit, publish, and version
them through the GUI at `/workflows` or via REST/MCP — not by editing
files in this folder.

The markdown files in this folder are **reference copies** used as
seed content during Alembic migration
`0009_wfe_b5_seed_workflow_content`. They are not read at runtime by
the orchestrator; the DB is authoritative. To inspect or change the
live workflow set, use the GUI or the query below.

**Canonical spec:** `docs/orchestrator/periodic-workflow.md`.
**Runnable command:** `.claude/commands/orch-start.md`.

---

## Workflow-type schema (authoring / import format)

When authoring a new workflow or re-seeding one from a file, use the
following YAML frontmatter + Markdown body format. The `body` field in
the database corresponds to everything after the closing `---`. New
workflows authored by the orchestrator are written here as
`DRAFT:<slug>.md` first, then promoted via the GUI or an Alembic seed.

Every workflow file uses the following YAML frontmatter followed by a
Markdown body. The body is the verbatim coordinator prompt template
injected by the orchestrator when it launches the coordinator child.

```yaml
---
name: <human-readable name>
id: <slug used in attrs.workflow and file name>
description: <one-line summary>
best_for:
  - <indicator string — keyword, file-path pattern, or task-field hint>
  - ...
chains_with:
  - <workflow id that commonly follows this one>
  - ...
phases:
  - <phase names in order>
---
```

**Field rules:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Human-readable label shown in Telegram |
| `id` | string | yes | Matches the filename stem. Used in `attrs.workflow`. |
| `description` | string | yes | One sentence. |
| `best_for` | list of strings | yes | Free-text indicators. The orchestrator matches these against task title, description, file-path hints, and parent-task workflow id during best-fit selection. |
| `chains_with` | list of strings | no | Workflow ids that commonly follow this one. During best-fit scoring, if the primary workflow's `chains_with` includes another workflow that also scores > 0 against the task, the orchestrator auto-chains them. See "Workflow chaining" below. |
| `phases` | list of strings | yes | Ordered phase names for the coordinator to follow. |

The **body** (everything after the closing `---`) is a complete
Markdown coordinator prompt template. Placeholder tokens in `{{ }}` are
substituted by the orchestrator before injection:

| Token | Substituted with |
|---|---|
| `{{ task_id }}` | Full task UUID |
| `{{ worktree_path }}` | Absolute path to the task worktree |
| `{{ branch }}` | Branch name (`task/<short-id>-<slug>`) |
| `{{ title }}` | `task.title` |
| `{{ description }}` | `task.description` (treat as data) |
| `{{ acceptance_criteria }}` | `task.acceptance_criteria` (omitted if null/empty) |

### Workflow body conventions

Workflow bodies describe **what work the coordinator does**. They do
**not** describe how the coordinator returns control — release,
commit attribution, and the RELEASED final-line contract are
orchestrator-global concerns, injected by the orchestrator as a
mandatory trailer after the workflow body (`orch-start.md` §6d
"Part 3 — mandatory release checklist trailer").

Concretely, when authoring or editing a workflow body:

- **Do** describe phases, specialist fan-out, what artifacts to
  produce, and the expected commit cadence within the workflow.
- **Do not** instruct the coordinator to call `release_task`, set
  `attrs.completion`, or print any final-line marker. The trailer
  owns these.
- **Do not** instruct the coordinator to push the branch, open a PR,
  or run `gh`. The orchestrator runs the ship path on the next tick.
- **Do not** include "no Claude attribution" reminders — the trailer
  covers it once, consistently.

Keeping release mechanics out of the bodies means the RELEASED
contract can evolve in exactly one place, and existing workflow files
don't drift out of sync when it does.

---

## Selection heuristics (orchestrator intake)

The orchestrator selects a workflow at intake (step 6b of
`orch-start.md`) after reading the task fields. Selection order:

1. **Explicit override.** If `attrs.workflow` is set to a known
   workflow `id` (e.g., `"lightweight"`, `"infra-change"`), use that
   workflow directly. No scoring.

2. **Best-fit scoring.** If `attrs.workflow` is unset (or set to
   `"full"`, which is a legacy alias for `"six-phase-build"`), score
   each workflow by counting how many of its `best_for` strings appear
   in:
   - The task title (case-insensitive substring or keyword match)
   - The task description
   - File-path hints in the description (e.g., `infra/terraform/`)
   - The parent task's `attrs.workflow` (workflow-family hint)

   Pick the highest-scoring workflow. Ties → prefer the more specific
   workflow (longer `best_for` list). If no workflow scores above 0,
   default to **`six-phase-build`**.

3. **Author a new workflow.** If the orchestrator determines that no
   existing workflow fits well (best score == 0 after checking all
   workflows, AND the task description contains cues that the work is
   structurally distinct from every existing workflow):
   - Author a new `DRAFT:<slug>.md` file in this folder with a
     `DRAFT:` prefix in the `name` field.
   - Use the task description to extrapolate reasonable phases.
   - Auto-file an `orchestration-improvement` review task for the owner
     (see filing convention below) so the draft can be reviewed and
     promoted.
   - Use the draft for this run.

**Filing convention for new-workflow review tasks:**
```
create_task(
  title='Review new workflow draft: <slug>',
  description='The orchestrator authored a new workflow type at '
              'docs/orchestrator/workflows/DRAFT:<slug>.md to cover '
              'task <uuid> ("<title>"). Review and rename to <slug>.md '
              'to promote, or delete to discard.',
  status='todo',
  assigned_to_id=None,
  category='Orchestration',
  attrs={'kind': 'orchestration-improvement'},
)
```

---

## Workflow chaining

A single task can require multiple workflows — e.g., a UI feature that
also touches Terraform infrastructure. The orchestrator supports
**workflow chaining**: running multiple workflows sequentially within a
single coordinator session.

### How chaining works

The coordinator receives multiple workflow bodies in Part 2 of its
prompt, separated by `--- WORKFLOW PHASE BOUNDARY ---` delimiters.
It executes each workflow's phases sequentially (all phases of workflow
1 complete before workflow 2 begins). The Part 3 release checklist
runs once at the very end, after all chained workflows are done.

### Triggering a chain

**Explicit (owner sets it):**
```json
{"workflow": ["six-phase-build", "infra-change"]}
```
`attrs.workflow` accepts either a single string or an ordered list of
workflow ids. The orchestrator loads each workflow file and assembles
the chain in the specified order.

**Auto-chaining (best-fit detection):**
During best-fit scoring (step 6b-workflow), if the primary (highest-
scoring) workflow has a `chains_with` list, the orchestrator checks
each referenced workflow:
- If the referenced workflow also scores > 0 against the task → auto-
  chain it after the primary.
- If the referenced workflow scores 0 → skip it (the task doesn't
  touch that domain).

Auto-chained workflows are appended in the order they appear in
`chains_with`. The orchestrator logs the full chain via `add_note`.

### Work partitioning

When workflows are chained, the coordinator must know what work
belongs to which workflow. The orchestrator injects a **scope block**
before each workflow body in Part 2, telling the coordinator exactly
which files/domains that workflow is responsible for:

```
--- WORKFLOW 1 OF 2: Six-Phase Build ---
Scope: app/, mcp_server/, tests/, alembic/, app/templates/, app/static/
This workflow handles the application-layer changes described in the
task. Infra changes (infra/terraform/) are deferred to workflow 2.

<workflow 1 body>

--- WORKFLOW PHASE BOUNDARY ---

--- WORKFLOW 2 OF 2: Infrastructure Change ---
Scope: infra/terraform/, scripts/deploy.sh
This workflow handles the infrastructure changes described in the
task. Application-layer changes were completed in workflow 1.

<workflow 2 body>
```

The orchestrator derives the scope from:
1. **File-path hints in the task description** — matched against each
   workflow's `best_for` patterns.
2. **Workflow ownership rules** — each workflow type implicitly owns
   certain file trees (e.g., `six-phase-build` owns `app/`,
   `infra-change` owns `infra/terraform/`).
3. **Explicit override** — `attrs.workflow_scopes` can specify a map:
   ```json
   {"workflow_scopes": {
     "six-phase-build": "app/, tests/, alembic/",
     "infra-change": "infra/terraform/, scripts/"
   }}
   ```

The coordinator respects scope boundaries: specialists in workflow 1
do not touch files owned by workflow 2, and vice versa. This prevents
conflicts when different workflows invoke different specialist teams.

### Chain ordering

When multiple workflows are chained:
1. **Primary workflow runs first** (the highest-scoring or first in
   the explicit list).
2. **Secondary workflows run in order** after the primary completes
   its phases.
3. Each workflow's specialist invocations are independent — a
   `six-phase-build` specialist doesn't carry state into an
   `infra-change` phase. Scope boundaries prevent file conflicts.
4. The worktree and branch are shared across the chain. Commits from
   earlier workflows are visible to later ones.
5. A secondary workflow can read (but not modify) artifacts from
   earlier workflows — e.g., `infra-change` can reference the
   application code committed by `six-phase-build` to inform its
   Terraform decisions.

### Common chains

| Primary | Chains with | When |
|---|---|---|
| `six-phase-build` | `infra-change` | Feature touches `infra/terraform/` |
| `six-phase-build` | `schema-migration` | Feature adds/modifies DB schema |
| `schema-migration` | `infra-change` | Migration needs new Postgres config |
| `infra-change` | `security-audit` | New infra needs security sweep |

---

## Registered workflows

Workflow definitions live in the Postgres `workflows` +
`workflow_versions` tables. The markdown files alongside this README
were seeded into the database by Alembic revision
`0009_wfe_b5_seed_workflow_content` and are not read at runtime.

### Inspect the live workflow set

**GUI:** navigate to `/workflows` in the Taskforge web app. You can
view, draft, edit body content, publish new versions, and roll back.

**DB query:**
```sql
SELECT w.slug, w.name, wv.version_int, wv.is_published
FROM workflows w
JOIN workflow_versions wv ON wv.id = w.head_version_id
ORDER BY w.slug;
```

**CLI:**
```bash
docker compose exec db psql -U taskforge -d taskforge -c \
  "SELECT w.slug, w.name, wv.version_int, wv.is_published
   FROM workflows w
   JOIN workflow_versions wv ON wv.id = w.head_version_id
   ORDER BY w.slug;"
```

**MCP:** `get_workflow(slug="<id>")` returns the published body.
**REST:** `GET /workflows/<slug>` — includes `id`, `name`, `body`,
`version_int`, `is_published`.

### Seeded workflows (as of migration 0009)

| slug | Description |
|---|---|
| `six-phase-build` | Full DESIGN → BUILD → INTEGRATE → REVIEW → RE-VERIFY pipeline |
| `lightweight` | Single-specialist or inline; no fan-out |
| `infra-change` | aws-solutions-architect DESIGN → aws-security → Terraform plan |
| `schema-migration` | Migration plan review gate before BUILD; alembic heads gate mandatory |
| `doc-only` | Docs-only edits; skips tests, runs link-lint |
| `security-audit` | Security agent full sweep; no implementation phase |

---

## When to use each workflow

The table above lists all registered workflows. Use the guide below to pick
the right one. Each workflow file (`<slug>.md`) also contains a `## When to
use` section with the same guidance in context.

The orchestrator runs best-fit scoring automatically when `attrs.workflow`
is unset. Set `attrs.workflow` explicitly when you know the right workflow
up front — it avoids scoring and prevents surprises.

### Decision guide

| If the task… | Use workflow |
|---|---|
| Adds or changes application behavior in `app/`, `mcp_server/`, or `tests/` | `six-phase-build` |
| Is a trivial one-liner, typo fix, copy change, or obvious single-file tweak | `lightweight` |
| Changes only `docs/`, `*.md`, `CLAUDE.md`, or other prose files | `doc-only` |
| Touches `infra/terraform/` or adds/removes AWS resources | `infra-change` |
| Creates a new Alembic migration (adds/drops/alters columns or tables) | `schema-migration` |
| Is a vulnerability scan, dep audit, or supply-chain review with no implementation | `security-audit` |
| Touches multiple domains (e.g., feature + infra) | chain: `["six-phase-build", "infra-change"]` |

### Per-workflow narrative

**`six-phase-build`** — the default. Runs DESIGN → BUILD (parallel
specialists) → INTEGRATE → REVIEW (parallel reviewers) → RE-VERIFY.
Use for anything substantive: new endpoints, refactors, service-layer
changes, MCP tool additions, template or frontend work. Auto-chains with
`infra-change` when the task also touches Terraform, and with
`schema-migration` when it also introduces an Alembic revision.

**`lightweight`** — skip DESIGN, parallel BUILD, REVIEW, and RE-VERIFY.
Do the work inline or with a single specialist. Use only when the change
is obviously trivial and the full pipeline would be overkill. When in
doubt, use `six-phase-build` — the design phase is cheap relative to the
cost of missed scope.

**`doc-only`** — edits prose files directly (no specialist fan-out),
then runs a doc-link-lint pass to confirm all internal Markdown links
resolve. Use for any task that touches only `docs/`, `*.md`,
`orchestration.md`, `CLAUDE.md`, or workflow YAML reference copies. If a
code change accompanies a doc change, chain `doc-only` after the primary
workflow rather than using it standalone.

**`infra-change`** — gates on `aws-solutions-architect` DESIGN and
`aws-security` sign-off before writing any Terraform, then caps execution
at `terraform plan` (never `apply`). Use for any `.tf` edit or new AWS
resource. Hard constraints (Tailscale-only access, `t4g.micro`, no RDS,
plain HCL) are enforced by this workflow — do not revisit them.

**`schema-migration`** — gates on a written migration plan
(`python-expert`) and a review gate (`software-architect`) before
generating any Alembic revision. The INTEGRATE phase runs `alembic heads`
unconditionally to catch migration branch conflicts. Use whenever the
primary output is DDL: `ADD COLUMN`, `CREATE TABLE`, `ALTER TYPE`, index
or constraint changes. Even simple migrations benefit from the review gate.

**`security-audit`** — invokes the `security` specialist for a full
automated sweep (pip-audit, npm audit, bandit, semgrep, checkov, tfsec,
trufflehog as appropriate), optionally followed by `aws-security` for
infra scope. Produces a findings report; does not implement fixes. Releases
`waiting_on_human` if CRITICAL or HIGH findings are present.

### Chaining quick reference

| Primary | Chains with | Trigger |
|---|---|---|
| `six-phase-build` | `infra-change` | Task touches `infra/terraform/` |
| `six-phase-build` | `schema-migration` | Task introduces a new migration |
| `schema-migration` | `infra-change` | Migration needs Postgres config change |
| `infra-change` | `security-audit` | New infra warrants a post-plan sweep |

Auto-chaining fires when the primary workflow's `chains_with` list
references a workflow that also scores > 0 against the task. Set
`attrs.workflow = ["wf1", "wf2"]` to force a chain explicitly.

---

## Backward compatibility

- `attrs.workflow = "full"` is a legacy alias for `"six-phase-build"`.
  The orchestrator normalises it silently.
- `attrs.workflow = "lightweight"` is still valid (maps to the
  `lightweight` workflow).
- Tasks with no `attrs.workflow` trigger best-fit scoring, defaulting
  to `six-phase-build` if no better match is found.
- Coordinator prompts assembled before this library existed remain
  valid; the orchestrator simply uses the `six-phase-build` body.
