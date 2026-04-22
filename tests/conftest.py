import importlib.util
import sys
from pathlib import Path

import pytest

FAKE_TASK_ID = "aaaaaaaa-0000-0000-0000-000000000000"
FAKE_WORKFLOW_VERSION_ID = "bbbbbbbb-0000-0000-0000-000000000000"

_scripts = Path(__file__).resolve().parent.parent / "scripts"

if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "build_coord_prompt", _scripts / "build-coord-prompt.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules["build_coord_prompt"] = _mod


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
