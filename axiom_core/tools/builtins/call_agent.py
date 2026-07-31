"""call_agent 工具: 以 A2A 协议调用 Agent Registry 里发现的远端 agent。

典型流程 (配合 Agent Registry 发现层):
1. mcp__agent-registry__search_agents / search_resources 搜索 (摘要)
2. mcp_read_resource 读 agent://<name> 详情 (endpoint/skills/auth)
3. 用本工具调用: 自动取 endpoint + 凭据 → A2A message/stream → 聚合结果

多轮: 远端返回 input_required 时, 用 ask_user 拿到用户答复后,
以相同的 context_id/task_id 再次调用本工具续接 (无需在 frame 里持久化,
LLM 从上次工具结果里拿)。

安全边界: 明文凭据只进 HTTP header, 工具返回值只含状态/文本/context_id,
绝不含凭据。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from axiom_core.a2a.client import A2AClient, A2AError
from axiom_core.config import load_settings
from axiom_core.registry.client import RegistryError, client_from_settings
from axiom_core.tools.context import ToolContext

CALL_AGENT_SPEC = {
    "name": "call_agent",
    "description": (
        "调用 Agent Registry 里发现的远端 A2A agent: 把任务文本交给它执行并拿回结果。"
        "先用 agent-registry 的 search 工具找到 agent 名, 再调用本工具。"
        "远端 agent 返回 input_required 时会带上 context_id/task_id: 先用 ask_user "
        "向用户索取答复, 然后带上相同的 context_id/task_id 再次调用本工具续接。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "registry 里的 agent 名 (与 search/resource 结果一致)",
            },
            "task": {
                "type": "string",
                "description": "要交给远端 agent 执行的任务文本",
            },
            "context_id": {
                "type": "string",
                "description": "可选, 多轮对话续接用 (上次调用返回值里的 context_id)",
            },
            "task_id": {
                "type": "string",
                "description": "可选, input_required 续接用 (上次调用返回值里的 task_id)",
            },
        },
        "required": ["name", "task"],
    },
}


def _build_auth_headers(credential: dict[str, Any]) -> dict[str, str]:
    """把 registry 凭据 {scheme_type, scheme_config, credential} 转成 HTTP headers。

    支持: none / apiKey (scheme_config 指定 header 名, 常见 X-API-Key) /
    http bearer (Authorization: Bearer <credential>)。
    其余 (oauth2/openIdConnect/mutualTLS) 抛 ValueError。

    安全边界: 返回值含明文凭据, 只允许注入 HTTP header, 不得进工具返回值。
    """
    scheme = str(credential.get("scheme_type") or "none").lower()
    if scheme == "none":
        return {}
    cred_value = credential.get("credential") or ""
    config = credential.get("scheme_config") or {}
    if scheme == "apikey":
        if not cred_value:
            raise ValueError("apiKey 凭据为空")
        header_name = (
            config.get("header_name") or config.get("name") or "X-API-Key"
        )
        return {header_name: cred_value}
    if scheme in ("http", "bearer"):
        if not cred_value:
            raise ValueError("http bearer 凭据为空")
        return {"Authorization": f"Bearer {cred_value}"}
    raise ValueError(
        f"暂不支持的认证类型: {credential.get('scheme_type')} (仅支持 none/apiKey/http bearer)"
    )


async def call_agent(
    ctx: ToolContext,
    name: str,
    task: str,
    context_id: str | None = None,
    task_id: str | None = None,
    **_kw,
) -> str:
    """调用 registry 中的 A2A agent。返回 JSON 字符串 (不含凭据)。"""
    manager = ctx.mcp_manager
    if manager is None:
        return "Error: no MCP manager in this session (MCP 未连接)"
    if not manager.is_connected("agent-registry"):
        return "Error: agent registry 未连接 (settings.registry 未启用?)"

    # a. 读 agent://<name> 详情拿 endpoint
    info_text = await manager.read_resource("agent-registry", f"agent://{name}")
    if info_text.startswith("Error:"):
        return info_text
    try:
        info = json.loads(info_text)
    except json.JSONDecodeError:
        return f"Error: agent://{name} resource 不是合法 JSON"
    endpoint = info.get("endpoint") or info.get("url") or ""
    if not endpoint:
        return f"Error: agent '{name}' 详情里没有 endpoint"

    # b. 取凭据 → 构建 auth headers (凭据只进 header)
    try:
        registry_client = client_from_settings()
    except RegistryError as e:
        return f"Error: {e}"
    try:
        credential = await registry_client.get_agent_credential(name)
    except RegistryError as e:
        return f"Error: registry: {e}"
    finally:
        await registry_client.close()

    try:
        headers = _build_auth_headers(credential)
    except ValueError as e:
        return f"Error: {e}"

    # c. A2A 调用 (message/stream, 失败自动回退 message/send)
    timeout = float(load_settings().a2a.default_timeout)
    a2a = A2AClient(endpoint, headers=headers, timeout=timeout)
    try:
        result = await a2a.send_message_streaming(task, context_id=context_id, task_id=task_id)
    except httpx.TimeoutException:
        return f"Error: 调用 agent '{name}' 超时 ({int(timeout)}s)"
    except (A2AError, httpx.HTTPError) as e:
        return f"Error: A2A 调用失败: {e}"
    finally:
        await a2a.close()

    # d. 按终结态映射返回值
    if result.state == "completed":
        return json.dumps(
            {"status": "completed", "text": result.text, "context_id": result.context_id},
            ensure_ascii=False,
        )
    if result.state == "input-required":
        hint = (
            "\n\n[远端 agent 等待补充输入: 先用 ask_user 向用户索取答复, "
            "然后带上本结果里的 context_id 和 task_id 再次调用 call_agent 续接。]"
        )
        return json.dumps(
            {
                "status": "input_required",
                "text": result.text + hint,
                "context_id": result.context_id,
                "task_id": result.task_id,
            },
            ensure_ascii=False,
        )
    if result.state in ("failed", "canceled"):
        detail = result.error or result.text or "无详情"
        return f"Error: agent '{name}' 任务 {result.state}: {detail}"
    return f"Error: agent '{name}' 返回了非终结状态: {result.state}"
