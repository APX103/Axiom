"""
Peer-review helpers. Auto-loaded into the python kernel by the host when
the skill loads:

    aggregate_reviews, apply_anti_inflation, route_weaknesses, regression_check

Deterministic scoring aggregation and anti-inflation enforcement. The LLM
plays the individual reviewer personas; these functions enforce the rules
that keep the aggregate honest (median over reviewers, round caps, max
delta, mandatory unresolved weakness).

Module top level is definition-only so the sidecar AST gate accepts it.
"""

from __future__ import annotations

import statistics

# ---------------------------------------------------------------------------
# Reviewer personas — the model role-plays each, scoring independently.
# ---------------------------------------------------------------------------

REVIEWER_PERSONAS = [
    {
        "id": "R1",
        "name": "Experimentalist",
        "focus": "statistical rigor, baselines, replication",
        "weight_dimension": "experimental",
        "weight": 0.30,
    },
    {
        "id": "R2",
        "name": "Theorist",
        "focus": "formal definitions, proofs, MECE taxonomy",
        "weight_dimension": "technical_depth",
        "weight": 0.35,
    },
    {
        "id": "R3",
        "name": "Perfectionist",
        "focus": "writing quality, figures, formatting",
        "weight_dimension": "clarity",
        "weight": 0.30,
    },
    {
        "id": "R4",
        "name": "Synthesizer",
        "focus": "cross-cutting analysis, gap identification",
        "weight_dimension": "novelty",
        "weight": 0.25,
    },
    {
        "id": "R5",
        "name": "Newcomer",
        "focus": "accessibility, definitions, examples",
        "weight_dimension": "clarity",
        "weight": 0.35,
    },
]

# Score calibration anchors
SCORE_ANCHORS = {
    6.0: "workshop quality",
    7.0: "main conference quality",
    8.0: "strong accept (top 20%)",
    9.0: "oral / best paper",
}

# Anti-inflation parameters
FIRST_ROUND_CAP = 7.0      # round 1 score can't exceed this
MAX_DELTA_PER_ROUND = 1.5  # max improvement round-over-round
MIN_UNRESOLVED = 1         # at least this many weaknesses must remain open


def aggregate_reviews(reviews: list[dict]) -> dict:
    """Aggregate per-reviewer scores into a consensus.

    Each review: {"reviewer": "R1", "scores": {"novelty":7, "comprehensiveness":6,
    "clarity":8, "technical_depth":7, "experimental":5}, "strengths":[...],
    "weaknesses":[{"id","severity","text"}], "recommendation":"..."}.

    Final score = median of all reviewers' overall (mean of their dimensions).
    Returns the consensus + per-dimension medians + merged weakness list.
    """
    if not reviews:
        return {"score": 0.0, "ok": False, "error": "no reviews"}

    DIMS = ["novelty", "comprehensiveness", "clarity", "technical_depth", "experimental"]

    # Per-reviewer overall = mean of their dimension scores
    overalls = []
    dim_values: dict[str, list[float]] = {d: [] for d in DIMS}
    all_weaknesses: list[dict] = []
    all_strengths: list[str] = []
    recs: list[str] = []

    for rv in reviews:
        scores = rv.get("scores", {})
        # reviewer overall
        present = [scores[d] for d in DIMS if d in scores]
        if present:
            overalls.append(sum(present) / len(present))
        for d in DIMS:
            if d in scores:
                dim_values[d].append(scores[d])
        all_strengths.extend(rv.get("strengths", []))
        for w in rv.get("weaknesses", []):
            w = dict(w)
            w.setdefault("source_reviewer", rv.get("reviewer", "?"))
            all_weaknesses.append(w)
        if "recommendation" in rv:
            recs.append(rv["recommendation"])

    final_score = statistics.median(overalls) if overalls else 0.0
    dim_medians = {d: round(statistics.median(v), 1) for d, v in dim_values.items() if v}

    # recommendation = median-leaning: map rec strings to ordinals, take median
    rec_map = {"reject": 0, "borderline": 1, "weak accept": 2, "weak_accept": 2,
               "accept": 3}
    rec_ord = [rec_map.get(r.lower(), 1) for r in recs] or [1]
    inv_map = {0: "Reject", 1: "Borderline", 2: "Weak Accept", 3: "Accept"}
    consensus_rec = inv_map[round(statistics.median(rec_ord))]

    return {
        "score": round(final_score, 2),
        "dimension_scores": dim_medians,
        "n_reviewers": len(reviews),
        "recommendation": consensus_rec,
        "strengths": all_strengths,
        "weaknesses": all_weaknesses,
        "ok": True,
    }


