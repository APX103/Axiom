"""LaTeX 编译工具: compile_pdf。

用 Tectonic (自包含 TeX 引擎) 把 .tex 编译成 PDF。
Tectonic 自动处理 bibtex + 多趟编译, 且按需下载缺失宏包,
不需要用户预装完整 TeX Live。

找不到 tectonic 时返回清晰的安装提示。
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
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
    result = await compile_tex(ctx.workspace, path, out_name=out_name)
    if result.success:
        # 记录到 artifacts (与 write_file 一致, 供版本化/前端展示)
        ctx.artifacts[result.rel_pdf] = {
            "path": str(result.pdf_path),
            "size": result.pdf_path.stat().st_size,
            "frame_id": ctx.frame.id,
        }
        return (
            f"Compiled {path} → {result.rel_pdf} ({result.size_kb:.0f} KB).\n"
            f"The PDF is in the workspace; the user can open/download it."
        )
    return f"Error: {result.message}"


@dataclass
class CompileResult:
    """编译结果 (结构化, 供 HTTP 端点和 tool 共用)。"""

    success: bool
    pdf_path: Path  # 编译成功时的 PDF 绝对路径 (失败时为占位)
    rel_pdf: str  # 相对工作区的 PDF 路径 (失败时为 "")
    size_kb: float
    message: str  # 人类可读信息/错误
    errors: list[str]  # 从 .log 解析出的错误行 (带行号)
    log_excerpt: str  # 日志尾部摘录


def _parse_log_errors(log_text: str) -> list[str]:
    """从 tectonic .log 提取错误行 (TeX 风格 `! ...` + 行号 `l.NN ...`)。"""
    errors: list[str] = []
    lines = log_text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("!") and len(s) > 2:
            # 尝试带上下一行的行号定位 (l.221 ...)
            ctx_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if ctx_line.startswith("l."):
                errors.append(f"{s}  [{ctx_line}]")
            else:
                errors.append(s)
    return errors


async def compile_tex(
    workspace: Path,
    path: str,
    *,
    out_name: str | None = None,
    skip_failing_envs: bool = True,
) -> CompileResult:
    """编译工作区内 .tex → PDF (核心逻辑, 不依赖 ToolContext)。

    skip_failing_envs: 首次失败时, 若错误来自 tikzpicture/algorithm/figure 等环境,
        临时把该环境包进 \\iffalse...\\fi 重编译一次, 保证正文 PDF 能出来。
    """
    ws = workspace.resolve()
    # 解析 .tex 路径 + 防穿越
    tex_path = (ws / path).resolve()
    try:
        tex_path.relative_to(ws)
    except ValueError:
        return _fail(workspace, path, f"path '{path}' is outside workspace")

    if not tex_path.exists():
        return _fail(workspace, path, f"tex file '{path}' not found")
    if tex_path.suffix.lower() != ".tex":
        return _fail(workspace, path, f"'{path}' is not a .tex file")

    tectonic = _find_tectonic()
    if not tectonic:
        return _fail(
            workspace,
            path,
            "tectonic (LaTeX engine) not found. Install: macOS `brew install tectonic`, "
            "or see https://tectonic-typesetting.github.io/en/installation/",
        )

    result = await _run_tectonic(tectonic, tex_path, ws, out_name)
    if result.success or not skip_failing_envs:
        return result

    # 容错: 解析失败原因, 若是某个浮动环境 (tikzpicture/algorithm/figure/table) 致命,
    # 把该环境临时注释后重编译, 至少让正文 PDF 出来。
    failing = _find_failing_env(tex_path, result.errors)
    if failing is None:
        return result  # 不是可跳过的环境错误, 原样返回

    skipped = await _retry_skipping_env(tectonic, tex_path, ws, out_name, failing)
    if skipped is not None and skipped.success:
        skipped.message = (
            f"Compiled with fallback (skipped failing {failing} environment to get body out). "
            + skipped.message
        )
        return skipped
    return result


async def _run_tectonic(
    tectonic: str,
    tex_path: Path,
    ws: Path,
    out_name: str | None,
) -> CompileResult:
    """单次 tectonic 编译, 返回结构化结果。"""
    work_dir = tex_path.parent
    pdf_name = (out_name or tex_path.stem) + ".pdf"
    pdf_path = work_dir / pdf_name

    cmd = [
        tectonic, "-X", "compile",
        "--keep-logs",
        "--keep-intermediates",
        str(tex_path.name),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except TimeoutError:
        return _fail(ws, str(tex_path.relative_to(ws)), "tectonic timed out after 300s (first compile downloads packages; retry)")
    except FileNotFoundError:
        return _fail(ws, str(tex_path.relative_to(ws)), f"failed to launch tectonic at {tectonic}")

    log_path = work_dir / (tex_path.stem + ".log")
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    stderr_text = stderr.decode(errors="replace") if stderr else ""
    rel = str(tex_path.relative_to(ws))

    if proc.returncode != 0:
        errors = _parse_log_errors(log_text) if log_text else []
        excerpt = (stderr_text + "\n" + log_text)[-2000:] if (stderr_text or log_text) else ""
        return CompileResult(
            success=False,
            pdf_path=pdf_path,
            rel_pdf="",
            size_kb=0.0,
            message=f"tectonic failed (exit {proc.returncode}) compiling {rel}",
            errors=errors,
            log_excerpt=excerpt.strip(),
        )

    if not pdf_path.exists():
        return _fail(ws, rel, "tectonic reported success but no PDF was produced")

    rel_pdf = str(pdf_path.relative_to(ws))
    size_kb = pdf_path.stat().st_size / 1024
    return CompileResult(
        success=True,
        pdf_path=pdf_path,
        rel_pdf=rel_pdf,
        size_kb=size_kb,
        message=f"compiled {rel} → {rel_pdf} ({size_kb:.0f} KB)",
        errors=[],
        log_excerpt="",
    )


# 可容错跳过的浮动环境 (跳过它们不影响正文文字)
_SKIP_ENVS = ("tikzpicture", "algorithm", "algorithmic", "figure", "table", "verbatim", "lstlisting")

# 错误关键词 → 应跳过的环境。这些错误根因在某个浮动环境里, 但错误信息
# 不一定含环境名本身 (如 "PGF Math Error" 来自 tikzpicture, "Algpseudocode" 来自 algorithm)。
_ERROR_ENV_HINTS = (
    (("pgf", "tikz", "unknown function", "of root", "\\node", "\\draw"), "tikzpicture"),
    (("algpseudocode", "algorithmic", "\\state", "\\ensuremath"), "algorithm"),
)


def _find_failing_env(tex_path: Path, errors: list[str]) -> str | None:
    """根据错误信息 + 文件内容判断是否是某个可跳过的环境导致的失败。

    优先看错误里直接出现的环境名; 再用关键词推断 (PGF/tikz → tikzpicture)。
    最后确认文件里确实有该环境 (否则跳了也没用)。
    """
    blob = " ".join(errors).lower()
    # 1. 错误信息里直接点名某环境
    for env in _SKIP_ENVS:
        if env in blob:
            return env
    # 2. 关键词推断
    original = tex_path.read_text(encoding="utf-8", errors="replace")
    for keywords, env in _ERROR_ENV_HINTS:
        if any(kw in blob for kw in keywords) and f"\\begin{{{env}}}" in original:
            return env
    return None


async def _retry_skipping_env(
    tectonic: str,
    tex_path: Path,
    ws: Path,
    out_name: str | None,
    env: str,
) -> CompileResult | None:
    """把 .tex 里所有 \\begin{env}...\\end{env} 块包进 \\iffalse...\\fi, 重编译。

    在临时副本上操作, 不改原文件。失败返回 None (调用方用原结果)。
    """
    import re
    import tempfile

    original = tex_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        rf"(\\begin\{{{env}\}}[\s\S]*?\\end\{{{env}\}})", re.MULTILINE
    )
    patched = pattern.sub(r"\\iffalse \1 \\fi", original)
    if patched == original:
        return None  # 没匹配到, 无法跳过

    with tempfile.TemporaryDirectory(dir=str(tex_path.parent)) as td:
        tmp_tex = Path(td) / tex_path.name
        tmp_tex.write_text(patched, encoding="utf-8")
        # 临时文件用同名, tectonic 输出 PDF 也在临时目录
        result = await _run_tectonic(tectonic, tmp_tex, ws, out_name)
        if result.success:
            # 把 PDF 从临时目录拷回原 tex 所在目录, 让用户拿得到
            import shutil

            final_pdf = tex_path.parent / result.pdf_path.name
            shutil.copyfile(result.pdf_path, final_pdf)
            result.pdf_path = final_pdf
            result.rel_pdf = str(final_pdf.relative_to(ws))
        return result


def _fail(workspace: Path, path: str, message: str) -> CompileResult:
    return CompileResult(
        success=False,
        pdf_path=workspace / path,
        rel_pdf="",
        size_kb=0.0,
        message=message,
        errors=[],
        log_excerpt="",
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
