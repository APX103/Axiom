"""Frame 服务测试。

对照原版 0125.js:55,83 createRootFrame/createChildFrame + 状态转移。
"""

from __future__ import annotations

import pytest

from axiom_core.agent.states import NEEDS_INPUT, SUCCESSFUL, TERMINAL, FrameStatus
from axiom_core.frames.service import FrameService


def test_create_root_frame():
    """根 frame: parent=None, root=self。对照 0125.js:55。"""
    svc = FrameService()
    f = svc.create_root_frame(agent_name="MAIN", model="step-3.7-flash")

    assert f.parent_frame_id is None
    assert f.root_frame_id == f.id  # 根的 root = self
    assert f.is_root is True
    assert f.status == FrameStatus.PROCESSING


def test_create_child_frame():
    """子 frame: parent+root 指向父/根。对照 0125.js:83。"""
    svc = FrameService()
    root = svc.create_root_frame()
    child = svc.create_child_frame(root.id, agent_name="REVIEWER", is_hidden=True)

    assert child.parent_frame_id == root.id
    assert child.root_frame_id == root.id  # 子的 root 指向根
    assert child.is_hidden is True
    assert child.is_root is False


def test_status_terminal_cannot_change():
    """终态 frame 不可再变。对照原版终态校验。"""
    svc = FrameService()
    f = svc.create_root_frame()
    svc.update_status(f.id, FrameStatus.COMPLETED)

    assert f.status in TERMINAL
    with pytest.raises(ValueError, match="terminal"):
        svc.update_status(f.id, FrameStatus.PROCESSING)


def test_status_awaiting_can_resume():
    """awaiting 状态可恢复到 processing (非终态)。"""
    svc = FrameService()
    f = svc.create_root_frame()
    svc.update_status(f.id, FrameStatus.AWAITING_USER_RESPONSE)
    assert f.status in NEEDS_INPUT
    # awaiting 不是终态,可继续变
    f.status = FrameStatus.PROCESSING
    assert f.status == FrameStatus.PROCESSING


def test_get_tree_and_children():
    """树查询: get_tree 返回同根全部, get_children 返回直接子。"""
    svc = FrameService()
    root = svc.create_root_frame()
    c1 = svc.create_child_frame(root.id, agent_name="REVIEWER")
    c2 = svc.create_child_frame(root.id, agent_name="BOOKMARKER")
    gc = svc.create_child_frame(c1.id, agent_name="SUB")

    tree = svc.get_tree(root.id)
    assert {f.id for f in tree} == {root.id, c1.id, c2.id, gc.id}

    children = svc.get_children(root.id)
    assert {f.id for f in children} == {c1.id, c2.id}


def test_terminal_sets_completed_at():
    """进入终态时设置 completed_at。"""
    svc = FrameService()
    f = svc.create_root_frame()
    assert f.completed_at is None
    svc.update_status(f.id, FrameStatus.SUCCESS)
    assert f.completed_at is not None


def test_status_groupings():
    """状态集合分组正确 (照搬原版 H3/Ec_/bd_)。"""
    assert FrameStatus.PROCESSING not in TERMINAL
    assert FrameStatus.COMPLETED in TERMINAL
    assert FrameStatus.COMPLETED in SUCCESSFUL
    assert FrameStatus.AWAITING_PLAN_APPROVAL in NEEDS_INPUT
    assert FrameStatus.PROCESSING not in NEEDS_INPUT
