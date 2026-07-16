"""LaTeX 编译工具: compile_pdf。

用 Tectonic (自包含 TeX 引擎) 把 .tex 编译成 PDF。
Tectonic 自动处理 bibtex + 多趟编译, 且按需下载缺失宏包,
不需要用户预装完整 TeX Live。

找不到 tectonic 时返回清晰的安装提示。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from operon.tools.context import ToolContext


def _find_tectonic() -> str | None:
    """定位 tectonic 可执行文件: PATH 优先, 回退常见安装位置。"""
    # 1. PATH (brew install / 手动装)
    t = shutil.which("tectonic")
    if t:
        return t
    # 2. brew Apple Silicon 默认路径
    for candidate in (
        "/opt/homebrew/bin/tectonic",
        "/usr/local/bin/tectonic",
        Path.home() / ".cargo/bin/tectonic",
    ):
        p = Path(candidate)
        if p.is_file():
            return str(p)
    return None


async def compile_pdf(
    ctx: ToolContext,
    path: str,
    out_name: str | None = None,
) -> str:
    """把工作区里的 .tex 编译成 PDF (用 Tectonic)。

    path: .tex 文件相对工作区的路径 (如 "main.tex" 或 "survey/main.tex")。
    out_name: 输出 PDF 文件名 (不含路径; 默认用 .tex 的 basename)。
              生成的 PDF 与 .tex 同目录。

    Tectonic 会自动跑 bibtex + 多趟编译, 缺失的宏包首次会联网下载。
    需要 tectonic 可执行 (brew install tectonic, 或 https://tectonic-typesetting.github.io)。
    """
    # 解析 .tex 路径 + 防穿越
    tex_path = (ctx.workspace / path).resolve()
    try:
        tex_path.relative_to(ctx.workspace.resolve())
    except ValueError:
        return f"Error: path '{path}' is outside workspace"

    if not tex_path.exists():
        return f"Error: tex file '{path}' not found"
    if tex_path.suffix.lower() != ".tex":
        return f"Error: '{path}' is not a .tex file"

    # 定位 tectonic
    tectonic = _find_tectonic()
    if not tectonic:
        return (
            "Error: tectonic (LaTeX engine) not found.\n"
            "Install it to compile .tex → PDF:\n"
            "  macOS:   brew install tectonic\n"
            "  Linux:   see https://tectonic-typesetting.github.io/en/installation/\n"
            "  Windows: see https://tectonic-typesetting.github.io/en/installation/\n"
            "Tectonic is self-contained (~30MB) and auto-downloads missing packages."
        )

    work_dir = tex_path.parent
    pdf_name = (out_name or tex_path.stem) + ".pdf"
    pdf_path = work_dir / pdf_name

    # tectonic -X compile main.tex
    # --keep-logs 保留日志便于排错; --keep-intermediates 保留 .aux 等 (bibtex 需要)
    cmd = [
        tectonic, "-X", "compile",
        "--keep-logs",
        "--keep-intermediates",
        str(tex_path.name),
    ]

    try:
        # Tectonic 首次编译会下载宏包 (可能几分钟), 给足超时
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except TimeoutError:
        return (
            f"Error: tectonic timed out after 300s compiling {path}.\n"
            "(First compile downloads packages and can be slow; retry.)"
        )
    except FileNotFoundError:
        return f"Error: failed to launch tectonic at {tectonic}"

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[-1500:] if stderr else ""
        log_hint = ""
        log_path = work_dir / (tex_path.stem + ".log")
        if log_path.exists():
            log_hint = f"\n\nFull log: {log_path.relative_to(ctx.workspace.resolve())}"
        return (
            f"Error: tectonic failed (exit {proc.returncode}) compiling {path}.\n"
            f"{err}{log_hint}"
        )

    if not pdf_path.exists():
        return f"Error: tectonic reported success but no PDF found at {pdf_path}"

    rel_pdf = pdf_path.relative_to(ctx.workspace.resolve())
    size_kb = pdf_path.stat().st_size / 1024
    # 记录到 artifacts (与 write_file 一致, 供版本化/前端展示)
    ctx.artifacts[str(rel_pdf)] = {
        "path": str(pdf_path),
        "size": pdf_path.stat().st_size,
        "frame_id": ctx.frame.id,
    }
    return (
        f"Compiled {path} → {rel_pdf} ({size_kb:.0f} KB).\n"
        f"The PDF is in the workspace; the user can open/download it."
    )


COMPILE_PDF_SPEC = {
    "name": "compile_pdf",
    "description": (
        "Compile a .tex file into a PDF using the Tectonic engine. "
        "Automatically runs bibtex and multiple passes; downloads missing "
        "LaTeX packages on first use. Use after writing main.tex + references.bib "
        "to produce a viewable PDF. Requires tectonic installed "
        "(brew install tectonic)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .tex file (relative to workspace).",
            },
            "out_name": {
                "type": "string",
                "description": "Output PDF filename without path (default: tex stem).",
            },
        },
        "required": ["path"],
    },
}
