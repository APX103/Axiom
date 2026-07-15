"""文件编辑工具: edit_file。

对应原版: 0804.js:11 edit_file (原子替换, old_string→new_string)。
原版语义: old_string 必须在文件中唯一匹配,否则失败 (防歧义)。
这是 agent 精确修改文件的标准工具 (而非整文件重写)。
"""

from __future__ import annotations

from operon.tools.context import ToolContext


async def edit_file(
    ctx: ToolContext,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """精确字符串替换编辑。

    对应原版 edit_file (0804.js:11): old_string→new_string 原子替换。
    - old_string 必须在文件中存在
    - 默认必须唯一匹配 (replace_all=False),否则报错防歧义
    - 替换失败时文件不变 (原子性)
    """
    p = (ctx.workspace / path).resolve()
    try:
        p.relative_to(ctx.workspace.resolve())
    except ValueError:
        return f"Error: path '{path}' is outside workspace"

    if not p.exists():
        return f"Error: file '{path}' not found"
    if not p.is_file():
        return f"Error: '{path}' is not a file"

    content = p.read_text(encoding="utf-8")

    if old_string == new_string:
        return "Error: old_string and new_string are identical (nothing to do)"

    count = content.count(old_string)
    if count == 0:
        return (
            f"Error: old_string not found in '{path}'. "
            "Make sure old_string matches the file exactly (including whitespace/indentation)."
        )
    if count > 1 and not replace_all:
        return (
            f"Error: old_string appears {count} times in '{path}' (must be unique). "
            "Pass replace_all=true to replace all occurrences, or include more surrounding context."
        )

    if replace_all:
        new_content = content.replace(old_string, new_string)
        replaced = count
    else:
        new_content = content.replace(old_string, new_string, 1)
        replaced = 1

    p.write_text(new_content, encoding="utf-8")
    # 更新 artifacts 记录
    ctx.artifacts[path] = {
        "path": str(p),
        "size": len(new_content),
        "frame_id": ctx.frame.id,
    }
    return f"Edited {path}: replaced {replaced} occurrence(s), file now {len(new_content)} bytes"


EDIT_FILE_SPEC = {
    "name": "edit_file",
    "description": (
        "Make a precise string-replacement edit to an existing file. "
        "old_string must match the file exactly (including indentation/whitespace) and be unique "
        "unless replace_all=true. Fails atomically if old_string is missing or ambiguous. "
        "Prefer this over write_file for targeted edits."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
            "old_string": {"type": "string", "description": "Exact text to find (must match whitespace)"},
            "new_string": {"type": "string", "description": "Text to replace it with"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false; requires unique match)",
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
}
