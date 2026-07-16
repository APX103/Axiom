---
name: academic-figures
description: Presentation layer for survey data — high-information-density tables (comparison matrix, benchmark, ablation, taxonomy, meta-analysis) with booktabs three-line style, and academic figure standards (vector PDF preferred, ≥10pt fonts, labeled axes, self-contained captions that state the finding). Use after experiment-design produces results.json.
---

# Academic Figures & Tables

Tables and figures are where a survey's density lives — a good comparison table conveys in one glance what three paragraphs struggle to say. This skill sets the standards for the presentation layer. It consumes `results.json` (from `experiment-design`) and section placeholders (from `paper-structure`), producing `tables/*.tex` and figure specs.

## Tables

### Choose the right type

| Type | Use it for | Info density |
|------|-----------|--------------|
| **Comparison matrix** | methods × features | very high |
| **Benchmark table** | models × metrics | high |
| **Ablation table** | conditions × results | high |
| **Taxonomy table** | classification visualization | medium |
| **Meta-analysis** | aggregated cross-paper data | very high |

A survey's most valuable table is usually the **comparison matrix** (methods × features/taxonomy-cells) — it's the taxonomy in tabular form, and it makes gaps (empty cells) visible at a glance.

### Table rules

- **No vertical lines.** `booktabs` three-line style only (`\toprule`, `\midrule`, `\bottomrule`).
- Alternating row color: `\rowcolor{gray!6}` for readability on wide tables.
- **Bold the best result** in each column.
- All experimental data: `mean ± std` (never just the mean — reviewers distrust bare means).
- **Caption must contain the key finding**, not just describe the table. "Table 3: Results" is useless. "Table 3: Method A outperforms B on long-horizon tasks (+12%) but not short-horizon" tells the reader the point.

## Figures

### Types and tools (by priority)

| Priority | Type | Tool |
|----------|------|------|
| 1st | Architecture / flow diagrams | TikZ (native LaTeX, scales perfectly) |
| 2nd | Data-driven (curves, bars, heatmaps) | matplotlib → PDF |
| 3rd | External diagrams | SVG → PDF |
| 4th | Simple schematics | PIL → PNG (acceptable per reviewer feedback, but lowest quality) |

### Quality checklist

- **Vector format (PDF) preferred.** PNG only if unavoidable, and then ≥ 300 DPI.
- **Font size ≥ 10pt** after scaling — if a reader has to zoom to read axis labels, the figure is broken.
- **Academic palette**: blue `#2196F3`, red `#F44336`, green `#4CAF50`, orange `#FF9800`. Consistent across all figures.
- All axes labeled (with units); all lines have a legend.
- Light grid (`alpha=0.3`) for readability — heavy grids compete with the data.
- **Self-contained**: understandable without reading the main text. A figure that requires three sentences of body prose to decode has failed.

### Quantity targets

| Paper length | Tables | Figures |
|--------------|--------|---------|
| Full survey (50+ pages) | ≥ 10 | ≥ 6 |
| Short survey (~30 pages) | ≥ 5 | ≥ 3 |

These are floors, not ceilings. But don't pad — a figure that doesn't carry information is worse than no figure (it wastes the reader's attention).

## Captions carry the load

Reviewers often read figures and tables *before* the body text. Every caption should let them do that:

- State the **finding**, not just the contents ("X outperforms Y by 12%" not "Comparison of X and Y").
- Define every abbreviation used in the figure (the caption is self-contained).
- For tables, note the metric and its direction ("higher is better" / "lower is better").

A reader who only looks at the figures, tables, and captions should understand the survey's main results.
