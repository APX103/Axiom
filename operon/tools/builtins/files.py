"""文件工具: read_file / write_file / list_files。


本阶段: 工作区文件读写,无 artifact 版本化 (阶段 4 接)。
PDF 走文本提取 (divergences §4),首版支持 .txt/.md/.py/.json 等。
"""

from __future__ import annotations

from pathlib import Path

from operon.tools.context import ToolContext


async def read_file(ctx: ToolContext, path: str, offset: int = 0, limit: int = 2000) -> str:
    """读取工作区文件。


    PDF 后置 (divergences §4: 强制文本路径)。
    """
    p = (ctx.workspace / path).resolve()
    # 防路径穿越
    try:
        p.relative_to(ctx.workspace.resolve())
    except ValueError:
        return f"Error: path '{path}' is outside workspace"

    if not p.exists():
        return f"Error: file '{path}' not found"
    if not p.is_file():
        return f"Error: '{path}' is not a file"

    # PDF 文本提取 (divergences §4)
    if p.suffix.lower() == ".pdf":
        return await _extract_pdf_text(p)

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[offset : offset + limit]
    result = "\n".join(selected)
    if offset + limit < len(lines):
        result += f"\n\n[... {len(lines) - offset - limit} more lines, use offset to continue]"
    return result


async def write_file(ctx: ToolContext, path: str, content: str) -> str:
    """写入工作区文件。


    artifact 版本化在阶段 4 接。
    """
    p = (ctx.workspace / path).resolve()
    try:
        p.relative_to(ctx.workspace.resolve())
    except ValueError:
        return f"Error: path '{path}' is outside workspace"

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    # 记录到 artifacts (供阶段 4 版本化接入)
    ctx.artifacts[path] = {
        "path": str(p),
        "size": len(content),
        "frame_id": ctx.frame.id,
    }
    return f"Wrote {len(content)} bytes to {path}"


async def list_files(ctx: ToolContext, path: str = ".") -> str:
    """列出工作区文件。"""
    p = (ctx.workspace / path).resolve()
    try:
        p.relative_to(ctx.workspace.resolve())
    except ValueError:
        return f"Error: path '{path}' is outside workspace"
    if not p.exists():
        return f"Error: '{path}' not found"
    if p.is_file():
        return str(p.relative_to(ctx.workspace))

    entries = []
    for child in sorted(p.iterdir()):
        rel = child.relative_to(ctx.workspace)
        kind = "dir" if child.is_dir() else f"{child.stat().st_size}B"
        entries.append(f"{kind:>8}  {rel}")
    return "\n".join(entries) if entries else "(empty)"


async def _extract_pdf_text(pdf_path: Path) -> str:
    """PDF 文本提取。对应 pdf-explore 路径 (divergences §4)。"""
    try:
        import pypdfium2  # type: ignore
    except ImportError:
        return "Error: pypdfium2 not installed (PDF text extraction unavailable)"

    def _run() -> str:
        pdf = pypdfium2.PdfDocument(str(pdf_path))
        parts = []
        for i, page in enumerate(pdf):
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            parts.append(f"--- Page {i + 1}/{len(pdf)} ---\n{text}")
            textpage.close()
            page.close()
        pdf.close()
        return "\n\n".join(parts)

    import asyncio

    return await asyncio.to_thread(_run)


READ_FILE_SPEC = {
    "name": "read_file",
    "description": (
        "Read a text file from the workspace (txt/md/py/json/csv...). "
        "PDF extracts text via pypdfium2."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
            "offset": {"type": "integer", "description": "Line offset to start (default 0)"},
            "limit": {"type": "integer", "description": "Max lines to read (default 2000)"},
        },
        "required": ["path"],
    },
}

WRITE_FILE_SPEC = {
    "name": "write_file",
    "description": "Write content to a file in the workspace (creates/overwrites).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
}

LIST_FILES_SPEC = {
    "name": "list_files",
    "description": "List files in a workspace directory.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory path (default '.')"}},
    },
}
