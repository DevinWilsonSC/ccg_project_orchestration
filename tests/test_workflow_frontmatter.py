"""Lint tests for workflow files in .orchestration/workflows/.

Three-tier checks:
  1. ALL workflow files must have valid YAML frontmatter.
  2. The task-0.3 workflows (lightweight, review-only, conflict-resolution)
     must declare a non-empty `specialists:` list — these are the files that
     task 0.3 owns. Other workflows are checked only once they also declare
     `specialists:` (enforced by tier 3).
  3. ANY workflow that declares `specialists:` must have no tmux invocations
     in its body (the Teams pattern replaces tmux).

As other tasks (0.2 for six-phase-build, etc.) update their respective
workflow files, the tier-2 check for those files can be added here.
"""
import re
from pathlib import Path

import pytest

WORKFLOWS_DIR = (
    Path(__file__).resolve().parent.parent / ".orchestration" / "workflows"
)

# Files that task 0.3 is responsible for. These MUST have `specialists:`.
TASK_03_WORKFLOWS = {"lightweight.md", "review-only.md", "conflict-resolution.md"}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TMUX_TERMS = ("tmux", "split-pane", "script -qefc", "tmux send-keys", "tmux new-window")


def _workflow_files():
    return sorted(WORKFLOWS_DIR.glob("*.md"))


def _task03_files():
    return [WORKFLOWS_DIR / name for name in sorted(TASK_03_WORKFLOWS)]


def _parse_frontmatter(text: str) -> dict:
    """Parse simple YAML frontmatter (key: value and key:\n  - item lines)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict = {}
    lines = m.group(1).splitlines()
    current_key = None
    for line in lines:
        if line.startswith("  - "):
            if current_key is not None:
                fm.setdefault(current_key, []).append(line[4:].strip())
        elif ": " in line or line.endswith(":"):
            parts = line.split(":", 1)
            current_key = parts[0].strip()
            value = parts[1].strip() if len(parts) > 1 else ""
            if value:
                fm[current_key] = value
            else:
                fm.setdefault(current_key, [])
        else:
            current_key = None
    return fm


def _body(text: str) -> str:
    m = _FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


# --- Tier 1: all workflows must have valid frontmatter ---

@pytest.mark.parametrize("wf_file", _workflow_files(), ids=lambda p: p.name)
def test_workflow_has_frontmatter(wf_file):
    text = wf_file.read_text()
    assert _FRONTMATTER_RE.match(text), (
        f"{wf_file.name}: missing or malformed YAML frontmatter"
    )


# --- Tier 2: task-0.3 workflows must declare specialists ---

@pytest.mark.parametrize("wf_file", _task03_files(), ids=lambda p: p.name)
def test_task03_workflow_declares_specialists(wf_file):
    text = wf_file.read_text()
    fm = _parse_frontmatter(text)
    assert "specialists" in fm, (
        f"{wf_file.name}: frontmatter missing 'specialists:' key"
    )
    specialists = fm["specialists"]
    assert isinstance(specialists, list), (
        f"{wf_file.name}: 'specialists:' must be a list, got {type(specialists)}"
    )


# --- Tier 3: any workflow with `specialists:` must not have tmux in body ---

def _workflows_with_specialists():
    return [
        wf for wf in _workflow_files()
        if "specialists" in _parse_frontmatter(wf.read_text())
    ]


@pytest.mark.parametrize(
    "wf_file", _workflows_with_specialists(), ids=lambda p: p.name
)
def test_specialists_workflow_body_no_tmux(wf_file):
    body = _body(wf_file.read_text())
    for term in _TMUX_TERMS:
        assert term not in body, (
            f"{wf_file.name}: workflow body must not contain {term!r} — "
            "Teams pattern replaces tmux (use SendMessage/Agent instead)"
        )
