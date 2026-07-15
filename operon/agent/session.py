"""Session: agent 执行的高层入口。

对应原版: 启动一个根 frame + 注册工具 + 创建 Agent + run。
把 FrameService / ToolRegistry / ToolRouter / Agent 组合起来,提供便捷 API。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from operon.frames.service import FrameService
from operon.llm.base import LLMClient
from operon.tools.builtins import register_all
from operon.tools.context import ToolContext
from operon.tools.registry import ToolRegistry
from operon.tools.router import ToolRouter

from .runner import Agent, AgentCallbacks, RunResult


@dataclass
class SessionConfig:
    """session 配置。"""

    workspace: Path
    max_iterations: int = 40
    max_tokens: int = 8192
    model: str | None = None
    plan_mode: bool = False
    # 模型实际上下文长度 (token)。None 时 Rolling Compact 用 config 默认 500000。
    # 重要: 不同模型不同 (StepFun step-3.7-flash = 256000)。Compact 的 hard-wall 按此触发。
    context_window: int | None = None
    # MCP server 配置 (会话级)
    mcp_servers: list = None  # list[MCPServerConfig]
    # 数据源 API keys (OpenAlex / Semantic Scholar 等)
    api_keys: dict[str, str] = None  # None → {}
    # 被用户禁用的 skill 名称列表 (从 settings.json 传入, 会话启动时过滤)
    disabled_skills: list[str] = None  # None → []
    # SQLite 持久层 (阶段 4)。None=纯内存 (CLI run / 测试); 提供时 ArtifactStore
    # 会 save_async 落库 + 启动时 load_from_db 回放, 支持断点续会话。
    db_session_factory: Any = None


class Session:
    """一次 agent 会话的组装入口。

    用法:
        session = Session(llm=client, config=SessionConfig(workspace=Path(".")))
        result = await session.run("帮我列出文件")
    """

    def __init__(self, *, llm: LLMClient, config: SessionConfig, callbacks: AgentCallbacks | None = None):
        self.llm = llm
        self.config = config
        self.callbacks = callbacks or AgentCallbacks()

        # 组件
        self.frame_service = FrameService()
        self.registry = ToolRegistry()
        self.mcp_manager = None  # MCPServerManager (有 MCP 时设置)
        self._mcp_tool_count = 0

    @staticmethod
    def _load_rc_config():
        """从 operon.config.Settings 加载 Rolling Compact 配置。"""
        try:
            from operon.config import load_settings

            settings = load_settings()
            return settings.rolling_compact
        except Exception:
            return None

    async def prepare(self) -> ToolContext:
        """初始化 frame + ctx + MCP + 注册工具。返回 ctx。

        供 API 层复用 (创建会话时调一次)。
        """
        config = self.config
        config.workspace.mkdir(parents=True, exist_ok=True)

        # 根 frame
        frame = self.frame_service.create_root_frame(
            agent_name="MAIN",
            model=self.config.model,
        )

        # 工具上下文
        ctx = ToolContext(
            frame=frame,
            frame_service=self.frame_service,
            workspace=config.workspace.resolve(),
            api_keys=config.api_keys or {},
            context_window=config.context_window,
            rolling_compact_config=self._load_rc_config(),
        )
        # 初始化 Artifact 版本化存储 (阶段 4: 内存优先 + 可选 SQLite 持久层)
        from operon.artifacts.store import ArtifactStore

        ctx.artifact_store = ArtifactStore(
            config.workspace.resolve(),
            db_session_factory=config.db_session_factory,
        )
        # 启动时从 DB 回放已有 artifact 元数据 (断点续会话)
        if config.db_session_factory is not None:
            try:
                n = await ctx.artifact_store.load_from_db()
                if n:
                    # 回放后重建 _by_filename 索引 (load_from_db 已建, 此处防御性日志)
                    pass
            except Exception as e:
                # DB 读失败不阻断会话启动 (退化为空内存态)
                import logging

                logging.getLogger(__name__).warning(
                    "artifact DB load failed, starting with empty store: %s", e
                )

        # 初始化 Skill 目录 (阶段 5): 扫工作区 .claude/skills + 内置
        from operon.skills.catalog import SkillCatalog, load_builtin_skills

        skills_root = config.workspace.resolve() / ".claude" / "skills"
        catalog = SkillCatalog(skills_root if skills_root.exists() else None)
        for s in load_builtin_skills():
            catalog.add(s)
        # 应用用户在设置面板中禁用的 skills
        if config.disabled_skills:
            catalog.set_disabled(config.disabled_skills)
        ctx.skill_catalog = catalog

        # 初始化三层记忆系统: MemoryStore + BM25 召回索引
        from operon.memory.recall import build_index
        from operon.memory.store import MemoryStore

        ctx.memory_store = MemoryStore(db_session_factory=config.db_session_factory)
        try:
            all_mems = await ctx.memory_store.list_all()
            ctx.memory_index = build_index(all_mems)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("memory index build failed: %s", e)
            ctx.memory_index = build_index([])

        # 初始化 host 对象 (阶段 6, 给 python kernel 的进程内接口)
        from operon.host import make_host

        ctx.host = make_host(
            llm=self.llm,
            artifact_store=ctx.artifact_store,
            model=config.model,
        )
        # 注册内置工具
        register_all(self.registry, ctx)

        # 连接 MCP server + 注册 MCP 工具 (双轨)
        if config.mcp_servers:
            from operon.mcp.manager import MCPServerManager
            from operon.mcp.skill_gen import generate_mcp_skills
            from operon.tools.builtins.mcp_proxy import register_mcp_tools

            self.mcp_manager = MCPServerManager()
            for srv in config.mcp_servers:
                await self.mcp_manager.add_server(srv)
            # 轨道 1: MCP 工具自动注册成 agent 工具 (直接调 mcp__server__tool)
            n = register_mcp_tools(self.registry, self.mcp_manager)
            self._mcp_tool_count = n
            # 轨道 2: 生成 mcp-* skill 文档 (发现层, 对照原版 0772.js RxO)
            for s in generate_mcp_skills(self.mcp_manager):
                ctx.skill_catalog.add(s)
            # host 对象回填 mcp_manager (使 host.mcp 可用)
            if ctx.host is not None:
                ctx.host._mcp_manager = self.mcp_manager
        return ctx

    async def run(self, user_input: str) -> RunResult:
        """创建根 frame + 注册工具 + 跑 agent。"""
        ctx = await self.prepare()
        frame = ctx.frame

        # agent
        router = ToolRouter(self.registry)
        agent = Agent(
            llm=self.llm,
            tool_router=router,
            frame_service=self.frame_service,
            frame=frame,
            ctx=ctx,
            max_iterations=self.config.max_iterations,
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            plan_mode=self.config.plan_mode,
            callbacks=self.callbacks,
        )
        result = await agent.run(user_input)
        # 关闭 MCP 连接
        if self.mcp_manager is not None:
            await self.mcp_manager.close_all()
        return result


async def run_session(
    user_input: str,
    *,
    llm: LLMClient,
    workspace: Path,
    plan_mode: bool = False,
    callbacks: AgentCallbacks | None = None,
) -> RunResult:
    """便捷入口。"""
    session = Session(
        llm=llm,
        config=SessionConfig(workspace=workspace, plan_mode=plan_mode),
        callbacks=callbacks,
    )
    return await session.run(user_input)
