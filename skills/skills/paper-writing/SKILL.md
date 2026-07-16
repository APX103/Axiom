---
name: paper-writing
description: Orchestrator for autonomous survey-paper production. Coordinates five sub-skills — lit-survey (graded literature), paper-structure (skeleton & logic), experiment-design (original analysis), academic-figures (tables/plots), peer-review (scoring loop) — across four phases (Topic → Draft → Deep Improvement → Sprint) with weakness routing. Produces 8.0+ quality surveys. Load this FIRST; it tells you which sub-skill to call and when.
---

# Paper Writing (orchestrator)

This skill doesn't write prose — it **orchestrates** the five sub-skills that do. Load it first; it tells you which sub-skill to call in each phase, what the quality gates are, and when the paper is done. Each sub-skill is loaded on demand via `skill({skill: <name>})`.

**The five sub-skills:**

| Sub-skill | Job | Loads from |
|-----------|-----|------------|
| `lit-survey` | graded bibliography (LQS scoring, A/B/C/D, venue upgrade) | its own kernel |
| `paper-structure` | chapter skeleton, paragraph logic, taxonomy, hedge ladder | — |
| `experiment-design` | original empirical analysis (only if the survey makes a claim) | — |
| `academic-figures` | tables/plots standards | — |
| `peer-review` | multi-persona scoring that drives the iteration loop | its own kernel |

The division of labor is strict: `lit-survey` owns *which papers*; `paper-structure` owns *how to argue*; `experiment-design` owns *what you ran*; `academic-figures` owns *how it looks*; `peer-review` owns *is it good enough*. If you catch yourself doing literature scoring in the structure phase, you've crossed the streams — route back.

## Phase 0 — Topic selection (before the pipeline starts)

