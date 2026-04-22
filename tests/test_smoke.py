import json

import pytest

import build_coord_prompt as bcp
import checkpoint_phase

FAKE_TASK_ID = "aaaaaaaa-0000-0000-0000-000000000000"
FAKE_WORKFLOW_VERSION_ID = "bbbbbbbb-0000-0000-0000-000000000000"


def _task_with_ckpt(phases_completed, current_phase):
    return {
        "id": FAKE_TASK_ID,
        "title": "Test task",
        "description": "desc",
        "acceptance_criteria": None,
        "attrs": {
            "workflow": "six-phase-build",
            "workflow_version_id": FAKE_WORKFLOW_VERSION_ID,
            "checkpoint": {
                "workflow": "six-phase-build",
                "workflow_version": FAKE_WORKFLOW_VERSION_ID,
                "phases_completed": phases_completed,
                "current_phase": current_phase,
                "updated_at": "2026-04-22T10:00:00Z",
                "by_coord": "coord-test",
            },
        },
    }


def test_checkpoint_advance_and_resume(httpserver, base_task, task_with_checkpoint, tmp_path, monkeypatch):
    base_url = httpserver.url_for("").rstrip("/")
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", base_url)

    # Phase 1: first checkpoint call (no existing checkpoint)
    httpserver.expect_request(f"/tasks/{FAKE_TASK_ID}", method="GET").respond_with_json(base_task)
    httpserver.expect_request(f"/tasks/{FAKE_TASK_ID}", method="PATCH").respond_with_json(base_task)
    result = checkpoint_phase.run(FAKE_TASK_ID, "DESIGN")
    assert result == 0
    httpserver.clear()

    # Phase 2: advance to BUILD — capture what was PATCHed
    mid_task = _task_with_ckpt([], "DESIGN")
    captured = {}

    def capture_patch(request):
        captured["body"] = json.loads(request.data)
        from werkzeug.wrappers import Response
        return Response(
            json.dumps(mid_task),
            status=200,
            content_type="application/json",
        )

    httpserver.expect_request(f"/tasks/{FAKE_TASK_ID}", method="GET").respond_with_json(mid_task)
    httpserver.expect_request(f"/tasks/{FAKE_TASK_ID}", method="PATCH").respond_with_handler(capture_patch)
    result = checkpoint_phase.run(FAKE_TASK_ID, "BUILD")
    assert result == 0
    assert captured["body"]["attrs"]["checkpoint"]["phases_completed"] == ["DESIGN"]
    assert captured["body"]["attrs"]["checkpoint"]["current_phase"] == "BUILD"
    httpserver.clear()

    # Phase 3: --resume builds correct prompt
    prompt = bcp.build(
        task=task_with_checkpoint,
        workflow_slug="six-phase-build",
        branch="test",
        worktree=str(tmp_path),
        base=base_url,
        orch_id="test-orch-id",
        api_key=None,
        resume=True,
    )
    assert "RESUMING WORKFLOW" in prompt
    assert "BUILD" in prompt


def test_resume_missing_checkpoint_error(base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    with pytest.raises(SystemExit) as exc:
        bcp.build(
            task=base_task,
            workflow_slug="six-phase-build",
            branch="test",
            worktree="/tmp",
            base="http://test-api",
            orch_id="test-orch-id",
            api_key=None,
            resume=True,
        )
    assert exc.value.code == 1
