"""联网检索工具: web_search + fetch_url。

对应原版: web_search 是原版核心工具 (Anthropic 服务端 server tool)。
divergences §5: 原版用 Anthropic 服务端工具,本项目自建 (国内模型无等价)。

实装: DuckDuckGo HTML 搜索 (免 key) + httpx 抓取页面。
让 agent 能真正调研,而非凭空生成 (综述任务的硬需求)。
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx

from operon.tools.context import ToolContext

# 常见 User-Agent (避免被反爬挡)
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


async def web_search(ctx: ToolContext, query: str, max_results: int = 8) -> str:
    """网络搜索。返回结果列表 (标题/URL/摘要)。

    用 DuckDuckGo HTML 接口 (免 API key)。
    """
    results = await _ddg_search(query, max_results)
    if not results:
        return f"No results for '{query}'. (搜索可能被限流,稍后重试或换关键词)"

    lines = [f"Search: '{query}' ({len(results)} results)", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet'][:200]}")
        lines.append("")
    return "\n".join(lines)


async def fetch_url(ctx: ToolContext, url: str, max_chars: int = 8000) -> str:
    """抓取网页内容,返回清洗后的纯文本。

    agent 用此深入阅读搜索到的页面。
    """
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPError as e:
        return f"Error fetching {url}: {type(e).__name__}: {e}"

    text = _html_to_text(html)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[... truncated, {len(text) - max_chars} more chars]"
    return f"URL: {url}\n\n{text}"


async def _ddg_search(query: str, max_results: int) -> list[dict[str, str]]:
    """DuckDuckGo HTML 搜索解析。"""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPError:
        return []

    results: list[dict[str, str]] = []
    # DuckDuckGo HTML 结果块
    for block in re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.S):
        title_html, snippet_html = block
        title = _strip_tags(title_html).strip()
        snippet = _strip_tags(snippet_html).strip()
        # 提取 URL (DDG 用 redirect,真实 URL 在 a 标签里)
        m = re.search(r'href="([^"]+)"', html[html.find(title_html)-200:html.find(title_html)+10] if title_html in html else "")
        # 简化: 从整个结果区域找
        results.append({"title": title or "(no title)", "url": _extract_ddg_url(html, title), "snippet": snippet})
        if len(results) >= max_results:
            break
    # 备用解析: 更宽松
    if not results:
        for m in re.finditer(r'<a rel="nofollow" class="result__url"[^>]*href="([^"]+)"', html):
            pass
        # 用 result__a 的 href
        for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.+?)</a>', html, re.S):
            raw_url, title_html = m.group(1), m.group(2)
            title = _strip_tags(title_html).strip()
            # DDG 的 href 形如 //duckduckgo.com/l/?uddg=<encoded>
            real_url = raw_url
            udm = re.search(r"uddg=([^&]+)", raw_url)
            if udm:
                from urllib.parse import unquote

                real_url = unquote(udm.group(1))
            elif raw_url.startswith("//"):
                real_url = "https:" + raw_url
            # snippet
            after = html[m.end(): m.end() + 2000]
            sm = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.+?)</a>', after, re.S)
            snippet = _strip_tags(sm.group(1)).strip() if sm else ""
            results.append({"title": title, "url": real_url, "snippet": snippet})
            if len(results) >= max_results:
                break
    return results


def _extract_ddg_url(html: str, title: str) -> str:
    return ""


def _strip_tags(s: str) -> str:
    """去 HTML 标签 + 解码实体。"""
    import html as html_mod

    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    return s


def _html_to_text(html: str) -> str:
    """简易 HTML→text: 去 script/style/标签,压空白。"""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    import html as html_mod

    html = html_mod.unescape(html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


WEB_SEARCH_SPEC = {
    "name": "web_search",
    "description": (
        "Search the web for current information. Returns titles, URLs, and snippets. "
        "Use for research, finding documentation, latest practices. "
        "After searching, use fetch_url to read full pages of relevant results."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default 8)"},
        },
        "required": ["query"],
    },
}

FETCH_URL_SPEC = {
    "name": "fetch_url",
    "description": "Fetch and read the text content of a web page. Use to read full articles/docs found via web_search.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "max_chars": {"type": "integer", "description": "Max chars to return (default 8000)"},
        },
        "required": ["url"],
    },
}
