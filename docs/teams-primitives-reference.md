# Teams Primitives — Reference (RETIRED)

> **Retired by the `orch-v2-rebaseline`.** The Teams primitives
> (`TeamCreate` / `SendMessage`-as-a-Teams-call / `TeamDelete`) are no longer
> used to launch or fan out delegated units. v2 dispatches natively — typed
> subagents via the `Agent` tool, multi-phase pipelines via the `Workflow`
> tool, native `SendMessage` follow-ups, `ScheduleWakeup`, `PushNotification`,
> and background agents.
>
> **This reference has been replaced by
> [`native-primitives-reference.md`](native-primitives-reference.md)** — read
> that for the primitives actually in use. This stub is kept at the old path so
> inbound links do not 404. Do not add content here.
>
> See also `native-dispatch.md` (delegation spec), `periodic-workflow.md` §5
> (design rationale), and `commands/orch-start.md` §6d (runnable dispatch).
