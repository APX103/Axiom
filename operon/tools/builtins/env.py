"""工作区隔离 venv 管理 + install_packages 工具。


本阶段用 uv 在 workspace 内建隔离 venv,解决可复现性:
- agent 装的依赖留在 workspace/.venv,产物自带环境
- python 工具注入该 venv 的 site-packages
- bash 工具的 PATH 前置该 venv

解决原 bug: agent 之前装依赖到 operon 进程的 venv,换环境就跑不起来。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from operon.tools.context import ToolContext


def venv_dir(workspace: Path) -> Path:
    return workspace / ".venv"


def venv_python(workspace: Path) -> Path:
    return venv_dir(workspace) / "bin" / "python"


async def ensure_venv(ctx: ToolContext) -> Path:
    """确保工作区 venv 存在 (用 uv 建)。返回 python 路径。

    懒初始化: 第一次调用时才建。后续直接返回。
    """
    workspace = ctx.workspace
    py = venv_python(workspace)
    if py.exists():
        ctx.venv_python = py
        return py

    venv = venv_dir(workspace)
    # 优先 uv (快),回退 venv
    uv = shutil.which("uv")
    if uv:
        proc = await asyncio.create_subprocess_exec(
            uv, "venv", str(venv), "--python", "3.11",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )
        await proc.communicate()
    else:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "venv", str(venv),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )
        await proc.communicate()

    if py.exists():
        ctx.venv_python = py
        # 写一个 requirements.txt 占位 (agent 可追加)
        req = workspace / "requirements.txt"
        if not req.exists():
            req.write_text("# Dependencies installed by operon agent\n", encoding="utf-8")
    return py


async def install_packages(ctx: ToolContext, packages: str | list[str]) -> str:
    """在工作区 venv 里装包。


    用 uv pip install (快); 回退到 venv 的 pip。
    同时写入 requirements.txt 保证可复现。
    """
    if isinstance(packages, str):
        pkgs = [p.strip() for p in packages.replace(",", "\n").splitlines() if p.strip()]
    else:
        pkgs = list(packages)
    if not pkgs:
        return "No packages specified"

    py = await ensure_venv(ctx)
    workspace = ctx.workspace

    # 优先 uv pip (装到当前 venv)
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--python", str(py), *pkgs]
    else:
        cmd = [str(py.parent / "pip"), "install", *pkgs]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if proc.returncode != 0:
        return f"[exit {proc.returncode}]\n{out}\n[stderr]\n{err}"

    # 追加到 requirements.txt (去重)
    req = workspace / "requirements.txt"
    existing = req.read_text(encoding="utf-8") if req.exists() else ""
    existing_pkgs = {
        line.strip().split("=")[0].lower()
        for line in existing.splitlines()
        if line.strip() and not line.startswith("#")
    }
    added = []
    for p in pkgs:
        name = p.split("=")[0].split(">")[0].split("<")[0].strip().lower()
        if name and name not in existing_pkgs:
            existing_pkgs.add(name)
            added.append(p)
    if added:
        with open(req, "a", encoding="utf-8") as f:
            for p in added:
                f.write(p + "\n")

    return (
        f"Installed {len(pkgs)} package(s) into workspace .venv: {', '.join(pkgs)}\n"
        f"{out[-200:] if out else ''}"
    ).strip()


INSTALL_PACKAGES_SPEC = {
    "name": "install_packages",
    "description": (
        "Install Python packages into the workspace's isolated venv (.venv). "
        "Use this BEFORE importing third-party libs (numpy/pandas/matplotlib...) in python code. "
        "Installed packages persist and are recorded in requirements.txt for reproducibility."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Package names (e.g. ['numpy', 'pandas>=2.0', 'matplotlib'])",
            },
        },
        "required": ["packages"],
    },
}
