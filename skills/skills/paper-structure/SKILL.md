---
name: paper-structure
description: Survey-paper architecture and prose logic — chapter skeleton (intro→background→core→benchmarks→future→conclusion), four paragraph-logic patterns (claim-evidence-implication, compare-contrast, concession-rebuttal, funnel), MECE taxonomy design, and a calibrated hedge ladder for formal claims. Makes a survey read as an argued synthesis, not an annotated bibliography.
---

# Paper Structure & Logic

This skill is about the *skeleton and the prose logic* of a survey — how chapters are ordered, how paragraphs argue, how a taxonomy is built, and how strongly a claim may be worded given its evidence. It assumes the literature is already graded (`lit-survey`) and produces `sections/*.tex` (the full manuscript body). The orchestration of when to apply it lives in `paper-writing`.

## Chapter architecture (survey standard)

A survey that reviews papers one-by-one is a reading list. A survey that argues a thesis across themed chapters is a survey. Use this skeleton; rename core chapters to fit the topic, but keep the arc.

- **§1 Introduction** — Hook (why this matters now) → Gap (what's missing or unresolved) → Contributions (what this survey adds: a new taxonomy, a cross-cutting analysis, a benchmark) → Roadmap (one line per section).
- **§2 Background** — formal definitions of the key terms the rest of the paper uses; a one-paragraph overview of the taxonomy so §3+ can reference it. Don't teach everything — only what's needed to read the core.
- **§3–§6 Core** — one method family or theme per chapter. Each chapter: define the family, walk the key methods, give a **critical assessment** (not just description). 3–6 core chapters is typical.
- **§7 Benchmarks & Experiments** — (if applicable; see `experiment-design`) how methods are evaluated, common datasets, what the results collectively show.
- **§8 Future directions** — specific open problems. Each gap framed as Barrier + Attack vector: what blocks progress, and what approach might break through. "More research is needed" is not a direction.
- **§9 Conclusion** — numbered key findings (3–5), each a substantive takeaway. **Not** a repeat of the abstract.

## Paragraph logic patterns

A review paragraph earns its place by opening on *your* synthetic claim and spending citations to back it, not by opening on a citation and reporting what it found. Four patterns cover almost every paragraph in a survey:

| Pattern | Structure | Use it for |
|---------|-----------|------------|
| **Claim-Evidence-Implication** | Assert → Data → "so what" | Main body — your argument |
| **Compare-Contrast** | A → B → Difference → Trade-off | Method comparison |
| **Concession-Rebuttal** | Admit a strength → "but" limitation | Critical analysis |
| **Funnel** | Broad context → Narrow → "This paper" | Introduction paragraphs |

The diagnostic: **read only the first sentence of each paragraph in sequence.** If they form *your argument*, you've written a synthesis. If they form a list of author names, you've written an annotated bibliography in paragraph costume. Rewrite those.

Compare-contrast is the most underused and the most valuable for a survey — it's where "these three methods agree on the effect but disagree on the mechanism" lives. Reserve it for genuine comparisons, not every adjacent pair of citations.

## Taxonomy design

The taxonomy is the spine of a survey; reviewers judge it hardest.

- **Multi-axis matrix**, not a flat list. Two or three orthogonal axes (e.g. *how* steps are obtained × *what* structure they assume) make a grid; each method is a cell.
- **MECE** — mutually exclusive, collectively exhaustive. Every method fits exactly one cell; together the cells cover the field. If a method spans two cells, your axes aren't orthogonal — fix the axes.
- **Empty cells are features, not bugs.** An empty cell is a gap in the literature — call it out in §8. A taxonomy with no empty cells probably isn't distinguishing anything.
- **Spanning methods show tension (good).** A method that legitimately sits across cells reveals that the axes capture a real trade-off; discuss it rather than forcing the method into one box.

## Formal claims and the hedge ladder

Surveys make claims about the state of a field. Match claim strength to evidence strength — an overstrong claim with weak evidence is what reviewers flag as "not substantiated."

Default to **Conjecture + Remark**, not Theorem (a survey rarely proves novel theorems; it organizes others'). When you do make a formal claim, calibrate the verb:

`demonstrates` > `suggests` > `may` > `hypothesize`

- **demonstrates** — backed by replicated, peer-reviewed experimental evidence.
- **suggests** — consistent evidence from multiple papers, but not fully replicated.
- **may** — plausible mechanism, limited or single-study evidence.
- **hypothesize** — your proposed explanation; no direct evidence yet.

**Rule: claim strength ≤ evidence strength.** If one preprint reports an effect, you may say it "may" hold, not that it "demonstrates" anything. Downgrade freely; reviewers trust a survey that hedges correctly far more than one that overclaims.

## Related-work differentiation

A survey must differentiate itself from existing surveys on the same topic. A comparison table is mandatory, and **"we are more recent" is not differentiation.** You need structural novelty — one of: a new taxonomy (different axes), a new angle (e.g. a resource-allocation view of methods that were previously categorized algorithmically), or new experiments/analysis the prior surveys lack. State the differentiation explicitly in §1, not buried in §2.

## Abstract–conclusion alignment

The abstract promises; the conclusion delivers. After the draft is complete, read them back-to-back: every contribution promised in the abstract must be answered in the conclusion's numbered findings, and vice versa. A conclusion that introduces a finding with no abstract setup, or an abstract that promises something the conclusion never addresses, is a structural bug — fix one or the other.
