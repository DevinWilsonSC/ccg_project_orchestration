# Workflow Library — Architectural Overview

Workflow definitions are stored in the **taskforge DB** as
`WorkflowVersion.body_template` (YAML frontmatter + Markdown body).
This directory is an architectural overview only — the authoritative data
lives in the database and is materialized locally by `/sync-workflow pull`.

## Where to find workflow definitions

| Layer | Path | Purpose |
|-------|------|---------|
| **DB source** | taskforge `/workflows` GUI or REST API | Live editing |
| **Seed data** | `alembic/_seed_data_0009.py` | Initial six bodies seeded at migration time |
| **Materialized cache** | `.orchestration/workflows/<slug>.md` | Read by `build-coord-prompt.py` at coordinator launch |
| **Per-project overlay** | `.orchestration/workflows/<slug>.overlay.md` | Project-specific additions (committed) |

## Available workflow slugs (seeded by migration 0009)

| Slug | Use when |
|------|----------|
| `six-phase-build` | Default — new features, refactors, endpoint changes, multi-file app work |
| `lightweight` | Trivial changes: typo fixes, copy edits, obvious single-file tweaks |
| `doc-only` | Documentation updates with no code changes |
| `schema-migration` | Alembic migration with no other application changes |
| `infra-change` | Terraform / AWS infrastructure changes |
| `security-audit` | Vulnerability scans, dep audits — no implementation |

## Materialized cache format

Each `.orchestration/workflows/<slug>.md` file is YAML frontmatter
followed by the `body_template`:

```markdown
---
name: Six-Phase Build
id: six-phase-build
description: ...
best_for:
  - feature
  - new endpoint
chains_with:
  - infra-change
  - schema-migration
phases:
  - DESIGN
  - BUILD
  - INTEGRATE
  - REVIEW
  - RE-VERIFY
  - COMMIT
version_int: 1
version_id: <uuid>
---

<body_template content>
```

## Selection heuristics

The orchestrator uses best-fit scoring (section 6b-workflow in `orch-start.md`):

1. **Materialized cache** — list `.orchestration/workflows/*.md` and score
   each by counting `best_for` substring matches against the task
   title + description. REST-API fallback (`GET /workflows`) if cache is empty.
2. **Chaining** — if the primary workflow's `chains_with` list contains a
   slug that also scored > 0, auto-chain it.
3. **Explicit override** — `task.attrs.workflow` bypasses scoring.
4. **Default** — `six-phase-build` if nothing scores above 0.

## Managing workflows

```bash
# Pull all seeded workflows into materialized cache
for slug in six-phase-build lightweight doc-only schema-migration infra-change security-audit; do
  /sync-workflow pull --slug "$slug"
done

# Check if local cache matches DB
/sync-workflow status --slug six-phase-build

# Propose a body change (creates a draft version for owner to review + publish)
/sync-workflow propose --slug six-phase-build
```

Edit workflow bodies via the Taskforge `/workflows` GUI or REST API,
then re-run `/sync-workflow pull` to refresh the local cache.
