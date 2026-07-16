"""
Literature-survey helpers. Auto-loaded into the python kernel by the host
when the skill loads:

    lqs_score, classify_citation, bib_health

These are deterministic (no LLM, no network) so the survey's literature
filtering is reproducible. Network lookups (search/verify) stay in the
sibling literature-review skill's kernel.

Module top level is definition-only (functions, imports, literal
constants) so the sidecar AST gate accepts it.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

# ---------------------------------------------------------------------------
# LQS (Literature Quality Score) — multi-dimensional paper scoring.
#
# Adapted from Deli Chen's AutoResearch paper-writing pipeline. Weights and
# buckets are tuned for survey citation selection: a paper must clear LQS>=7.0
# to be a must-cite, 5.0-7.0 is conditional, <5.0 is dropped.
#
# All dimensions are scored 0-10 then combined by weight into a single LQS.
# ---------------------------------------------------------------------------

LQS_WEIGHTS = {
    "recency": 0.30,
    "citation_impact": 0.25,
    "venue": 0.20,
    "institution": 0.10,
    "acceptance": 0.15,
}

# Venue tiers — curated top-tier venues per area. Match is case-insensitive
# substring on the venue/journal/container field. Extend as needed.
TOP_VENUES = [
    # ML/AI
    "neurips", "nips", "icml", "iclr", "aaai", "ijcai", "cvpr", "iccv", "eccv",
    "acl", "emnlp", "naacl", "coling", "kdd", "www", "sigir", "aaai",
    # Systems / security
    "sosp", "osdi", "sigcomm", "nsdi", "ccs", "s&p", "security", "usenix",
    # Nature / Science family
    "nature", "science", "pnas", "nature machine intelligence",
    # Top journals
    "jmlr", "tpami", "transactions on pattern analysis",
]
STRONG_VENUES = [
    "transactions", "journal of", "plos", "bmj", "workshop", "findings",
    "emnlp findings", "neurips workshop",
]


def _parse_year(year: Any) -> int | None:
    """Best-effort coerce a year value (int/str) to int."""
    if year is None:
        return None
    try:
        return int(str(year).strip()[:4])
    except (ValueError, TypeError):
        return None


def _now_year() -> int:
    return _dt.datetime.now().year


def score_recency(year: Any, now_year: int | None = None) -> float:
    """Recency dimension (weight 30%).

    6mo=10 is approximated by current year; 1yr=8; 2yr=5; 3yr=3; older=1.
    """
    y = _parse_year(year)
    if y is None:
        return 3.0  # unknown year — neutral-low
    now = now_year or _now_year()
    age = now - y
    if age <= 0:
        return 10.0
    if age == 1:
        return 8.0
    if age == 2:
        return 5.0
    if age == 3:
        return 3.0
    return 1.0


def score_citation_impact(citations: Any, year: Any) -> float:
    """Citation impact dimension (weight 25%).

    cites/month normalizes raw citation count by age. >=50/mo=10,
    >=10/mo=8, >=3/mo=6, else scales down. Foundational old papers with
    huge raw counts still score well after normalization.
    """
    try:
        cites = int(citations)
    except (ValueError, TypeError):
        return 3.0  # unknown — neutral
    y = _parse_year(year)
    now = _now_year()
    age_years = max(1, (now - y) if y else 3)  # assume ~3yr if unknown
    per_month = cites / (age_years * 12.0)
    if per_month >= 50:
        return 10.0
    if per_month >= 10:
        return 8.0
    if per_month >= 3:
        return 6.0
    if per_month >= 1:
        return 4.0
    return 2.0


def score_venue(venue: Any) -> float:
    """Venue dimension (weight 20%). Top-tier=10, Strong=7, Workshop=4, else=3."""
    v = str(venue or "").lower()
    if not v:
        return 3.0
    # workshop check first (a top venue + "workshop" is still a workshop)
    if "workshop" in v:
        return 4.0
    if any(t in v for t in TOP_VENUES):
        return 10.0
    if any(t in v for t in STRONG_VENUES):
        return 7.0
    return 3.0


def score_institution(authors: Any) -> float:
    """Institution dimension (weight 10%).

    Coarse heuristic: top lab / top university affiliated → 10/9. Without an
    affiliation database we score by author count signal (multi-author papers
    from large collaborations correlate with strong institutions) and fall
    back to neutral. Refine when affiliation data is available.
    """
    if not authors:
        return 5.0  # neutral
    # Large industry/academic collaborations often have many authors; this is
    # a weak signal, intentionally capped so it doesn't dominate.
    if isinstance(authors, list):
        if len(authors) >= 8:
            return 8.0
        if len(authors) >= 1:
            return 6.0
    return 5.0


def score_acceptance(status: Any) -> float:
    """Acceptance dimension (weight 15%).

    Accepted (peer-reviewed) = 10, Under review = 5, None/preprint = 3.
    `status` may be one of: "accepted", "under_review", "preprint", None,
    or a venue string (if it looks peer-reviewed → accepted).
    """
    s = str(status or "").lower().strip()
    if s in ("accepted", "published", "inproceedings"):
        return 10.0
    if s in ("under review", "under_review", "submitted"):
        return 5.0
    if s in ("preprint", "arxiv", "none", ""):
        return 3.0
    # If a venue name was passed, infer from it
    return 10.0 if score_venue(s) >= 7.0 else 5.0


def lqs_score(paper: dict) -> dict:
    """Score a single paper dict on the LQS scale.

    Expected keys (all optional, missing → neutral scores):
        year, citations, venue (or journal/container), authors (list),
        status (accepted|under_review|preprint)

    Returns {"lqs": float, "dimensions": {...}, "tier": "must|conditional|drop"}.
    """
    venue = paper.get("venue") or paper.get("journal") or paper.get("container")
    dims = {
        "recency": score_recency(paper.get("year")),
        "citation_impact": score_citation_impact(
            paper.get("citations"), paper.get("year")
        ),
        "venue": score_venue(venue),
        "institution": score_institution(paper.get("authors")),
        "acceptance": score_acceptance(paper.get("status") or venue),
    }
    lqs = sum(dims[k] * w for k, w in LQS_WEIGHTS.items())
    if lqs >= 7.0:
        tier = "must"
    elif lqs >= 5.0:
        tier = "conditional"
    else:
        tier = "drop"
    return {"lqs": round(lqs, 2), "dimensions": dims, "tier": tier}


def score_papers(papers: list[dict]) -> list[dict]:
    """Score a batch and attach LQS to each (mutates copies)."""
    out = []
    for p in papers:
        r = lqs_score(p)
        pp = dict(p)
        pp["lqs"] = r["lqs"]
        pp["lqs_tier"] = r["tier"]
        pp["lqs_dimensions"] = r["dimensions"]
        out.append(pp)
    return out


# ---------------------------------------------------------------------------
# Citation depth classification (A/B/C/D) — which slot a citation fills.
#
# This is a *recommendation* the model finalizes: A-level = section
# protagonist (3-5 per chapter), B-level = important insight (5-10/chapter),
# C-level = one-sentence support, D-level = dropped (not cited).
# ---------------------------------------------------------------------------


def classify_citation(paper: dict, context: str = "") -> str:
    """Suggest an A/B/C/D citation level from LQS tier + usage signal.

    `context` is optional surrounding text (where the citation lands); longer
    dedicated discussion → A. The model has final say.
    """
    tier = paper.get("lqs_tier") or lqs_score(paper)["tier"]
    ctx_len = len(context or "")
    if tier == "must" and ctx_len > 400:
        return "A"
    if tier == "must":
        return "B"
    if tier == "conditional":
        return "C"
    return "D"  # drop


# ---------------------------------------------------------------------------
# Bibliography health check — runs over a .bib string or parsed entries.
# ---------------------------------------------------------------------------


def bib_health(bib_text_or_entries: Any) -> dict:
    """Quick health stats over a bibliography.

    Accepts a raw .bib string or a list of entry dicts (each with at least
    a 'year' and optional 'venue'/'status'). Reports:
      - total entries
      - arxiv-only ratio (target <= 60%)
      - within-1yr ratio (target >= 40%)
      - accepted ratio (target >= 30%)
      - missing required fields
    """
    entries = _coerce_entries(bib_text_or_entries)
    if not entries:
        return {"total": 0, "ok": False, "issues": ["empty bibliography"]}

    now = _now_year()
    total = len(entries)
    arxiv_only = 0
    within_1yr = 0
    accepted = 0
    missing_fields = []

    for e in entries:
        venue = str(e.get("venue") or e.get("journal") or "").lower()
        status = str(e.get("status") or "").lower()
        y = _parse_year(e.get("year"))

        is_arxiv = "arxiv" in venue or status == "preprint"
        looks_accepted = status in ("accepted", "published") or (
            venue and "arxiv" not in venue and score_venue(venue) >= 7
        )
        if is_arxiv and not looks_accepted:
            arxiv_only += 1
        if y is not None and y >= now - 1:
            within_1yr += 1
        if looks_accepted:
            accepted += 1
        for f in ("title", "author", "year"):
            if not e.get(f):
                missing_fields.append(f"entry missing {f}")

    arxiv_ratio = arxiv_only / total
    recent_ratio = within_1yr / total
    accepted_ratio = accepted / total

    issues = []
    if arxiv_ratio > 0.60:
        issues.append(f"arxiv-only ratio {arxiv_ratio:.0%} > 60% target — upgrade venues")
    if recent_ratio < 0.40:
        issues.append(f"within-1yr ratio {recent_ratio:.0%} < 40% target — add recent work")
    if accepted_ratio < 0.30:
        issues.append(f"accepted ratio {accepted_ratio:.0%} < 30% target")
    if missing_fields:
        issues.append(f"{len(missing_fields)} entries missing required fields")

    return {
        "total": total,
        "arxiv_only_ratio": round(arxiv_ratio, 2),
        "within_1yr_ratio": round(recent_ratio, 2),
        "accepted_ratio": round(accepted_ratio, 2),
        "ok": len(issues) == 0,
        "issues": issues,
    }


def _coerce_entries(x: Any) -> list[dict]:
    """Accept a list of dicts or a raw .bib string → list of entry dicts."""
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return _parse_bib(x)
    return []


def _parse_bib(bib: str) -> list[dict]:
    """Minimal .bib parser: extract @type{key, field = {val}, ...} entries."""
    entries = []
    i = 0
    n = len(bib)
    while i < n:
        at = bib.find("@", i)
        if at < 0:
            break
        brace = bib.find("{", at)
        if brace < 0:
            break
        # key
        comma = bib.find(",", brace)
        if comma < 0:
            i = at + 1
            continue
        depth = 1
        j = comma + 1
        while j < n and depth > 0:
            if bib[j] == "{":
                depth += 1
            elif bib[j] == "}":
                depth -= 1
            j += 1
        body = bib[brace + 1 : j - 1]
        entry: dict[str, Any] = {"_raw": bib[at:j]}
        for field in re.finditer(r"(\w+)\s*=\s*[{\"](.*?)[}\"]", body, re.S):
            entry[field.group(1).lower()] = field.group(2).strip().replace("\n", " ")
        if entry:
            entries.append(entry)
        i = j
    return entries
