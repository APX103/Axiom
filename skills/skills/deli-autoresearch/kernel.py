"""
Deli_AutoResearch state helpers for Axiom.

Load into the python tool namespace with:

    exec(open('{baseDir}/kernel.py').read())

All functions are deterministic, use only stdlib, and read/write files under
the task directory.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _state_dir(task_dir: str | Path) -> Path:
    return Path(task_dir) / "state"


def _logs_dir(task_dir: str | Path) -> Path:
    return Path(task_dir) / "logs"


def _ensure_dirs(task_dir: str | Path) -> None:
    _state_dir(task_dir).mkdir(parents=True, exist_ok=True)
    _logs_dir(task_dir).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------


def init_task(task_dir: str | Path, spec_text: str) -> None:
    """Create a fresh Deli task directory with empty state files."""
    task_dir = Path(task_dir)
    _ensure_dirs(task_dir)
    (task_dir / "state" / "task_spec.md").write_text(spec_text, encoding="utf-8")

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    progress = {
        "iteration": 0,
        "status": "running",
        "total_findings": 0,
        "stale_count": 0,
        "last_seen": now,
    }
    _save_json(task_dir / "state" / "progress.json", progress)
    _save_json(task_dir / "state" / "directions_tried.json", [])

    for name in ("findings.jsonl", "iteration_log.jsonl"):
        (task_dir / "state" / name).touch(exist_ok=True)
    for name in ("work.jsonl", "orchestrator.jsonl", "heartbeat.jsonl"):
        (task_dir / "logs" / name).touch(exist_ok=True)

    update_last_seen(task_dir)


# ---------------------------------------------------------------------------
# Progress / heartbeat
# ---------------------------------------------------------------------------


def load_progress(task_dir: str | Path) -> dict[str, Any]:
    return _load_json(_state_dir(task_dir) / "progress.json", {})


def save_progress(task_dir: str | Path, progress: dict[str, Any]) -> None:
    _save_json(_state_dir(task_dir) / "progress.json", progress)


def update_last_seen(task_dir: str | Path) -> str:
    """Write the current UTC timestamp to state/.last_seen."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    (_state_dir(task_dir) / ".last_seen").write_text(now, encoding="utf-8")
    return now


def heartbeat_age_minutes(task_dir: str | Path) -> float:
    """Return minutes since the last_seen heartbeat."""
    path = _state_dir(task_dir) / ".last_seen"
    if not path.exists():
        return float("inf")
    try:
        ts = path.read_text(encoding="utf-8").strip()
        last = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(UTC) - last).total_seconds() / 60.0
    except Exception:
        return float("inf")


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def load_findings(task_dir: str | Path) -> list[dict[str, Any]]:
    path = _state_dir(task_dir) / "findings.jsonl"
    if not path.exists():
        return []
    findings = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            findings.append(json.loads(line))
        except Exception:
            continue
    return findings


def append_finding(task_dir: str | Path, finding: dict[str, Any]) -> None:
    """Append one finding as NDJSON."""
    path = _state_dir(task_dir) / "findings.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(finding, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Directions
# ---------------------------------------------------------------------------


def load_directions(task_dir: str | Path) -> list[str]:
    return _load_json(_state_dir(task_dir) / "directions_tried.json", [])


def record_direction(task_dir: str | Path, direction: str) -> None:
    """Add a direction to the tried set (idempotent)."""
    tried = load_directions(task_dir)
    if direction not in tried:
        tried.append(direction)
        _save_json(_state_dir(task_dir) / "directions_tried.json", tried)


def pick_direction(candidates: list[str], tried: list[str]) -> str | None:
    """Pick a candidate direction not already tried.

    If every candidate has been tried, return the first candidate with a
    perturbation marker so the caller knows to change the framing.
    """
    for c in candidates:
        if c not in tried:
            return c
    if candidates:
        return candidates[0] + " [PERTURB: flip hypothesis / cross-domain analogy]"
    return None


