"""MCP server → mcp-* skill 文档自动生成。



原版: MCP server 连接后,其工具列表被合成 mcp-<server> skill 文档,
让模型通过 search_skills/skill 发现,再用 host.mcp 调用。
本项目双轨: 既自动注册成 agent 工具 (直接调),也生成 mcp-* skill 文档 (发现)。
"""

from __future__ import annotations

import re

from operon.mcp.manager import MCPServerManager
from operon.skills.parser import Skill


def mcp_skill_name(server: str) -> str:
    """mcp-<server> 名称规范。对照原版 _v (0772.js:124)。"""
    slug = re.sub(r"[^a-z0-9-]", "-", server.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"mcp-{slug}"[:64]


def is_mcp_skill(name: str) -> bool:
    """是否是 mcp-* skill。对照原版 VZ (0772.js:136)。"""
    return name.startswith("mcp-")


def generate_mcp_skills(manager: MCPServerManager) -> list[Skill]:
    """把所有已连接 MCP server 的工具生成 mcp-* skill 文档。

    对照原版: 连接 connector → 抓工具列表 → 合成 mcp-* SKILL.md。
    每个文档含工具索引 (name + 一行描述) + host.mcp 调用提示。
    """
    skills: list[Skill] = []
    for tool in manager.list_all_tools():
        # 每个 server 一个 skill
        server = tool["server_name"]
        name = mcp_skill_name(server)
        if any(s.name == name for s in skills):
            continue  # 已有该 server 的 skill
        skills.append(_build_skill(manager, server, name))
    return skills


def _build_skill(manager: MCPServerManager, server: str, name: str) -> Skill:
    """构建单个 mcp-* skill 文档。对照原版 RxO (0772.js:497)。"""
    tools = [t for t in manager.list_all_tools() if t["server_name"] == server]
    # 工具索引
    lines = [
        f"# {server} (MCP connector)",
        "",
        f"This MCP server provides {len(tools)} tool(s). ",
        "You can call them directly as `mcp__{server}__<tool>` or via host.mcp.",
        "",
        "## Tools",
        "",
    ]
    for t in tools:
        desc = (t.get("description") or "")[:100]
        # 去掉 [MCP:server] 前缀 (我代理注册时加的)
        desc = re.sub(r"^\[MCP:[^\]]+\]\s*", "", desc)
        lines.append(f"- **{t['name']}** — {desc}")
    body = "\n".join(lines)
    description = f"MCP connector '{server}' with {len(tools)} tool(s): " + ", ".join(
        t["name"] for t in tools[:5]
    )
    return Skill(
        name=name,
        description=description[:200],
        body=body,
        source="mcp",
        index_text=f"{name} {description} {body[:2000]}",
    )
