#!/usr/bin/env python3
"""Checkpoint helper — records phase transitions in taskforge task attrs.

Usage:
  python3 scripts/checkpoint_phase.py <task-id> <phase-being-entered>

Exit codes:
  0  success (or idempotent no-op)
  1  API error (non-2xx or network failure)
  2  task not found (404)
  3  TASKFORGE_API_KEY not set

Environment:
  TASKFORGE_API_KEY      required
  TASKFORGE_BASE_URL     default: http://taskforge-prod:8000
  COORD_WINDOW           tmux window name stored in checkpoint.by_coord
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

import httpx


def _get_by_coord() -> str:
    coord_window = os.environ.get("COORD_WINDOW")
    if coord_window:
        return coord_window
    if os.environ.get("TMUX"):
        try:
            return subprocess.check_output(
                ["tmux", "display-message", "-p", "#W"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            pass
    return "unknown"


def run(task_id: str, phase: str) -> int:
    api_key = os.environ.get("TASKFORGE_API_KEY")
    if not api_key:
        print("ERROR: TASKFORGE_API_KEY not set", file=sys.stderr)
        return 3

    base = os.environ.get("TASKFORGE_BASE_URL", "http://taskforge-prod:8000").rstrip("/")

    try:
        resp = httpx.get(f"{base}/tasks/{task_id}", headers={"X-API-Key": api_key})
    except httpx.RequestError as exc:
        print(f"ERROR: network failure: {exc}", file=sys.stderr)
        return 1

    if resp.status_code == 404:
        print(f"ERROR: task {task_id} not found", file=sys.stderr)
        return 2

    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        print(f"ERROR: GET /tasks/{task_id} returned {resp.status_code}", file=sys.stderr)
        return 1

    task = resp.json()
    attrs = dict(task.get("attrs") or {})
    ckpt = dict(attrs.get("checkpoint") or {})

    prev_phase = ckpt.get("current_phase")
    phases_completed = list(ckpt.get("phases_completed") or [])

    if prev_phase == phase:
        print(f"checkpoint: already on phase {phase!r}, no-op", flush=True)
        return 0

    if prev_phase and prev_phase not in phases_completed:
        phases_completed.append(prev_phase)

    new_ckpt = {
        **ckpt,
        "workflow": ckpt.get("workflow") or attrs.get("workflow") or "",
        "workflow_version": (
            ckpt.get("workflow_version") or attrs.get("workflow_version_id") or ""
        ),
        "phases_completed": phases_completed,
        "current_phase": phase,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by_coord": _get_by_coord(),
    }

    attrs["checkpoint"] = new_ckpt

    try:
        resp = httpx.patch(
            f"{base}/tasks/{task_id}",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"attrs": attrs},
        )
        resp.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        print(f"ERROR: PATCH /tasks/{task_id} failed: {exc}", file=sys.stderr)
        return 1

    print(f"checkpoint: {prev_phase or '(none)'!r} -> {phase!r}", flush=True)
    return 0


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h"):
        print(f"Usage: {sys.argv[0]} <task-id> <phase>")
        return 0
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <task-id> <phase>", file=sys.stderr)
        return 1
    return run(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    sys.exit(main())
