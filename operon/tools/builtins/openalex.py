"""OpenAlex 学术论文检索工具。


OpenAlex 是开放的学术图谱 API (免 mailto 即可用,有 api_key 更稳)。

提供:
- search_papers: 关键词检索,返回标题/作者/年份/DOI/摘要/被引数
- fetch_paper: 按 DOI/OpenAlex-ID 取单篇详情
"""

from __future__ import annotations

from typing import Any

import httpx

from operon.tools.context import ToolContext

_OPENALEX_BASE = "https://api.openalex.org"


def _headers(ctx: ToolContext) -> dict[str, str]:
    """OpenAlex 请求头。有 api_key 用 api_key,否则用 mailto(OpenAlex 礼仪)。"""
    h = {"User-Agent": "operon-py/0.0.1 (https://github.com/operon-py)"}
    key = ctx.api_keys.get("OPENALEX_API_KEY") or ctx.api_keys.get("openalex")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


async def search_papers(
    ctx: ToolContext,
    query: str,
    max_results: int = 10,
    year_from: int | None = None,
    sort: str = "relevance_score:desc",
) -> str:
    """检索学术论文。

    Args:
        query: 检索词 (如 "multi-agent LLM orchestration")
        max_results: 最多返回数 (默认 10)
        year_from: 只返回此年份之后的 (如 2022)
        sort: 排序 (relevance_score:desc / cited_by_count:desc / publication_date:desc)
    Returns:
        论文列表 (标题/作者/年份/DOI/被引数/摘要前 300 字)
    """
    params: dict[str, Any] = {
        "search": query,
        "per-page": min(max_results, 25),
        "sort": sort,
    }
    if year_from:
        params["filter"] = f"from_publication_date:{year_from}-01-01"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{_OPENALEX_BASE}/works", params=params, headers=_headers(ctx))
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return f"Error searching OpenAlex: {type(e).__name__}: {e}"

    results = data.get("results", [])
    if not results:
        return f"No papers found for '{query}'."

    lines = [f"OpenAlex search: '{query}' ({len(results)} results, sort={sort})", ""]
    for i, w in enumerate(results, 1):
        title = w.get("display_name") or w.get("title") or "(untitled)"
        authors = _format_authors(w.get("authorships", []))
        year = w.get("publication_year") or "?"
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        cited = w.get("cited_by_count", 0)
        # 摘要: OpenAlex 用 inverted_index,需重建
        abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))

        lines.append(f"{i}. {title}")
        lines.append(f"   Authors: {authors}")
        lines.append(f"   Year: {year} | Cited by: {cited} | DOI: {doi}")
        if abstract:
            lines.append(f"   Abstract: {abstract[:300]}")
        lines.append("")
    return "\n".join(lines)


async def fetch_paper(ctx: ToolContext, doi: str) -> str:
    """按 DOI 取单篇论文详情。"""
    doi = doi.replace("https://doi.org/", "").strip()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{_OPENALEX_BASE}/works/doi:{doi}", headers=_headers(ctx)
            )
            resp.raise_for_status()
            w = resp.json()
    except httpx.HTTPError as e:
        return f"Error fetching DOI {doi}: {type(e).__name__}: {e}"

    title = w.get("display_name") or "(untitled)"
    authors = _format_authors(w.get("authorships", []))
    abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))
    venue = (w.get("primary_location") or {}).get("source", {}).get("display_name", "?")

    return (
        f"Title: {title}\n"
        f"Authors: {authors}\n"
        f"Venue: {venue} ({w.get('publication_year', '?')})\n"
        f"DOI: {doi}\n"
        f"Cited by: {w.get('cited_by_count', 0)}\n"
        f"Type: {w.get('type', '?')}\n"
        f"Abstract: {abstract}"
    )


def _format_authors(authorships: list) -> str:
    """格式化作者列表 (取前 3,超过加 et al.)。"""
    names = []
    for a in authorships[:3]:
        author = a.get("author") or {}
        name = author.get("display_name") or "?"
        names.append(name)
    s = ", ".join(names)
    if len(authorships) > 3:
        s += " et al."
    return s or "unknown"


def _reconstruct_abstract(inverted: dict | None) -> str:
    """OpenAlex 摘要是 inverted index,重建为文本。"""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for idx in idxs:
            positions.append((idx, word))
    positions.sort()
    return " ".join(w for _, w in positions)


SEARCH_PAPERS_SPEC = {
    "name": "search_papers",
    "description": (
        "Search academic papers via OpenAlex. Returns title, authors, year, DOI, "
        "citation count, abstract. "
        "Use for literature review and finding authoritative sources. "
        "Sort options: relevance_score:desc (default), cited_by_count:desc (most cited), "
        "publication_date:desc (newest)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default 10, max 25)"},
            "year_from": {"type": "integer", "description": "Only papers from this year onward"},
            "sort": {
                "type": "string",
                "enum": ["relevance_score:desc", "cited_by_count:desc", "publication_date:desc"],
                "description": "Sort order (default relevance)",
            },
        },
        "required": ["query"],
    },
}

FETCH_PAPER_SPEC = {
    "name": "fetch_paper",
    "description": "Fetch a single paper's full details by DOI via OpenAlex.",
    "parameters": {
        "type": "object",
        "properties": {
            "doi": {"type": "string", "description": "DOI (e.g. 10.1145/3292500.3330703)"}
        },
        "required": ["doi"],
    },
}
