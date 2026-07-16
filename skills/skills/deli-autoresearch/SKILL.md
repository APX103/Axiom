---
name: deli-autoresearch
description: Axiom adaptation of the Deli_AutoResearch protocol for long-horizon autonomous tasks. Uses file-based state, delegate subagents, stall detection, and an emulated heartbeat within a single long-running session.
---

# Deli_AutoResearch (Axiom adaptation)

This skill ports the Deli_AutoResearch long-horizon protocol to Axiom. It does not add durable cron or a true cross-session watchdog (Axiom does not expose those to agents yet). Instead it gives you a convention for running a focused, unattended research loop inside one long session, with clear state files so the loop can be resumed or nudged if the session restarts.

## When to use

- Tasks expected to run for hours to days with minimal user interaction.
- Research or writing tasks that benefit from many directional pivots.
- You can leave Axiom running, or you are OK with restarting from `state/` files if the process stops.

## Behavioral constraints

1. **Zero interaction** — do not ask the user for confirmation inside the loop. Resolve ambiguity yourself and log the reasoning.
2. **Ready means execute** — preparation steps exist to be executed. Submit, resubmit, fix, and restart monitors as routine.
3. **Callback means report-alive** — at the start of every iteration, update `{task}/state/.last_seen` and check liveness. If resuming and the heartbeat is stale, treat it as a stall and nudge.
4. **Persist state to files** — all progress lives in `{task}/state/` and `{task}/logs/`, not in conversation memory. Start each iteration from files.
5. **Guardian / worker separation** — a watchdog/nudge subagent may only check liveness, restart, or nudge. It does not read business findings or modify task state files.

## Setup

1. Create a task directory, e.g. `deli_tasks/my_task/`.
2. Write `deli_tasks/my_task/state/task_spec.md` with:
   - Goal / research question
   - Milestones
   - Success criteria
   - Stop conditions
3. Initialize progress with the helpers in `{baseDir}/kernel.py`:
   ```python
   exec(open('{baseDir}/kernel.py').read())
   init_task(Path('deli_tasks/my_task'), '''# task spec here...''')
   ```
4. Load helpers at the start of every orchestrator callback:
   ```python
   exec(open('{baseDir}/kernel.py').read())
   ```

## State file layout

```
{task}/
├── state/
│   ├── task_spec.md
│   ├── progress.json
│   ├── findings.jsonl
│   ├── directions_tried.json
│   ├── iteration_log.jsonl
│   └── .last_seen
└── logs/
    ├── work.jsonl
    ├── orchestrator.jsonl
    └── heartbeat.jsonl
```

Log line format (append-only NDJSON):

```json
{"ts": "2026-07-16T07:00:00Z", "source": "orchestrator", "level": "info|warn|error|decision", "event": "...", "detail": "..."}
```

## Iteration loop

Each iteration the orchestrator does:

1. Read `task_spec.md`, `progress.json`, `directions_tried.json`, `findings.jsonl`.
2. Update `.last_seen`.
3. `check_stall(progress)` → decide if this is a fresh direction or a forced pivot.
4. `pick_direction(candidates, tried)` → choose a direction not in history; on stall, pick a perturbation.
5. **Delegate** a work subagent with a concrete deliverable:
   ```json
   {
     "task": "Investigate <direction>. Produce one verifiable finding.",
     "output_schema": {"finding": "...", "evidence": "...", "files_changed": []},
     "working_dir": "{workspace}/deli_tasks/my_task",
     "constraints": ["max 5 files", "max 300 lines per file", "append findings to findings.jsonl"]
   }
   ```
6. Validate: compile / test / check / verify citations.
7. Append findings, update `progress.json`, log iteration.
8. Repeat until stop criteria.

Stop criteria:
- Success criteria met.
- `stale_count >= 4` (structurally stuck — stop and report to user).
- Iteration budget exhausted.

## Stall detection & pivoting

| Mechanism | Rule |
|-----------|------|
| Stall | Iteration adds 0 new findings or metric drops → `stale_count += 1` |
| Forced pivot | `stale_count >= 2` → change a structural constraint, not a tactic |
| Stuck | `stale_count >= 4` → stop and escalate to user |
| Direction diversity | New direction must differ from all tried; after stall, perturb |
| Round cap | One delegate session caps at 15 rounds or 30 minutes |

Why pivot structure, not tactics: when a task stalls repeatedly inside one frame, the decisive gain usually comes from fixing the environment or structural constraint, not from tuning parameters harder inside the same frame.

## Heartbeat emulation

Axiom does not provide durable cron. Emulate a heartbeat by writing `.last_seen` every iteration and checking it on resume:

```python
from datetime import datetime, timedelta, timezone
last = datetime.fromisoformat(progress["last_seen"].replace("Z", "+00:00"))
if datetime.now(timezone.utc) - last > timedelta(hours=2):
    # treat as stall, nudge rather than restart from scratch
    stale_count += 1
```

If the session died, the next run reads `.last_seen`, sees it is stale, and continues with a nudge using the current `task_spec` and `progress`.

## Subagent scheduling patterns

| Pattern | Use | How in Axiom |
|---------|-----|--------------|
| Goal-driven research | Standard iteration | `delegate` with explicit deliverable, write to `findings.jsonl` |
| Parallel exploration | Complex sub-problem | Fire multiple `delegate` calls in one batch, then merge |
| Experiment run | Long compute | Submit job, then minute-level polling loop via `delegate` or `bash` |
| Verification | Post-iteration QA | Independent `delegate` audits the evidence chain |

A subagent prompt must include:
- Background (what is known)
- Verifiable deliverable
- Working directory
- File/line caps
- Completion criteria

## Engineering constraints

1. At most 5 large files per iteration; no single file over 300 lines.
2. State is injected via files, not conversation history.
3. Validation (test / compile / check) must run between iterations.
4. Citation-like content is verified every 20 entries, never batched up.
5. With multiple candidate directions, prefer adding diversity over digging one deeper.
6. Unresolvable external-dependency failures escalate: full report + notify user + poll for a reply; never abandon silently.

## Honest limits

- This is a **single-session emulation** of the Deli framework. True unattended multi-day runs would require host-level cron and heartbeat, which Axiom does not yet expose to agents.
- Scores and self-assessments are longitudinal only; they are not an external quality claim.
- The separation of guardian/worker relies on protocol discipline, not model discipline.

## Boot prompt

If the user says "run deli autoresearch on <topic>", start here:

1. Ask (once, up front) for the topic, scope, and stop condition.
2. Create the task directory and `task_spec.md`.
3. Load `{baseDir}/kernel.py`.
4. Enter the iteration loop and do not ask again until success or `stale_count >= 4`.
