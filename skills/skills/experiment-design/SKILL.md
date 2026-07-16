---
name: experiment-design
description: Four-stage experiment loop for survey papers that include original analysis — Design (hypothesis + variables + pre-registered statistical plan, no HARKing) → Execute (API lightweight or GPU heavyweight) → Iterate (ceiling/floor/non-significance/surprise responses) → Report (structured results.json). Use only when the survey makes an empirical claim; a pure literature synthesis doesn't need it.
---

# Experiment Design

Not every survey needs experiments — a pure literature synthesis doesn't. But when the survey makes an empirical claim ("method family A outperforms B on long-horizon tasks"), a small, well-designed experiment turns a hand-wave into evidence. This skill is the loop for that. The orchestration of *whether* to run it lives in `paper-writing` (Phase 2).

**Rule of thumb:** if the survey's contribution is "we organized the literature," skip this skill. If it's "we organized the literature *and* showed X," use it.

## Stage 1 — Design (the most important stage)

Most bad experiments are bad before any code runs. Before executing, answer on paper:

- **Which paper claim does this support?** If you can't point to the sentence in the draft this experiment backs, don't run it.
- **Experiment spec**: hypothesis (falsifiable), independent variable, dependent variable, control variables, expected result (direction + rough magnitude).
- **Statistical plan decided BEFORE running.** No HARKing (Hypothesizing After Results are Known). Decide number of trials, significance test, and what counts as "significant" before you see a single number.
- **Principles**: falsifiable (it must be possible to disprove), minimal first (simplest config that tests the hypothesis), pre-registered (write the plan down, don't drift), has a control.

If the hypothesis is "A is better than B," the experiment must be capable of showing A is *not* better than B. An experiment that can only confirm your belief isn't an experiment — it's a demo.

## Stage 2 — Execute

Two scales:

| Path | Scale | Use for |
|------|-------|---------|
| **A: API** | hours, lightweight | multi-model comparison, prompt ablation, small benchmarks |
| **B: GPU / cluster** | days, heavyweight | agent training, reward shaping, large-scale eval |

- **API config**: 3–5 frontier models × 2–3 conditions × 15–25 tasks × 3 trials. This grid is large enough for a meaningful comparison, small enough to finish in an afternoon.
- **GPU**: submit as a cluster job, then start a minute-level polling loop — auto-diagnose errors, fix, resubmit. Don't submit and walk away; GPU jobs fail in ways that waste hours if unwatched.

## Stage 3 — Iterate

Results rarely come out clean on the first try. Recognize the pattern and respond:

| Observation | Response |
|-------------|----------|
| **Ceiling effect** (everything maxes out) | increase task difficulty |
| **Floor effect** (everything zeros out) | decrease difficulty, or check for bugs |
| **Not significant** | increase trials, or the hypothesis may be wrong — say so |
| **Surprise finding** | design a follow-up to confirm it's real, not noise |

**Max 5 iterations, then accept the best result.** An experiment that needs 12 rounds of tuning to show an effect is an effect you manufactured, not found. Report the honest result, including nulls — a well-run null is more valuable than a p-hacked positive.

## Stage 4 — Report (data only, interpretation later)

Two outputs:

- **`results.json`** — structured: `{config, results, statistics, findings}`. Raw numbers, machine-readable. This is what `academic-figures` turns into tables/plots.
- **`experiment_summary.md`** — purpose (which claim it supports), results (what happened), limitations (what it doesn't show). 1–2 paragraphs.

Report **data** in the results file and **interpretation** in the paper prose — don't mix them. A table that editorializes ("this proves…") invites a reviewer rebuttal; a table that shows numbers and lets the prose argue is defensible.

## When NOT to use this skill

- The survey is purely a literature synthesis with no original empirical claim.
- You don't have the compute/data to run even the API-scale path — a claimed experiment you can't actually run is worse than no experiment; just analyze the literature.
- The "experiment" would be a single toy example with no controls — that's an illustration, put it in prose, don't dress it up as an experiment.
