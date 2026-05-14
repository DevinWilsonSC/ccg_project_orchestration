---
description: Stop the periodic orchestrator loop (no more ScheduleWakeup ticks)
argument-hint: (no args)
---

You are (or were) running the taskforge periodic orchestrator. Stop the
loop:

1. Do **not** call `ScheduleWakeup` on this turn or any future turn in
   this session. The loop ends here.
2. If a wakeup is already pending for a later time, acknowledge that
   the next fire will still happen — there is no way to cancel an
   already-scheduled dynamic wakeup from this side. On that fire, detect
   the stop flag (below) and exit immediately without doing a tick.
3. Set an in-context flag: `ORCHESTRATOR_STOPPED = true`. Whenever the
   `<<autonomous-loop-dynamic>>` sentinel next fires, check this flag
   first — if set, reply with `"🛑 Orchestrator stopped."` via PushNotification
   (if the plugin is available) and do nothing else.
4. If there is currently a task mid-tick (claimed but not released),
   heartbeat it once more and leave the lease to expire naturally — do
   NOT force-release, because the child may still be working. The
   server-side `sweep_expired_leases` job will reclaim it after the
   lease window.
5. Notify: `"🛑 Orchestrator loop stopped. In-flight task (if any)
   left to finish or expire. Run /orch-start to resume."`

Do not run another tick. Do not reschedule.
