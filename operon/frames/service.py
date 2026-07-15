"""Frame 服务。

对应原版: 0125.js:55,83 的 createRootFrame/createChildFrame + 0877.js:207 FrameService。
负责 frame 的创建、状态转移、树查询。

本阶段:
- 内存态优先 (Frame 对象 + 内存索引),DB 持久化作为可选 (持久化由调用方按需开启)。
- 这样单元测试无需 DB,agent 循环也更快。
- 持久化接口 persist(frame) 留着,后续接 DB。
"""

from __future__ import annotations

from datetime import UTC, datetime

from operon.agent.states import TERMINAL, FrameStatus

from .model import Frame


class FrameService:
    """Frame 管理服务。对应原版 FrameService (0877.js:207)。

    内存态维护 frame 树,DB 持久化为可选。
    """

    def __init__(self) -> None:
        self._frames: dict[str, Frame] = {}

    def create_root_frame(
        self,
        *,
        agent_name: str = "MAIN",
        model: str | None = None,
        project_id: str | None = None,
        name: str | None = None,
    ) -> Frame:
        """创建根 frame。

        对应原版 createRootFrame (0125.js:55):
        parent_frame_id=None, root_frame_id=id, conversation_type="agent"。
        """
        frame = Frame(
            agent_name=agent_name,
            parent_frame_id=None,
            root_frame_id="",  # __post_init__ 会设为 self.id
            model=model,
            project_id=project_id,
            name=name,
        )
        self._frames[frame.id] = frame
        return frame

    def create_child_frame(
        self,
        parent_id: str,
        *,
        agent_name: str = "MAIN",
        delegate_name: str | None = None,
        model: str | None = None,
        is_hidden: bool = False,
    ) -> Frame:
        """创建子 frame。

        对应原版 createChildFrame (0125.js:83)。
        parent+root 都指向父/根。用于 delegate/reviewer/bookmarker (阶段 3+)。
        """
        parent = self._frames[parent_id]
        frame = Frame(
            parent_frame_id=parent_id,
            root_frame_id=parent.root_frame_id,
            agent_name=agent_name,
            delegate_name=delegate_name,
            model=model,
            project_id=parent.project_id,
            is_hidden=is_hidden,
        )
        self._frames[frame.id] = frame
        return frame

    def get(self, frame_id: str) -> Frame | None:
        return self._frames.get(frame_id)

    def require(self, frame_id: str) -> Frame:
        f = self._frames.get(frame_id)
        if f is None:
            raise KeyError(f"frame {frame_id} not found")
        return f

    def update_status(self, frame_id: str, status: FrameStatus) -> Frame:
        """更新 frame 状态 + 终态校验。

        对应原版 status 转移规则: 终态 frame 不可再变。
        """
        frame = self.require(frame_id)
        if frame.status in TERMINAL and status != frame.status:
            raise ValueError(
                f"frame {frame_id} is terminal ({frame.status.value}), cannot change to {status.value}"
            )
        frame.status = status
        frame.updated_at = datetime.now(UTC)
        if status in TERMINAL:
            frame.completed_at = frame.updated_at
        return frame

    def get_tree(self, root_id: str) -> list[Frame]:
        """返回 root 下所有 frame (按创建顺序)。对应原版树查询。"""
        root = self.require(root_id)
        return [f for f in self._frames.values() if f.root_frame_id == root.root_frame_id]

    def get_children(self, frame_id: str) -> list[Frame]:
        """返回直接子 frame。"""
        return [f for f in self._frames.values() if f.parent_frame_id == frame_id]
