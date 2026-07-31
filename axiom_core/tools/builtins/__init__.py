"""内置工具注册。


按角色返回不同的工具集 (原版 REVIEWER 只读集 i$z,MAIN 全集)。
"""

from __future__ import annotations

from axiom_core.tools.context import ToolContext
from axiom_core.tools.registry import ToolRegistry

from . import (
    artifact_tool,
    ask_user,
    boundary,
    call_agent,
    files,
    mcp_read_resource,
    memory,
    openalex,
    plan,
    registry_connect,
    summary_query,
    web,
)
from . import (
    edit_file as edit_mod,
)
from . import (
    env as env_mod,
)
from . import (
    exec as exec_mod,
)
from . import (
    skills as skill_mod,
)


def register_all(registry: ToolRegistry, ctx: ToolContext) -> None:
    """注册所有内置工具到 registry。"""

    # 代码执行
    registry.register(
        **exec_mod.PYTHON_SPEC, handler=lambda **kw: exec_mod.python(ctx, **kw)
    )
    registry.register(
        **exec_mod.BASH_SPEC, handler=lambda **kw: exec_mod.bash(ctx, **kw)
    )

    # 文件
    registry.register(
        **files.READ_FILE_SPEC, handler=lambda **kw: files.read_file(ctx, **kw)
    )
    registry.register(
        **files.WRITE_FILE_SPEC, handler=lambda **kw: files.write_file(ctx, **kw)
    )
    registry.register(
        **files.LIST_FILES_SPEC, handler=lambda **kw: files.list_files(ctx, **kw)
    )
    registry.register(
        **edit_mod.EDIT_FILE_SPEC, handler=lambda **kw: edit_mod.edit_file(ctx, **kw)
    )

    # LaTeX 编译 (.tex → PDF, 用 Tectonic)
    from . import latex as latex_mod

    registry.register(
        **latex_mod.COMPILE_PDF_SPEC, handler=lambda **kw: latex_mod.compile_pdf(ctx, **kw)
    )

    # plan mode
    registry.register(
        **plan.GENERATE_PLAN_SPEC, handler=lambda **kw: plan.generate_plan(ctx, **kw)
    )
    registry.register(
        **plan.UPDATE_STEP_STATUS_SPEC,
        handler=lambda **kw: plan.update_step_status(ctx, **kw),
    )

    # ask_user
    registry.register(**ask_user.ASK_USER_SPEC, handler=lambda **kw: ask_user.ask_user(ctx, **kw))

    # 包管理 (工作区隔离 venv)
    registry.register(
        **env_mod.INSTALL_PACKAGES_SPEC, handler=lambda **kw: env_mod.install_packages(ctx, **kw)
    )

    # 联网检索
    registry.register(
        **web.WEB_SEARCH_SPEC, handler=lambda **kw: web.web_search(ctx, **kw)
    )
    registry.register(
        **web.FETCH_URL_SPEC, handler=lambda **kw: web.fetch_url(ctx, **kw)
    )

    # MCP resource 读取 (通用; 配合 MCP 搜索工具拿摘要后读详情)
    registry.register(
        **mcp_read_resource.MCP_READ_RESOURCE_SPEC,
        handler=lambda **kw: mcp_read_resource.mcp_read_resource(ctx, **kw),
    )

    # Agent Registry: 动态挂载发现的 MCP server
    registry.register(
        **registry_connect.REGISTRY_CONNECT_MCP_SERVER_SPEC,
        handler=lambda **kw: registry_connect.registry_connect_mcp_server(ctx, **kw),
    )

    # Agent Registry: A2A 协议调用远端 agent
    registry.register(
        **call_agent.CALL_AGENT_SPEC,
        handler=lambda **kw: call_agent.call_agent(ctx, **kw),
    )

    # 学术论文检索 (OpenAlex)
    registry.register(
        **openalex.SEARCH_PAPERS_SPEC, handler=lambda **kw: openalex.search_papers(ctx, **kw)
    )
    registry.register(
        **openalex.FETCH_PAPER_SPEC, handler=lambda **kw: openalex.fetch_paper(ctx, **kw)
    )

    # Artifact 版本化 (阶段 4)
    registry.register(
        **artifact_tool.SAVE_ARTIFACTS_SPEC,
        handler=lambda **kw: artifact_tool.save_artifacts(ctx, **kw),
    )
    registry.register(
        **artifact_tool.GET_ARTIFACT_SPEC,
        handler=lambda **kw: artifact_tool.get_artifact(ctx, **kw),
    )
    registry.register(
        **artifact_tool.LIST_ARTIFACTS_SPEC,
        handler=lambda **kw: artifact_tool.list_artifacts(ctx, **kw),
    )

    # Skills 系统 (阶段 5)
    registry.register(
        **skill_mod.SEARCH_SKILLS_SPEC,
        handler=lambda **kw: skill_mod.search_skills(ctx, **kw),
    )
    registry.register(
        **skill_mod.LIST_SKILLS_SPEC,
        handler=lambda **kw: skill_mod.list_skills(ctx, **kw),
    )
    registry.register(
        **skill_mod.SKILL_SPEC,
        handler=lambda **kw: skill_mod.skill(ctx, **kw),
    )

    # Rolling Compact 辅助工具
    registry.register(
        **summary_query.SUMMARY_QUERY_SPEC,
        handler=lambda **kw: summary_query.summary_query(ctx, **kw),
    )
    registry.register(
        **boundary.BOUNDARY_SPEC,
        handler=lambda **kw: boundary.boundary(ctx, **kw),
    )

    # 三层记忆系统
    registry.register(
        **memory.READ_MEMORY_SPEC,
        handler=lambda **kw: memory.read_memory(ctx, **kw),
    )
    registry.register(
        **memory.WRITE_MEMORY_SPEC,
        handler=lambda **kw: memory.write_memory(ctx, **kw),
    )
    registry.register(
        **memory.SEARCH_MEMORY_SPEC,
        handler=lambda **kw: memory.search_memory(ctx, **kw),
    )

    # 子 agent 委派 (sub-agent)
    from . import delegate as delegate_mod
    from . import submit_output as submit_mod

    registry.register(
        **delegate_mod.DELEGATE_SPEC,
        handler=lambda **kw: delegate_mod.delegate(ctx, **kw),
    )
    registry.register(
        **submit_mod.SUBMIT_OUTPUT_SPEC,
        handler=lambda **kw: submit_mod.submit_output(ctx, **kw),
    )


def plan_mode_extras(registry: ToolRegistry, ctx: ToolContext) -> None:
    """plan mode 需要的工具已包含在 register_all 中 (generate_plan 等)。
    此函数保留供未来按模式添加工具 (原版 _buildToolSpecificSections 按条件注入)。
    """
    return None
