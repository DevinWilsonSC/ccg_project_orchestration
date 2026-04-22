import json
import os
import re

import pytest

import checkpoint_phase

FAKE_TASK_ID = "aaaaaaaa-0000-0000-0000-000000000000"
BASE_URL = "http://test-api"
GET_URL = f"{BASE_URL}/tasks/{FAKE_TASK_ID}"
PATCH_URL = f"{BASE_URL}/tasks/{FAKE_TASK_ID}"


def test_first_call_creates_checkpoint(httpx_mock, base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    httpx_mock.add_response(method="GET", url=GET_URL, json=base_task)
    httpx_mock.add_response(method="PATCH", url=PATCH_URL, json=base_task)

    result = checkpoint_phase.run(FAKE_TASK_ID, "DESIGN")
    assert result == 0

    requests = httpx_mock.get_requests()
    patch_req = next(r for r in requests if r.method == "PATCH")
    body = json.loads(patch_req.content)
    ckpt = body["attrs"]["checkpoint"]
    assert ckpt["phases_completed"] == []
    assert ckpt["current_phase"] == "DESIGN"


def test_advances_phase(httpx_mock, base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    base_task["attrs"]["checkpoint"] = {
        "current_phase": "DESIGN",
        "phases_completed": [],
    }
    httpx_mock.add_response(method="GET", url=GET_URL, json=base_task)
    httpx_mock.add_response(method="PATCH", url=PATCH_URL, json=base_task)

    result = checkpoint_phase.run(FAKE_TASK_ID, "BUILD")
    assert result == 0

    requests = httpx_mock.get_requests()
    patch_req = next(r for r in requests if r.method == "PATCH")
    body = json.loads(patch_req.content)
    ckpt = body["attrs"]["checkpoint"]
    assert ckpt["phases_completed"] == ["DESIGN"]
    assert ckpt["current_phase"] == "BUILD"


def test_idempotent_same_phase(httpx_mock, base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    base_task["attrs"]["checkpoint"] = {
        "current_phase": "DESIGN",
        "phases_completed": [],
    }
    httpx_mock.add_response(method="GET", url=GET_URL, json=base_task)

    result = checkpoint_phase.run(FAKE_TASK_ID, "DESIGN")
    assert result == 0

    requests = httpx_mock.get_requests()
    assert not any(r.method == "PATCH" for r in requests)


def test_no_duplicate_in_phases_completed(httpx_mock, base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    base_task["attrs"]["checkpoint"] = {
        "current_phase": "BUILD",
        "phases_completed": ["DESIGN"],
    }
    httpx_mock.add_response(method="GET", url=GET_URL, json=base_task)
    httpx_mock.add_response(method="PATCH", url=PATCH_URL, json=base_task)

    result = checkpoint_phase.run(FAKE_TASK_ID, "INTEGRATE")
    assert result == 0

    requests = httpx_mock.get_requests()
    patch_req = next(r for r in requests if r.method == "PATCH")
    body = json.loads(patch_req.content)
    ckpt = body["attrs"]["checkpoint"]
    assert ckpt["phases_completed"] == ["DESIGN", "BUILD"]
    assert ckpt["current_phase"] == "INTEGRATE"


def test_patch_failure_exits_1(httpx_mock, base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    httpx_mock.add_response(method="GET", url=GET_URL, json=base_task)
    httpx_mock.add_response(method="PATCH", url=PATCH_URL, status_code=500)

    result = checkpoint_phase.run(FAKE_TASK_ID, "DESIGN")
    assert result == 1


def test_missing_api_key_exits_3(monkeypatch):
    monkeypatch.delenv("TASKFORGE_API_KEY", raising=False)
    result = checkpoint_phase.run(FAKE_TASK_ID, "DESIGN")
    assert result == 3


def test_task_not_found_exits_2(httpx_mock, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    httpx_mock.add_response(method="GET", url=GET_URL, status_code=404)

    result = checkpoint_phase.run(FAKE_TASK_ID, "DESIGN")
    assert result == 2


def test_preserves_extension_keys(httpx_mock, base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    base_task["attrs"]["checkpoint"] = {
        "current_phase": "BUILD",
        "phases_completed": ["DESIGN"],
        "migration_revision_id": "rev123",
    }
    httpx_mock.add_response(method="GET", url=GET_URL, json=base_task)
    httpx_mock.add_response(method="PATCH", url=PATCH_URL, json=base_task)

    result = checkpoint_phase.run(FAKE_TASK_ID, "INTEGRATE")
    assert result == 0

    requests = httpx_mock.get_requests()
    patch_req = next(r for r in requests if r.method == "PATCH")
    body = json.loads(patch_req.content)
    ckpt = body["attrs"]["checkpoint"]
    assert ckpt["migration_revision_id"] == "rev123"


def test_updated_at_is_utc_iso8601(httpx_mock, base_task, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_KEY", "test-key")
    monkeypatch.setenv("TASKFORGE_BASE_URL", BASE_URL)
    httpx_mock.add_response(method="GET", url=GET_URL, json=base_task)
    httpx_mock.add_response(method="PATCH", url=PATCH_URL, json=base_task)

    result = checkpoint_phase.run(FAKE_TASK_ID, "DESIGN")
    assert result == 0

    requests = httpx_mock.get_requests()
    patch_req = next(r for r in requests if r.method == "PATCH")
    body = json.loads(patch_req.content)
    ckpt = body["attrs"]["checkpoint"]
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ckpt["updated_at"])
