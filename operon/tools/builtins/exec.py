"""代码执行工具: python / bash。


原版在沙箱 conda env 里执行;本阶段用进程内 exec (python) / subprocess (bash)。
沙箱化在阶段后续接 (sandbox.py 接口已预留)。

python 工具: 进程内 exec,捕获 stdout/stderr/异常,保留 namespace 跨调用。
bash 工具: subprocess,捕获输出,有超时。
"""

from __future__ import annotations

import asyncio
import io
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from operon.tools.context import ToolContext

# 跨调用保留的 Python namespace (模拟原版持久 kernel)
_PYTHON_NS: dict[str, Any] = {}


async def python(ctx: ToolContext, code: str) -> str:
    """执行 Python 代码。


    本阶段用进程内 exec + 全局 namespace (无隔离,沙箱后置)。

    CWD = 工作区 (ctx.workspace): 让脚本里的相对路径 (open('output/...')) 正确解析。
    venv: 若 ctx.venv_python 设置,把工作区 venv 的 site-packages 注入 sys.path,
          让 agent 装的依赖 (pandas 等) 能被 import。
    """
    workspace = str(ctx.workspace)
    venv_python = ctx.venv_python
    host = getattr(ctx, "host", None)

    def _run() -> str:
        import os

        prev_cwd = os.getcwd()
        added_paths: list[str] = []
        os.chdir(workspace)
        # 注入工作区 venv 的 site-packages (让 agent 装的依赖可见)
        if venv_python is not None:
            for sp in _venv_site_packages(venv_python):
                if sp not in sys.path:
                    sys.path.insert(0, sp)
                    added_paths.append(sp)
        # 注入 host 对象到 namespace (让 agent 代码能调 host.llm/host.lineage 等)
        if host is not None:
            _PYTHON_NS["host"] = host
            _PYTHON_NS["operon"] = host  # legacy 别名
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    exec(compile(code, "<python>", "exec"), _PYTHON_NS)  # noqa: S102
                except Exception:
                    traceback.print_exc(file=err)
            result = out.getvalue()
            errstr = err.getvalue()
            if errstr:
                return (result + "\n[stderr]\n" + errstr) if result else errstr
            return result or "(no output)"
        finally:
            os.chdir(prev_cwd)
            # 清理注入的 path (避免跨会话污染)
            for sp in added_paths:
                if sp in sys.path:
                    sys.path.remove(sp)

    return await asyncio.to_thread(_run)


def _venv_site_packages(venv_python: Any) -> list[str]:
    """工作区 venv 的 site-packages 目录列表。"""
    import subprocess

    try:
        out = subprocess.check_output(
            [str(venv_python), "-c", "import site, sys; print('\\n'.join(site.getsitepackages() + [site.getusersitepackages()]))"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [p for p in out.strip().splitlines() if p]
    except Exception:
        return []


async def bash(ctx: ToolContext, command: str, timeout: int = 30) -> str:
    """执行 bash 命令。


    cwd=workspace; 若工作区有 venv, PATH 前置 venv/bin (让 python/pip 指向工作区 venv)。
    """
    import os

    env = os.environ.copy()
    # PATH 前置工作区 venv (若有),让 `python` 指向工作区隔离环境
    venv_bin = ctx.workspace / ".venv" / "bin"
    if venv_bin.exists():
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(ctx.workspace / ".venv")
        # 让 python 子进程也能找到 venv 的包
        py_path = venv_bin / "python"
        if py_path.exists():
            env.pop("PYTHONPATH", None)

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(ctx.workspace),
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Error: command timed out after {timeout}s\ncommand: {command}"

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if proc.returncode != 0:
        return f"[exit {proc.returncode}]\n{out}\n[stderr]\n{err}"
    if err:
        return out + ("\n[stderr]\n" + err if err else "")
    return out or "(no output)"


# 工具规格 (给 registry 用)
PYTHON_SPEC = {
    "name": "python",
    "description": (
        "Execute Python code in a persistent kernel. Variables persist across calls. "
        "Use for data analysis, computation, file processing. Available: numpy, pandas, etc."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute"},
        },
        "required": ["code"],
    },
}

BASH_SPEC = {
    "name": "bash",
    "description": (
        "Execute a bash command in the workspace. Use for file operations, git, system tools. "
        "Has a timeout (default 30s)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Bash command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
        },
        "required": ["command"],
    },
}
