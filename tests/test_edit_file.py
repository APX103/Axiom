"""edit_file + python CWD 修复测试。

对照原版 0804.js:11 edit_file (精确替换,old_string 唯一匹配)。
+ python 工具 CWD=workspace 修复验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operon.frames.service import FrameService
from operon.tools.builtins import edit_file as edit_mod
from operon.tools.builtins import exec as exec_mod
from operon.tools.context import ToolContext


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    svc = FrameService()
    frame = svc.create_root_frame()
    return ToolContext(frame=frame, frame_service=svc, workspace=tmp_path.resolve())


# ---------- edit_file ----------


@pytest.mark.asyncio
async def test_edit_file_basic(ctx, tmp_path):
    f = tmp_path / "x.py"
    f.write_text("def foo():\n    return 1\n")
    result = await edit_mod.edit_file(ctx, "x.py", "return 1", "return 2")
    assert "replaced 1" in result
    assert f.read_text() == "def foo():\n    return 2\n"


@pytest.mark.asyncio
async def test_edit_file_not_found(ctx, tmp_path):
    (tmp_path / "x.py").write_text("hello")
    result = await edit_mod.edit_file(ctx, "x.py", "nonexistent", "x")
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_edit_file_ambiguous(ctx, tmp_path):
    (tmp_path / "x.py").write_text("a a a")
    result = await edit_mod.edit_file(ctx, "x.py", "a", "b")
    assert "3 times" in result  # 歧义,拒绝


@pytest.mark.asyncio
async def test_edit_file_replace_all(ctx, tmp_path):
    (tmp_path / "x.py").write_text("a a a")
    result = await edit_mod.edit_file(ctx, "x.py", "a", "b", replace_all=True)
    assert "replaced 3" in result
    assert (tmp_path / "x.py").read_text() == "b b b"


@pytest.mark.asyncio
async def test_edit_file_outside_workspace(ctx, tmp_path):
    result = await edit_mod.edit_file(ctx, "../escape.txt", "a", "b")
    assert "outside workspace" in result


@pytest.mark.asyncio
async def test_edit_file_identical(ctx, tmp_path):
    (tmp_path / "x.py").write_text("a")
    result = await edit_mod.edit_file(ctx, "x.py", "a", "a")
    assert "identical" in result


# ---------- python CWD 修复 ----------


@pytest.mark.asyncio
async def test_python_cwd_is_workspace(ctx, tmp_path):
    """python 工具的 CWD 应是工作区,不是进程 CWD。

    原 bug: 进程内 exec 继承 operon 进程 CWD,导致 agent 脚本相对路径错误。
    """
    (tmp_path / "marker.txt").write_text("found!")
    result = await exec_mod.python(ctx, "import os; print(os.getcwd()); print(open('marker.txt').read())")
    assert str(tmp_path.resolve()) in result or "found!" in result
    assert "found!" in result


@pytest.mark.asyncio
async def test_python_writes_relative_path(ctx, tmp_path):
    """python 工具应该能用相对路径写文件到工作区。"""
    await exec_mod.python(ctx, "open('out.txt','w').write('ok')")
    assert (tmp_path / "out.txt").read_text() == "ok"


@pytest.mark.asyncio
async def test_python_namespace_persists(ctx, tmp_path):
    """python 工具的变量应跨调用持久。"""
    await exec_mod.python(ctx, "x = 42")
    result = await exec_mod.python(ctx, "print(x * 2)")
    assert "84" in result
