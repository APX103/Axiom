"""boundary 工具测试。

回归: boundary 工具插入的 [boundary] 标记消息必须带 _harness_notice=True,
否则重载 session 时会被前端当成用户消息渲染出来 (见 useSession.ts 过滤器)。
"""

from __future__ import annotations

import pytest

from operon.frames.model import Frame
from operon.tools.builtins.boundary import boundary
from operon.tools.context import ToolContext


@pytest.fixture
def ctx() -> ToolContext:
    # boundary 工具只读写 ctx.frame.messages, 其余字段给占位值
    return ToolContext(
        frame=Frame(),
        frame_service=None,  # type: ignore[arg-type]
        workspace=None,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_boundary_marks_harness_notice(ctx: ToolContext):
    """boundary 消息必须带 _harness_notice=True (前端据此隐藏)。"""
    result = await boundary(ctx, label="survey writing complete")

    assert result == "Boundary marked: survey writing complete"
    assert len(ctx.frame.messages) == 1
    msg = ctx.frame.messages[0]
    assert msg.role.value == "user"
    # 关键: 必须带 _harness_notice, 否则重载时会渲染成用户气泡
    assert getattr(msg, "_harness_notice", False) is True
    # task_boundary 元数据保留, chunk.py 仍据此切分
    assert getattr(msg, "task_boundary", None) is not None
    assert "[boundary] survey writing complete" in msg.content


@pytest.mark.asyncio
async def test_boundary_default_label(ctx: ToolContext):
    """无 label 时用默认 'task transition'。"""
    await boundary(ctx)
    msg = ctx.frame.messages[0]
    assert "[boundary] task transition" in msg.content
    assert getattr(msg, "_harness_notice", False) is True