def apply_anti_inflation(
    score: float,
    round_num: int,
    prev_score: float | None = None,
    weaknesses: list[dict] | None = None,
) -> dict:
    """Enforce the anti-inflation rules. Returns {score, capped, reasons}.

    - Round 1 score capped at FIRST_ROUND_CAP (every paper has room to improve).
    - Max +MAX_DELTA_PER_ROUND improvement over previous round.
    - At least MIN_UNRESOLVED weakness must remain (can't claim everything fixed).
    """
    reasons: list[str] = []
    capped = score

    if round_num <= 1 and capped > FIRST_ROUND_CAP:
        reasons.append(f"round-1 score capped {capped} → {FIRST_ROUND_CAP}")
        capped = FIRST_ROUND_CAP

    if prev_score is not None:
        delta = capped - prev_score
        if delta > MAX_DELTA_PER_ROUND:
            reasons.append(
                f"delta {delta:+.1f} exceeds max +{MAX_DELTA_PER_ROUND}/round → capped"
            )
            capped = prev_score + MAX_DELTA_PER_ROUND

    open_weaknesses = [w for w in (weaknesses or []) if w.get("severity") != "resolved"]
    if len(open_weaknesses) < MIN_UNRESOLVED and capped >= 7.0:
        reasons.append(
            f"score >= 7.0 but {len(open_weaknesses)} open weaknesses "
            f"(< {MIN_UNRESOLVED} required) — at least one weakness must stay unresolved"
        )
        # don't cap the score down, but flag it: a perfect paper is suspicious
        capped = min(capped, 8.4)

    return {"score": round(capped, 2), "capped": len(reasons) > 0, "reasons": reasons}


# ---------------------------------------------------------------------------
# Weakness routing — map a review weakness to the sub-skill that fixes it.
# ---------------------------------------------------------------------------

ROUTING_RULES = [
    # (keyword in weakness text → target sub-skill, action hint)
    ("citation", "lit-survey", "Stage 1-2 targeted search"),
    ("arxiv", "lit-survey", "Stage 4 venue upgrade"),
    ("reference", "lit-survey", "verify + expand citations"),
    ("taxonomy", "paper-structure", "redesign taxonomy axes (MECE)"),
    ("structure", "paper-structure", "reorder/restructure chapters"),
    ("paragraph", "paper-structure", "apply paragraph-logic patterns"),
    ("claim", "paper-structure", "calibrate hedge ladder"),
    ("abstract", "paper-structure", "align abstract ↔ conclusion"),
    ("experiment", "experiment-design", "design/execute supporting experiment"),
    ("baseline", "experiment-design", "add baselines or controls"),
    ("table", "academic-figures", "add/improve table"),
    ("figure", "academic-figures", "add/improve figure"),
    ("caption", "academic-figures", "rewrite captions with finding"),
]


def route_weaknesses(weaknesses: list[dict]) -> list[dict]:
    """Attach a 'route' (target sub-skill + action) to each weakness."""
    routed = []
    for w in weaknesses:
        text = (w.get("text", "") + " " + w.get("severity", "")).lower()
        route_to, action = "paper-structure", "address in revision"
        for keyword, target, act in ROUTING_RULES:
            if keyword in text:
                route_to, action = target, act
                break
        ww = dict(w)
        ww["route_to"] = route_to
        ww["action"] = action
        routed.append(ww)
    return routed


def regression_check(
    current_weaknesses: list[dict],
    prev_weaknesses: list[dict],
) -> dict:
    """Check that previously-fixed weaknesses haven't regressed.

    Compares weakness texts: a prev weakness not present (by fuzzy text match)
    in current is 'resolved'; a prev weakness reappearing is a 'regression'.
    """
    def _norm(t: str) -> str:
        return "".join(c for c in t.lower() if c.isalnum())[:60]

    prev_texts = {_norm(w.get("text", "")): w for w in prev_weaknesses}
    curr_texts = {_norm(w.get("text", "")) for w in current_weaknesses}

    resolved = [t for t in prev_texts if t not in curr_texts]
    regressions = [t for t in prev_texts if t in curr_texts
                   and prev_texts[t].get("severity") != "resolved"]

    return {
        "newly_resolved": len(resolved),
        "regressions": len(regressions),
        "regression_texts": [prev_texts[t].get("text", "") for t in regressions],
        "ok": len(regressions) == 0,
    }


def should_stop(
    score: float,
    round_num: int,
    prev_score: float | None = None,
    *,
    target: float = 8.5,
    max_rounds: int = 12,
    min_delta: float = 0.3,
    plateau_rounds: int = 2,
) -> tuple[bool, str]:
    """Decide whether the review loop should stop.

    Stop when ANY: score >= target, OR improvement ≤ min_delta for
    plateau_rounds consecutive rounds, OR round > max_rounds.
    """
    if score >= target:
        return True, f"target reached ({score} ≥ {target})"
    if round_num > max_rounds:
        return True, f"max rounds ({max_rounds}) exceeded"
    if prev_score is not None and score - prev_score <= min_delta:
        return True, f"plateaued (Δ ≤ {min_delta})"
    return False, "continue"
