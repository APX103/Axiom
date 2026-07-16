"""Tests for the deli-autoresearch skill kernel helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_KERNEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "skills"
    / "deli-autoresearch"
    / "kernel.py"
)

_spec = importlib.util.spec_from_file_location("deli_autoresearch_kernel", _KERNEL_PATH)
_kernel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_kernel)

init_task = _kernel.init_task
load_progress = _kernel.load_progress
save_progress = _kernel.save_progress
load_directions = _kernel.load_directions
record_direction = _kernel.record_direction
pick_direction = _kernel.pick_direction
load_findings = _kernel.load_findings
append_finding = _kernel.append_finding
check_stall = _kernel.check_stall
heartbeat_age_minutes = _kernel.heartbeat_age_minutes
update_last_seen = _kernel.update_last_seen
log_iteration = _kernel.log_iteration
log_event = _kernel.log_event


@pytest.fixture
def task(tmp_path):
    t = tmp_path / "task"
    init_task(t, "# Goal\nDo something hard.\n")
    return t


def test_init_task_creates_state_and_logs(task):
    assert (task / "state" / "task_spec.md").exists()
    assert (task / "state" / "progress.json").exists()
    assert (task / "state" / "directions_tried.json").exists()
    assert (task / "state" / "findings.jsonl").exists()
    assert (task / "state" / ".last_seen").exists()
    assert (task / "logs" / "orchestrator.jsonl").exists()

    progress = load_progress(task)
    assert progress["iteration"] == 0
    assert progress["status"] == "running"


def test_progress_roundtrip(task):
    progress = load_progress(task)
    progress["iteration"] = 3
    save_progress(task, progress)
    assert load_progress(task)["iteration"] == 3


def test_directions(task):
    record_direction(task, "foo")
    record_direction(task, "bar")
    record_direction(task, "foo")  # idempotent
    assert load_directions(task) == ["foo", "bar"]


def test_pick_direction(task):
    tried = ["a"]
    assert pick_direction(["a", "b", "c"], tried) == "b"
    assert pick_direction(["a"], tried) == "a [PERTURB: flip hypothesis / cross-domain analogy]"
    assert pick_direction([], tried) is None


def test_findings(task):
    append_finding(task, {"claim": "x", "evidence": "y"})
    append_finding(task, {"claim": "z", "evidence": "w"})
    findings = load_findings(task)
    assert len(findings) == 2
    assert findings[0]["claim"] == "x"


def test_check_stall_continue():
    progress = {"stale_count": 0, "total_findings": 5}
    result = check_stall(progress, new_findings=5)
    assert result["verdict"] == "continue"
    assert result["stale_count"] == 0


def test_check_stall_pivot():
    progress = {"stale_count": 1, "total_findings": 5}
    result = check_stall(progress, new_findings=0)
    assert result["verdict"] == "pivot"
    assert result["stale_count"] == 2


def test_check_stall_stuck():
    progress = {"stale_count": 3, "total_findings": 5}
    result = check_stall(progress, new_findings=0)
    assert result["verdict"] == "stuck"
    assert result["stale_count"] == 4


def test_heartbeat_age_and_update(task):
    # Just after init the heartbeat should be recent.
    assert heartbeat_age_minutes(task) < 1.0

    update_last_seen(task)
    assert heartbeat_age_minutes(task) < 1.0


def test_log_iteration(task):
    log_iteration(task, {"iteration": 1, "result": "ok"})
    lines = (task / "state" / "iteration_log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["result"] == "ok"


def test_log_event(task):
    log_event(task, "orchestrator", "decision", "picked_direction", "foo")
    lines = (task / "logs" / "orchestrator.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["source"] == "orchestrator"
    assert entry["event"] == "picked_direction"
