---
name: peer-review
description: Multi-persona peer-review simulation that drives the survey iteration loop. Five independent reviewer roles (Experimentalist, Theorist, Perfectionist, Synthesizer, Newcomer) score the draft; aggregate functions enforce anti-inflation rules (first-round cap, max delta, mandatory unresolved weakness) and route each weakness back to the sub-skill that fixes it. This is what pushes a survey from 6.0 to 8.5.
---

# Peer Review Simulation

This is the engine of the improvement loop. After a draft compiles, a panel of reviewers scores it independently, the scores are aggregated with anti-inflation rules, and each weakness is routed to the sub-skill responsible for fixing it. The orchestrator (`paper-writing`) uses the output to decide: iterate again, or stop.

`kernel.py` provides `aggregate_reviews`, `apply_anti_inflation`, `route_weaknesses`, `regression_check`, `should_stop` — deterministic, no LLM. **The model plays the reviewers; the kernel enforces the rules.**

## The five reviewer personas

Each review round, role-play 3–5 of these personas. **Each scores independently** — no anchoring on each other's numbers, no discussion before scoring. Write each review as a separate, self-contained assessment.

| Persona | Focus | Scores hardest |
|---------|-------|----------------|
| **R1 Experimentalist** | statistical rigor, baselines, replication | Experimental validation |
| **R2 Theorist** | formal definitions, proofs, MECE taxonomy | Technical depth |
| **R3 Perfectionist** | writing quality, figures, formatting | Clarity |
| **R4 Synthesizer** | cross-cutting analysis, gap identification | Novelty |
| **R5 Newcomer** | accessibility, definitions, examples | Clarity |

The Newcomer (R5) is the most underrated reviewer — they catch "you used a term for three pages before defining it" and "this section assumes I read §4." Always include at least one Newcomer pass.

**Diversity rule:** at least one reviewer per round should be played with a different "mindset" (more critical, or from a different sub-area). Identical reviewers produce correlated scores that inflate the median.

## What each review contains

Each reviewer produces:

```
{
  "reviewer": "R1",
  "scores": {
    "novelty": 7, "comprehensiveness": 8, "clarity": 7,
    "technical_depth": 6, "experimental": 5
  },
  "strengths": ["...", "..."],
  "weaknesses": [
    {"id": "w1", "severity": "major", "text": "citation coverage insufficient in §3"},
    {"id": "w2", "severity": "minor", "text": "Table 2 caption doesn't state finding"}
  ],
  "recommendation": "weak accept"
}
```

Scores are 0–10 per dimension. `aggregate_reviews(reviews)` takes the **median** of reviewers' overall scores and per-dimension medians — median, not mean, so one outlier reviewer can't drag the score.

## Scoring calibration

Anchor your scores so they're comparable across rounds:

| Score | Meaning |
|-------|---------|
| 6.0 | workshop quality |
| 7.0 | main conference quality |
| 8.0 | strong accept (top 20%) |
| 9.0 | oral / best paper |

A 7.0 means "this would get into a real venue." Be honest — the point is to find the gap to 8.5, not to feel good.

## Anti-inflation rules (enforced by the kernel)

These exist because LLM reviewers drift upward over rounds. `apply_anti_inflation(score, round, prev_score, weaknesses)` enforces:

1. **First round capped at 7.0** — every paper has room to improve; a 9.0 on round 1 means the reviewer wasn't critical enough.
2. **Max +1.5 per round** — bigger jumps than that are the reviewer re-reading more charitably, not the paper improving.
3. **At least 1 unresolved weakness** must remain — a paper with zero open weaknesses at 8.0+ is suspicious; cap at 8.4 and flag.

If the kernel caps your score, that's the real score. Don't argue with it — the cap is what makes the loop honest.

## Weakness routing

`route_weaknesses(weaknesses)` maps each weakness to the sub-skill that owns the fix:

| Reviewer says | Route to | Action |
|---------------|----------|--------|
| "citation coverage insufficient" | lit-survey | Stage 1-2 targeted search |
| "too many arXiv-only refs" | lit-survey | Stage 4 venue upgrade |
| "taxonomy not MECE" | paper-structure | redesign axes |
| "paragraphs are summaries, not synthesis" | paper-structure | apply logic patterns |
| "claim overstated" | paper-structure | calibrate hedge ladder |
| "abstract ≠ conclusion" | paper-structure | realign |
| "no supporting experiment" | experiment-design | design one |
| "missing baselines" | experiment-design | add controls |
| "table unclear" | academic-figures | rewrite with finding |
| "figure illegible" | academic-figures | fix fonts/resolution |

This is how the review *drives* revision: each weakness becomes a task for a specific sub-skill, not a vague "improve the paper."

## Regression check

After a revision round, run `regression_check(current_weaknesses, prev_weaknesses)`. It verifies weaknesses marked "resolved" in a prior round haven't crept back. A regression (a fixed weakness reappearing) is worse than a new weakness — it means the fix didn't hold. Flag regressions explicitly.

## When to stop

`should_stop(score, round, prev_score)` returns stop when ANY:

- score ≥ **8.5** (target reached), OR
- improvement ≤ **0.3** for the round (plateaued), OR
- round > **12** (budget exhausted).

If you stop on plateau or budget, accept the current score honestly — don't round 7.8 up to "8.0ish" in the summary. An honest 7.8 with documented limitations beats an inflated 8.5.

## Honest limits

- Scores are simulated, not real peer review. They're comparable **longitudinally** (round-to-round within one paper) but not an external quality claim.
- The anti-inflation rules reduce but don't eliminate LLM reviewer drift. The kernel enforces the *aggregate*; individual reviewer honesty still depends on the model being critical.
- Fabricated citations/data originate in the LLM — peer review catches the symptoms (a citation that looks wrong) but the mechanical verification (`lit-survey` Stage verify) is what removes the source.
