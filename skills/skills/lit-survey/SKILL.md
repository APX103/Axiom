---
name: lit-survey
description: High-precision literature pipeline for survey papers — high-recall retrieval, deterministic LQS multi-dimensional scoring (recency/citation/venue/institution/acceptance), A/B/C/D citation-depth classification, and arXiv→accepted venue upgrade. Produces a graded references.bib with a citation plan. Pair with literature-review (prose) and paper-writing (orchestration).
---

# Literature Survey (graded pipeline)

A survey is only as good as its citation set. This skill turns a topic into a **graded, verified bibliography** — not a list of whatever search returned, but papers scored on a reproducible scale, classified by how deeply they'll be cited, and upgraded from preprint to accepted where possible. It is the literature half of paper-writing; the prose-writing conventions live in `literature-review`, the structure in `paper-structure`.

`kernel.py` provides `lqs_score`, `score_papers`, `classify_citation`, `bib_health` — deterministic, no LLM, no network. Use them; do not score papers in your head.

## The four stages

### Stage 1 — High-recall retrieval

Cast a wide net first; you will cut later by score, not by gut.

- Run **20–30 keyword queries**. For each cell of your taxonomy, 3+ query variants: core terms, synonyms, specific method names.
- Use `search_openalex` / `crossref_lookup` (from the `literature-review` skill's kernel) and any wired MCP literature connector. Run `search_skills({prefix:"mcp-"})` once to see which are available.
- **Snowball**: take the 2–3 most relevant hits and `expand_citations(doi)` one step forward and backward on the citation graph. The seminal paper a field builds on surfaces in the backward step; recent extensions surface in the forward step.
- **Target: 200–500 raw candidates** before scoring. A survey with fifteen papers found by three queries is a reading list, not a synthesis.

### Stage 2 — LQS scoring (deterministic)

Score every candidate with `lqs_score(paper)`. It combines five dimensions into one 0–10 score:

| Dimension | Weight | Scoring |
|-----------|--------|---------|
| Recency | 30% | this yr=10, 1yr=8, 2yr=5, 3yr=3 |
| Citation impact | 25% | cites/mo ≥50=10, ≥10=8, ≥3=6 |
| Venue | 20% | top-tier=10, strong=7, workshop=4 |
| Institution | 10% | top lab=10, top uni=9 |
| Acceptance | 15% | accepted=10, under review=5, preprint=3 |

**Tiers:** `lqs >= 7.0` → **must-cite** · `5.0–7.0` → **conditional** (cite if it fills a gap) · `< 5.0` → **drop**.

Pass each candidate as a dict with keys `year, citations, venue, authors, status`. Missing fields score neutral-low, not zero. `score_papers(list)` scores a batch and stamps `lqs`/`lqs_tier` onto each.

The point of deterministic scoring: two runs over the same retrieval get the same cut. Don't override the tier because you "feel" a paper is important — if you believe a dropped paper matters, retrieve the evidence (citations, venue) that raises its score.

### Stage 3 — Citation-depth classification (A/B/C/D)

For each must-cite and conditional paper, decide **how deeply** it gets cited — this drives how many papers you need per chapter:

| Level | Treatment | Density |
|-------|-----------|---------|
| **A** | Section protagonist — 1–3 paragraphs of dedicated discussion | 3–5 per chapter |
| **B** | Important insight — 2–5 sentences | 5–10 per chapter |
| **C** | Supporting evidence — one sentence, grouped citation | as needed |
| **D** | Dropped — not cited | — |

`classify_citation(paper, context)` suggests a level from LQS tier + how much surrounding discussion there is; the model finalizes. A common mistake: everything becomes B-level. Force the top 3–5 papers per chapter to A-level — they are what the chapter is *about*.

### Stage 4 — Venue upgrade

arXiv-only bibliographies read as "the author couldn't be bothered to check." Before finalizing:

- Cross-check DBLP / OpenReview / the publisher page for acceptance status.
- An arXiv preprint marked "Accepted at X" → upgrade the entry to `@inproceedings` with the real venue.
- **Target: arXiv-only ratio ≤ 60%.** Run `bib_health(bib_text)` to measure — it reports arxiv-only / within-1yr / accepted ratios and flags shortfalls.

## Verification (mechanical, not aspirational)

Hallucinated citations are the failure mode this skill exists to prevent. Make checking a step in the process, not a promise:

- **Every 20 citations**: title match, author list, year, venue — via `verify_dois` (from literature-review's kernel). Never batch up 80 and check at the end; errors compound.
- **Targets**: verification rate ≥ 80%, hallucinated = 0 (zero tolerance — a DOI that resolves to nothing is fabrication, drop it or replace it, don't keep it).
- **Distribution sanity**: within-1yr ≥ 40%, accepted (peer-reviewed) ≥ 30%. `bib_health` checks both.

## Output

Two files in the workspace:

- `references.bib` — only papers that survived scoring (must + conditional). Each entry complete (`@article`/`@inproceedings`, all fields from retrieval, never composed from memory).
- `citation_plan.jsonl` — one line per cited paper: `{key, lqs, tier, level (A/B/C), intended_section, doi}`. This is the bridge to `paper-structure`: it tells the writing phase where each citation lands.

Keep `\cite{key}` in the `.tex` and `@article{key,...}` in the `.bib` perfectly in sync — a key in one but not the other renders as `[?]`.
