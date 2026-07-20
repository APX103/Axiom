"""MCP 工具代理: 把 MCP server 的工具自动注册成 agent 直接可调的工具。


本项目: 自动注册成 agent 工具,工具名 mcp__<server>__<tool>,体验更好。

session 创建时:
1. manager.list_all_tools() 拿到所有 MCP 工具
2. 工具数 ≤ threshold: register_mcp_tools 全量注册
3. 工具数 > threshold: register_mcp_search_tools 只注册 mcp_search / mcp_call
   元工具, 避免每轮把所有 MCP schema 塞进 LLM 请求撑爆 context

阈值切换的动机: SciForge 用 mcp_search/mcp_describe/mcp_call 三件套解决 MCP
工具爆炸, 我们简化成 search/call 两件 (describe 内联到 search 结果里)。
"""

from __future__ import annotations

import json
import re
from typing import Any

from operon.mcp.manager import MCPServerManager
from operon.skills.search import SkillIndex, SearchResult, tokenize  # 复用 BM25
from operon.skills.parser import Skill  # 仅用于构造索引对象
from operon.tools.registry import ToolRegistry

# MCP Search 元工具 schema (按 OpenAI function tool 规范)
_MCP_SEARCH_SPEC = {
    "name": "mcp_search",
    "description": (
        "在已连接的 MCP server 中检索匹配的工具。当 MCP 工具很多时, "
        "工具 schema 不会全部展示给你, 用此工具先找出要用的 MCP 工具名, "
        "再用 mcp_call 调用。返回: [{name, server, description}]。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用自然语言描述你要做的事, 如 'search the web for latest papers'",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回几个匹配工具, 默认 8",
                "default": 8,
            },
        },
        "required": ["query"],
    },
}

_MCP_CALL_SPEC = {
    "name": "mcp_call",
    "description": (
        "调用一个已知的 MCP 工具。先用 mcp_search 找到工具的准确名字, "
        "再调用此工具。name 格式: mcp__<server>__<tool>。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "MCP 工具全名, 如 mcp__web_search_prime__search",
            },
            "arguments": {
                "type": "object",
                "description": "传给 MCP 工具的参数对象",
                "default": {},
            },
        },
        "required": ["name"],
    },
}


def _sanitize(name: str) -> str:
    """工具名只保留字母数字下划线。"""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def tool_name(server: str, tool: str) -> str:
    """MCP 工具的全名: mcp__<server>__<tool>。"""
    return f"mcp__{_sanitize(server)}__{_sanitize(tool)}"


def register_mcp_tools(registry: ToolRegistry, manager: MCPServerManager) -> int:
    """把所有已连接 MCP server 的工具注册到 registry (全量直接暴露模式)。

    返回注册的工具数。
    工具 description 标注来源 server。parameters 透传 MCP inputSchema。
    handler 路由到 manager.call_tool。
    """
    count = 0
    for t in manager.list_all_tools():
        server_name = t["server_name"]
        tool_name_raw = t["name"]
        full_name = tool_name(server_name, tool_name_raw)
        description = t["description"] or f"MCP tool {tool_name_raw}"
        description = f"[MCP:{server_name}] {description}"
        parameters = t["input_schema"] or {"type": "object", "properties": {}}

        # 闭包捕获正确的 server/tool 名 (避免循环变量陷阱)
        def _make_handler(srv: str, tool: str):
            async def _handler(**kwargs):
                return await manager.call_tool(srv, tool, kwargs)

            return _handler

        try:
            registry.register(
                name=full_name,
                description=description,
                parameters=parameters,
                handler=_make_handler(server_name, tool_name_raw),
            )
            count += 1
        except Exception:
            # 重复名或 schema 问题,跳过
            pass
    return count


def register_mcp_search_tools(registry: ToolRegistry, manager: MCPServerManager) -> int:
    """工具数过多时, 只注册 mcp_search + mcp_call 两个元工具。

    返回 2 (固定)。
    工具列表缓存在闭包里, 用 operon.skills.search 的 BM25 索引检索。
    """
    # 构造 SkillIndex 适配对象 (SkillIndex 强依赖 Skill dataclass)
    # 把 MCP 工具包成 Skill, 复用 BM25 引擎
    all_tools = manager.list_all_tools()
    skills_for_index = [
        Skill(
            name=tool_name(t["server_name"], t["name"]),
            description=t["description"] or "",
            body="",
        )
        for t in all_tools
    ]
    index = SkillIndex(skills_for_index) if skills_for_index else None

    # 建立 full_name → (server, raw_name) 映射, 供 mcp_call 路由
    name_map: dict[str, tuple[str, str]] = {
        tool_name(t["server_name"], t["name"]): (t["server_name"], t["name"])
        for t in all_tools
    }

    async def _mcp_search_handler(query: str, max_results: int = 8, **_kw):
        """返回匹配的 MCP 工具列表 (JSON 字符串)。"""
        if index is None:
            return json.dumps({"error": "no MCP tools available"}, ensure_ascii=False)
        results: list[SearchResult] = index.search(query, max_results=max_results)
        out = []
        for r in results:
            full = r.skill.name
            server, raw = name_map.get(full, ("", ""))
            out.append({
                "name": full,
                "server": server,
                "tool": raw,
                "description": r.skill.description,
                "score": round(r.score, 4),
            })
        return json.dumps({"tools": out, "total_indexed": len(all_tools)}, ensure_ascii=False)

    async def _mcp_call_handler(name: str, arguments: dict[str, Any] | None = None, **_kw):
        """根据工具全名 mcp__<server>__<tool> 路由调用。"""
        if name not in name_map:
            available = list(name_map.keys())[:10]
            return json.dumps({
                "error": f"unknown MCP tool: {name}",
                "hint": "use mcp_search to find the right tool name first",
                "available_examples": available,
            }, ensure_ascii=False)
        server, raw = name_map[name]
        return await manager.call_tool(server, raw, arguments or {})

    try:
        registry.register(
            name="mcp_search",
            description=_MCP_SEARCH_SPEC["description"],
            parameters=_MCP_SEARCH_SPEC["parameters"],
            handler=_mcp_search_handler,
        )
    except Exception:
        pass
    try:
        registry.register(
            name="mcp_call",
            description=_MCP_CALL_SPEC["description"],
            parameters=_MCP_CALL_SPEC["parameters"],
            handler=_mcp_call_handler,
        )
    except Exception:
        pass
    return 2
