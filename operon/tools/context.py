"""工具运行上下文。

工具 handler 需要访问 agent 的共享状态 (frame、工作区、plan 状态等)。
本模块定义 ToolContext,所有 builtin 工具通过它访问运行时环境。


"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from operon.frames.model import Frame
from operon.frames.service import FrameService


@dataclass
class PlanState:
    """Plan mode 状态。

    steps: [{id, description, status}]
    approved: 是否已批准
    plan_artifact_id: plan artifact id (阶段 4 才真存,这里用内存占位)
    """

    steps: list[dict[str, Any]] = field(default_factory=list)
    approved: bool = False
    plan_artifact_id: str | None = None

    def find_step(self, step_id: str) -> dict[str, Any] | None:
        for s in self.steps:
            if s["id"] == step_id:
                return s
        return None


@dataclass
class PendingUserAsk:
    """ask_user 挂起的问题。触发 AWAITING_USER_RESPONSE。"""

    question: str
    options: list[str] = field(default_factory=list)


@dataclass
class ToolContext:
    """工具运行上下文。

    工具通过此对象访问: 当前 frame、frame 服务、工作区、plan 状态。
    """

    frame: Frame
    frame_service: FrameService
    workspace: Path
    plan: PlanState = field(default_factory=PlanState)
    # ask_user 挂起 (触发 awaiting_user_response)
    pending_ask: PendingUserAsk | None = None
    # 工具可写的任意运行时数据 (如收集的产物清单,供阶段 4)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 工作区隔离 venv 的 python 解释器路径 (None=用进程默认 python)。
    # 解决可复现性: agent 在 venv 里装依赖,产物自带 venv,换环境也能跑。
    venv_python: Path | None = None
    # 数据源 API keys (OpenAlex/SEMANTIC_SCHOLAR 等)。
    #
    api_keys: dict[str, str] = field(default_factory=dict)
    # Artifact 版本化存储 (阶段 4)。None 时工具退化为工作区文件。
    artifact_store: Any = None
    # Skill 目录 (阶段 5)。None 时 search_skills/skill 返回未配置。
    skill_catalog: Any = None
    # host 对象 (阶段 6, 给 python kernel 的进程内接口)。None 时 python 代码无 host。
    host: Any = None
    # 模型实际上下文长度 (token)。Rolling Compact 按此触发 hard-wall。
    # None 时用 RollingCompactConfig.context_ceiling 兜底。
    # 重要: 必须匹配真实模型 (如 StepFun step-3.7-flash = 256000),否则 Compact 会在爆窗后才触发。
    context_window: int | None = None
    # Rolling Compact 配置 (从 Settings 传入)。None 时用默认值。
    rolling_compact_config: Any = None
    # 三层记忆系统: 存储层 + BM25 召回索引
    memory_store: Any = None
    memory_index: Any = None
