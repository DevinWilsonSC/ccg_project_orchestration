import pytest
import build_coord_prompt as bcp

FAKE_TASK_ID = "aaaaaaaa-0000-0000-0000-000000000000"
FAKE_WORKFLOW_VERSION_ID = "bbbbbbbb-0000-0000-0000-000000000000"


def _build(task, resume=False):
    return bcp.build(
        task=task,
        workflow_slug="six-phase-build",
        branch="test-branch",
        worktree="/tmp/test",
        base="http://test-api",
        orch_id="test-orch-id",
        api_key=None,
        resume=resume,
    )


def test_resume_injects_part1_block(task_with_checkpoint):
    prompt = _build(task_with_checkpoint, resume=True)
    assert "RESUMING WORKFLOW" in prompt
    assert "BUILD" in prompt


def test_resume_phases_listed_in_part1(task_with_checkpoint):
    prompt = _build(task_with_checkpoint, resume=True)
    assert "DESIGN" in prompt


def test_resume_missing_checkpoint_exits_1(base_task, capsys):
    with pytest.raises(SystemExit) as exc:
        _build(base_task, resume=True)
    assert exc.value.code == 1
    assert "has none" in capsys.readouterr().err


def test_resume_empty_current_phase_exits_1(base_task, capsys):
    base_task["attrs"]["checkpoint"] = {
        "workflow": "six-phase-build",
        "current_phase": "",
        "phases_completed": [],
    }
    with pytest.raises(SystemExit) as exc:
        _build(base_task, resume=True)
    assert exc.value.code == 1
    assert "current_phase is empty" in capsys.readouterr().err


def test_resume_workflow_mismatch_exits_1(base_task, capsys):
    base_task["attrs"]["checkpoint"] = {
        "workflow": "lightweight",
        "current_phase": "BUILD",
        "phases_completed": [],
    }
    with pytest.raises(SystemExit) as exc:
        _build(base_task, resume=True)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "lightweight" in err
    assert "six-phase-build" in err


def test_resume_banner_at_top_of_part2(task_with_checkpoint):
    prompt = _build(task_with_checkpoint, resume=True)
    marker = "--- WORKFLOW INSTRUCTIONS ---\n"
    wf_start = prompt.find(marker) + len(marker)
    wf_content = prompt[wf_start:]
    assert wf_content.startswith(">>> RESUME POINT <<<")


def test_resume_full_body_retained(task_with_checkpoint):
    prompt = _build(task_with_checkpoint, resume=True)
    assert "DESIGN" in prompt
    assert "INTEGRATE" in prompt


def test_normal_mode_unaffected(base_task):
    prompt = _build(base_task, resume=False)
    assert "RESUMING" not in prompt
    assert "RESUME POINT" not in prompt