Before touching literature, answer three questions in the chat (briefly, don't over-deliberate):

1. **Scope** — what's in, what's out? ("LLM reasoning methods, 2020–2026, excluding pure architecture papers")
2. **Angle** — what's the structural novelty vs existing surveys? (new taxonomy axis, new cross-cutting analysis, new experiments)
3. **Audience** — who reads this? (ML researchers, practitioners, newcomers — changes depth of §2 Background)

If you can't answer "angle" with something beyond "we're more recent," the survey doesn't have a reason to exist yet. Either find the angle or tell the user honestly.

This is also where you set the `research_question` (if running in plan mode) — it's the anchor peer-review will check convergence against.

## Phase 1 — Draft (iterations 1–6, target: 6.0/10)

Goal: a complete, compiling draft end-to-end. Breadth over depth.

| Iter | Sub-skills | Produces |
|------|-----------|----------|
| 1 | `paper-structure` | skeleton + §1-2 (intro, background) + first compile |
| 2 | `lit-survey` (Stage 1-2) | recall + LQS scoring → initial references.bib |
| 3 | `paper-structure` ‖ `academic-figures` | §3-6 core chapters ‖ 2+ figures/tables |
| 4 | `lit-survey` (Stage 3-4) ‖ `paper-structure` | citation classification + venue upgrade ‖ §7-8 |
| 5 | verify citations → compile → `peer-review` | first score (expect 5.5-6.5) |
| 6 | route fixes → compile | revised draft |

**Compile after every iteration that changes .tex.** A draft that doesn't compile is not a draft — it's a text file. Catch structural breakage early. Call `compile_pdf("main.tex")` to build a PDF (uses the Tectonic engine; auto-runs bibtex + multiple passes, downloads missing packages on first use). A compiled PDF is the final deliverable the user opens.

## Phase 2 — Deep improvement (iterations 7–9, target: 7.5–8.0)

Now add what a draft lacks: original analysis and presentation polish.

| Iter | Sub-skills | Produces |
|------|-----------|----------|
| 7 | `experiment-design` | design + execute supporting experiment (if the survey makes an empirical claim) |
| 8 | `academic-figures` + `paper-structure` | present experiment results + integrate into prose |
| 9 | compile → `peer-review` → route fixes | second score (target 7.5+) |

If the survey is purely a literature synthesis (no original experiment), skip iter 7-8 and go straight to a peer-review round — but expect the Experimentalist (R1) to score low; that's an honest limitation, document it.

## Phase 3 — Sprint (iteration 10+, target: 8.5+)

Tight review-fix loop until convergence.

```
loop:
  peer-review → route_weaknesses → fix via routed sub-skill → compile → peer-review
  stop when: score ≥ 8.5  OR  Δ ≤ 0.3 for the round  OR  iter > 12
```

Use `should_stop(score, round, prev_score)` from peer-review's kernel to decide. When it stops, accept the score honestly in your final summary — an honest 7.8 beats an inflated 8.5.

## Weakness routing (the feedback edge)

When `peer-review` returns weaknesses, `route_weaknesses` stamps each with its target sub-skill. **Don't fix weaknesses ad hoc** — load the routed sub-skill and apply its method:

| Reviewer weakness | Route to | Action |
|-------------------|----------|--------|
| "Citation coverage insufficient" | `lit-survey` | Stage 1-2 targeted search |
| "Too many arXiv-only refs" | `lit-survey` | Stage 4 venue upgrade via DBLP/OpenReview |
| "Missing recent papers" | `lit-survey` | 2025-2026 focused search |
| "Structure unclear" / "Weak transitions" | `paper-structure` | Reorganize + add transitions |
| "Analysis lacks depth" | `paper-structure` | Add critical assessment |
| "Taxonomy not novel / not MECE" | `paper-structure` | Redesign multi-axis taxonomy |
| "Claims too strong" | `paper-structure` | Downgrade hedge ladder |
| "No experiments" | `experiment-design` | Design pilot study |
| "Experiment not rigorous" / "Missing baselines" | `experiment-design` | Add trials / controls / ablation |
| "Tables incomparable" | `academic-figures` | Regroup + add Δ column |
| "Missing visualizations" | `academic-figures` | Add figure |
| "No error bars" / "Unclear figure" | `academic-figures` | Add ±std or rewrite caption |

Each fix is verifiable: a citation gap closes when `bib_health` passes; a taxonomy fix holds when the matrix is MECE; a hedge fix holds when claim-strength ≤ evidence-strength. The next peer-review round confirms.

## Quality gates (don't advance phases prematurely)

### Phase transition gates

- **Phase 0 → 1**: angle is articulated (not just "more recent").
- **Phase 1 → 2**: draft compiles, has §1-8 with at least placeholder citations, first peer-review done.
- **Phase 2 → 3**: experiments (if any) integrated; score ≥ 7.0.
- **Phase 3 → done**: `should_stop` returns True; regression check clean (no fixed weakness regressed).

### Sub-skill output gates (quantitative)

Each sub-skill output must pass its gate before integration. Gates 1–4 can run in parallel; Gate 5 is blocking.

#### Gate 1 — Literature (`lit-survey`)

- Citations ≥ 80 (draft) / ≥ pages×3 (final).
- Within 1yr ≥ 40%.
- Accepted (peer-reviewed) ≥ 30%.
- arXiv-only ≤ 60%.
- Verification rate ≥ 80%; hallucinated = 0.
- Every taxonomy cell ≥ 2 A/B refs.

#### Gate 2 — Experiment (`experiment-design`)

- Clear hypothesis pre-registered.
- Statistical test reported (p or CI).
- ≥ 3 trials with std.
- No ceiling/floor effect.
- Links to a specific paper claim.
- (Bonus) Surprise finding documented.

#### Gate 3 — Structure (`paper-structure`)

- Compiles with 0 errors & 0 undefined refs.
- Every `.tex` file ≤ 300 lines.
- Abstract–conclusion alignment.
- Inter-section transitions present.
- Critical assessment in core sections.
- ≥ 1 formal claim (conjecture/observation).
- Terminology consistent throughout.

#### Gate 4 — Figures & Tables (`academic-figures`)

- Tables ≥ 10, figures ≥ 6 (full survey); tables ≥ 5, figures ≥ 3 (short survey).
- `booktabs` format, no vertical lines.
- Each carries a non-trivial insight.
- Captions contain the conclusion, not just a description.
- Every figure/table referenced in text.
- Experimental data has `mean ± std`.

#### Gate 5 — Final review (blocking)

- All Gates 1–4 passed.
- PDF compiles cleanly.
- Peer-review score ≥ target (6.0 / 7.0 / 8.0 / 8.5 by phase).
- No regression: previously fixed weaknesses remain fixed.
- Version bumped and snapshot saved.

## Score progression (validated path)

| Score | Requirements beyond previous | Typical additions |
|-------|------------------------------|-------------------|
| **6.0** | Complete draft, 80+ refs, compiles | Full 8 sections + basic tables |
| **7.0** | + logical transitions, quantitative data, gap analysis | Formal conjecture + grouped tables |
| **8.0** | + original experiment, critical assessment, 150+ refs | Multi-model pilot + vector figures |
| **8.5** | + cross-validation, meta-analysis, key takeaways, proof sketch | Cross-benchmark table + deeper theory |

## Production statistics (rough time budget)

| Sub-skill | % of time | Score contribution | Key output |
|-----------|-----------|--------------------|------------|
| Literature Survey | 20% | Foundation (without it ≤6.0) | Graded bibliography |
| Structure & Logic | 35% | Main driver (6.0→7.5) | Manuscript body |
| Experiment Design | 20% | +1.0–1.5 points | Results + summary |
| Figures & Tables | 10% | +0.5–1.0 points | Tables/figures |
| Review + Integration | 15% | Drives iteration | Review rounds |

## Engineering constraints

1. **Citations verified every 20**, never batched (see `lit-survey`). A fabricated DOI discovered at iter 10 means re-checking everything after it.
2. **State in files, not memory** — `references.bib`, `citation_plan.jsonl`, `results.json`, section `.tex` files. If the context compacts, the files survive; your recollection of "which papers I scored how" doesn't.
3. **Compile between iterations** — structural errors compound; catch them per-iteration.
4. **One sub-skill at a time per weakness** — don't load three sub-skills and mash their advice together; route each weakness to its owner.

## What this orchestrator does NOT do

- It does not run unattended for days (that's a separate framework concern). It works within an interactive session; the user can intervene between phases.
- It does not guarantee acceptance at a real venue — scores are simulated self-review, comparable only within this protocol.
- It does not replace the convergence mechanism (plan research_question + Verifier drift check). Peer-review checks *academic quality*; the Verifier checks *topical convergence*. They're complementary — use both.
