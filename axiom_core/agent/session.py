"""Session: agent 执行的高层入口。


把 FrameService / ToolRegistry / ToolRouter / Agent 组合起来,提供便捷 API。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiom_core.frames.service import FrameService
from axiom_core.llm.base import LLMClient
from axiom_core.tools.builtins import register_all
from axiom_core.tools.context import ToolContext
from axiom_core.tools.registry import ToolRegistry
from axiom_core.tools.router import ToolRouter

from .runner import Agent, AgentCallbacks, RunResult

logger = logging.getLogger(__name__)


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
    # 被用户禁用的 skill 名称列表 (session 级启用配置, 从 settings.json 传入默认值)
    disabled_skills: list[str] = None  # None → []
    # SQLite 持久层 (阶段 4)。None=纯内存 (CLI run / 测试); 提供时 ArtifactStore
    # 会 save_async 落库 + 启动时 load_from_db 回放, 支持断点续会话。
    db_session_factory: Any = None
    # 全局数据目录; 用于加载工具级 skill 目录 {data_dir}/skills。
    data_dir: Path | None = None
    # 是否加载 Claude Code 用户级 skill 目录 (~/.claude/skills)。
    load_claude_skills: bool = True
    # 是否加载当前项目/工作区 skill 目录 (workspace/.axiom/skills)。
    load_project_skills: bool = True
    # 额外自定义 skill 目录路径列表。
    skill_extra_dirs: list[str] | None = None
    # Layer A.5: 所属 project id。用于 frame.project_id + 记忆按 project 隔离。
    # None 时 frame.project_id 也 None, agent 第一次保存 artifact 时自动生成 proj_<root>。
    project_id: str | None = None


class Session:
    """一次 agent 会话的组装入口。

    用法:
        session = Session(llm=client, config=SessionConfig(workspace=Path(".")))
        result = await session.run("帮我列出文件")
    """

    def __init__(
        self, *, llm: LLMClient, config: SessionConfig, callbacks: AgentCallbacks | None = None
    ):
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
        """从 axiom_core.config.Settings 加载 Rolling Compact 配置。"""
        try:
            from axiom_core.config import load_settings

            settings = load_settings()
            return settings.rolling_compact
        except Exception:
            return None

    @staticmethod
    def _load_data_dir() -> Path | None:
        """从 axiom_core.config.Settings 加载全局数据目录。"""
        try:
            from axiom_core.config import load_settings

            settings = load_settings()
            return settings.data_dir_resolved()
        except Exception:
            return None

    async def prepare(self) -> ToolContext:
        """初始化 frame + ctx + MCP + 注册工具。返回 ctx。

        供 API 层复用 (创建会话时调一次)。
        """
        config = self.config
        config.workspace.mkdir(parents=True, exist_ok=True)

        # 根 frame
        # Layer A.5: session 创建时就赋 frame.project_id (替代 artifact_tool 的惰性赋值)
        frame = self.frame_service.create_root_frame(
            agent_name="MAIN",
            model=self.config.model,
            project_id=self.config.project_id,
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
        from axiom_core.artifacts.store import ArtifactStore

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

        # 初始化 Skill 目录 (阶段 5): 按优先级加载多来源 skills。
        # 优先级由低到高: builtin -> global -> claude -> project -> custom,
        # 后加载的同名 skill 覆盖先加载的。
        # Axiom 不会自动把 skill 复制到工作区,只扫描用户已放置的目录。
        from axiom_core.skills.catalog import (
            SkillCatalog,
            load_builtin_skills,
            load_claude_skills,
            load_custom_skills,
            load_global_skills,
            load_project_skills,
        )

        data_dir = config.data_dir or self._load_data_dir()
        catalog = SkillCatalog()
        for s in load_builtin_skills():
            catalog.add(s)
        if data_dir is not None:
            for s in load_global_skills(data_dir):
                catalog.add(s)
        if config.load_claude_skills:
            for s in load_claude_skills():
                catalog.add(s)
        if config.load_project_skills:
            for s in load_project_skills(config.workspace):
                catalog.add(s)
        for s in load_custom_skills(config.skill_extra_dirs or []):
            catalog.add(s)
        # 应用 session 级禁用配置
        if config.disabled_skills:
            catalog.set_disabled(config.disabled_skills)
        ctx.skill_catalog = catalog

        # 初始化三层记忆系统: MemoryStore + BM25 召回索引
        from axiom_core.memory.recall import build_index
        from axiom_core.memory.store import MemoryStore

        # memory.enabled 开关真正生效 (修死配置: 原来写了 enabled 字段但从不读)
        # 关闭时不初始化 memory_store / memory_index, runner 的 recall/extract 自然跳过
        try:
            from axiom_core.config import load_settings
            memory_enabled = load_settings().memory.enabled
        except Exception:
            memory_enabled = True

        if memory_enabled:
            ctx.memory_store = MemoryStore(db_session_factory=config.db_session_factory)
            try:
                all_mems = await ctx.memory_store.list_all()
                ctx.memory_index = build_index(all_mems)
            except Exception as e:
                logger.warning("memory index build failed: %s", e)
                ctx.memory_index = build_index([])
        else:
            ctx.memory_store = None
            ctx.memory_index = None

        # 初始化 host 对象 (阶段 6, 给 python kernel 的进程内接口)
        from axiom_core.host import make_host

        ctx.host = make_host(
            llm=self.llm,
            artifact_store=ctx.artifact_store,
            model=config.model,
        )
        # 注册内置工具
        register_all(self.registry, ctx)
        # 暴露 llm + registry 给 ctx (供 delegate 工具构造子 Agent)
        ctx.llm = self.llm
        ctx.registry = self.registry
        # Layer A.5: ctx.project_id 方便工具拿 (与 frame.project_id 一致)
        ctx.project_id = frame.project_id

        # 连接 MCP server + 注册 MCP 工具 (双轨)
        if config.mcp_servers:
            from axiom_core.mcp.manager import MCPServerManager
            from axiom_core.mcp.skill_gen import generate_mcp_skills
            from axiom_core.tools.builtins.mcp_proxy import (
                register_mcp_search_tools,
                register_mcp_tools,
            )

            self.mcp_manager = MCPServerManager()
            for srv in config.mcp_servers:
                await self.mcp_manager.add_server(srv)

            # 轨道 1: MCP 工具注册成 agent 工具
            # 工具数 ≤ threshold → 全量直接暴露 (现状); 超过 → 改用 mcp_search/mcp_call
            # 元工具模式, 避免每轮把所有 MCP schema 塞进 LLM 请求撑爆 context。
            total_mcp = len(self.mcp_manager.list_all_tools())
            threshold = self._load_mcp_threshold()
            if total_mcp <= threshold:
                n = register_mcp_tools(self.registry, self.mcp_manager)
            else:
                n = register_mcp_search_tools(self.registry, self.mcp_manager)
                logger.info(
                    "MCP tools (%d) > threshold (%d), using mcp_search/mcp_call meta-tools",
                    total_mcp, threshold,
                )
            self._mcp_tool_count = n
            # 轨道 2: 生成 mcp-* skill 文档 (发现层, 对照原版 0772.js RxO)
            for s in generate_mcp_skills(self.mcp_manager):
                ctx.skill_catalog.add(s)
            # host 对象回填 mcp_manager (使 host.mcp 可用)
            if ctx.host is not None:
                ctx.host._mcp_manager = self.mcp_manager
        return ctx

    @staticmethod
    def _load_mcp_threshold() -> int:
        """从 axiom_core.config.Settings 读 MCP search 阈值。失败回退默认 30。"""
        try:
            from axiom_core.config import load_settings

            settings = load_settings()
            return settings.mcp.search_threshold
        except Exception:
            return 30

    async def run(self, user_input: str) -> RunResult:
        """创建根 frame + 注册工具 + 跑 agent。"""
        ctx = await self.prepare()
        frame = ctx.frame

        # trace recorder (从 settings.trace 读配置; enabled=False 时返回零开销 _NullRecorder)
        # 用 frame.id 作为 session_id 维度 (一个 frame = 一次完整 agent run)
        trace_recorder = self._make_trace_recorder(frame.id)

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
            trace_recorder=trace_recorder,
        )
        result = await agent.run(user_input)
        # 关闭 trace recorder
        if hasattr(trace_recorder, "close"):
            trace_recorder.close()
        # 关闭 MCP 连接
        if self.mcp_manager is not None:
            await self.mcp_manager.close_all()
        return result

    @staticmethod
    def _make_trace_recorder(session_id: str):
        """从 axiom_core.config.Settings 读 trace 配置, 创建 recorder。

        enabled=False (默认) 时返回 _NullRecorder (零开销)。
        """
        try:
            from axiom_core.config import load_settings
            from axiom_core.observability import get_trace_recorder

            settings = load_settings()
            return get_trace_recorder(
                settings.trace,
                session_id=session_id,
                data_dir=settings.data_dir,
            )
        except Exception:
            # 任何失败都退化为不记 trace (不能影响主流程)
            return None


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