def perturb_direction(direction: str) -> str:
    """Return a structurally different framing of the same direction."""
    templates = [
        f"Start from the opposite hypothesis: {direction}",
        f"Find a cross-domain analogy for: {direction}",
        f"Reframe {direction} as a failure-mode analysis",
        f"Reframe {direction} as a boundary-condition study",
    ]
    # Deterministic pick based on length so repeated calls don't cycle.
    return templates[len(direction) % len(templates)]


# ---------------------------------------------------------------------------
# Stall detection
# ---------------------------------------------------------------------------


def check_stall(
    progress: dict[str, Any],
    new_findings: int = 0,
    hours_since_last: float | None = None,
) -> dict[str, Any]:
    """Update stale_count and return a verdict.

    - 0 new findings or a metric drop => stale_count + 1.
    - stale_count >= 2 => force a structural pivot.
    - stale_count >= 4 => structurally stuck, escalate.
    - hours_since_last > 2 => also counts as a stall.
    """
    stale_count = int(progress.get("stale_count", 0))
    prev_findings = int(progress.get("total_findings", 0))

    if new_findings == 0:
        stale_count += 1
    elif new_findings < prev_findings:
        # metric drop
        stale_count += 1

    if hours_since_last is not None and hours_since_last > 2:
        stale_count += 1

    progress["stale_count"] = stale_count

    if stale_count >= 4:
        verdict = "stuck"
    elif stale_count >= 2:
        verdict = "pivot"
    else:
        verdict = "continue"

    return {"progress": progress, "verdict": verdict, "stale_count": stale_count}


# ---------------------------------------------------------------------------
# Iteration logging
# ---------------------------------------------------------------------------


def log_iteration(task_dir: str | Path, entry: dict[str, Any]) -> None:
    """Append an iteration summary to state/iteration_log.jsonl."""
    path = _state_dir(task_dir) / "iteration_log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_event(
    task_dir: str | Path,
    source: str,
    level: str,
    event: str,
    detail: str = "",
) -> None:
    """Append a structured event to logs/<source>.jsonl if known, else logs/orchestrator.jsonl."""
    log_name = source if source in ("work", "orchestrator", "heartbeat") else "orchestrator"
    path = _logs_dir(task_dir) / f"{log_name}.jsonl"
    line = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "level": level,
        "event": event,
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Nudge helper
# ---------------------------------------------------------------------------


def nudge_prompt(task_dir: str | Path) -> str:
    """Build a zero-interaction nudge prompt for a delegate subagent."""
    task_dir = Path(task_dir)
    spec = (task_dir / "state" / "task_spec.md").read_text(encoding="utf-8")
    progress = load_progress(task_dir)
    tried = load_directions(task_dir)
    findings = load_findings(task_dir)
    return (
        "You are resuming a Deli_AutoResearch task after a stall or session restart.\n\n"
        "TASK SPEC:\n"
        f"{spec}\n\n"
        "CURRENT PROGRESS:\n"
        f"{json.dumps(progress, ensure_ascii=False, indent=2)}\n\n"
        "DIRECTIONS ALREADY TRIED:\n"
        f"{chr(10).join(f'- {d}' for d in tried) or '(none)'}\n\n"
        "RECENT FINDINGS:\n"
        f"{chr(10).join(json.dumps(f, ensure_ascii=False) for f in findings[-5:]) or '(none)'}\n\n"
        "YOUR JOB:\n"
        "1. Read the task spec and progress.\n"
        "2. Choose a direction that is structurally different from the tried list.\n"
        "3. Produce one verifiable finding or concrete artifact.\n"
        "4. Append the finding to state/findings.jsonl.\n"
        "5. Do NOT ask the user questions; resolve ambiguity yourself and proceed.\n"
        "6. Stay under the file/line caps (5 files, 300 lines/file)."
    )
