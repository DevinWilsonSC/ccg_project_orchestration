# CCG Teams Delegation Spec — SUPERSEDED

> **Superseded by [`native-dispatch.md`](native-dispatch.md)** as of the
> `orch-v2-rebaseline`. This document described the **retired** Teams
> (`TeamCreate` / `SendMessage` / `TeamDelete`) + `claude -p` coordinator model,
> which itself replaced the earlier tmux delegation.
>
> Orchestration v2 dispatches work with **native Claude Code primitives** —
> typed subagents (the `Agent` tool), the `Workflow` tool, and background
> agents. There are no tmux windows, no panes, no `claude -p` children, and no
> Teams teammates. For the current delegation contract see:
>
> - **`native-dispatch.md`** — the delegation spec (roles, dispatch mechanisms,
>   fan-out, structured hand-back, failure modes).
> - **`commands/orch-start.md` §6d** — the runnable dispatch form.
> - **`periodic-workflow.md` §5** — the design rationale.
>
> This stub is kept only so older references (e.g. an unmigrated
> `taskforge/CLAUDE.md` link) still resolve. Do not add new content here; edit
> `native-dispatch.md` instead.

## Historical note (provenance)

The Teams model ran a three-tier hierarchy — orchestrator → coordinator (Teams
teammate launched via `TeamCreate` + `claude -p`) → specialist teammates fanned
out via `SendMessage` — and detected completion via a `/tmp/coord-*.done` file
plus a `RELEASED <status>` stdout marker. All of that is retired. The load-
bearing safety spine it carried (leases, audit events, the DB `WorkflowVersion`
gate, DAG cycle detection, `client_request_id` idempotency, Telegram HITL, and
prompt-injection hygiene) is **unchanged** and preserved in v2; only the
execution mechanism changed. See `docs/designs/orch-v2-rebaseline.md` in the
taskforge repo for the full re-baseline design.
