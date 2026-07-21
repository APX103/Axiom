"""Artifact 版本化工具: save_artifacts / get_artifact / list_artifacts。


与 files.py 的工作区文件工具分开: 这里走 ArtifactStore 版本化。

save_artifacts 把工作区文件提升为版本化 artifact,返回 version_id (VID)。
agent 拿到 VID 后在手稿里用 {{artifact:VID}} 引用。
"""

from __future__ import annotations

import json
from typing import Any

from operon.tools.context import ToolContext


async def save_artifacts(
    ctx: ToolContext,
    files: list[dict[str, Any]],
    version_of: str | None = None,
) -> str:
    """保存 artifact 版本。

    files: [{path, version_of?, content_type?, is_intermediate?, language?}]
    从工作区文件读取内容,版本化存储。
    """
    store = ctx.artifact_store
    if store is None:
        return "Error: artifact store not configured (versioning unavailable)"

    if ctx.frame.project_id is None:
        # Layer A.5: fallback 到默认 project (替代老的 proj_<root_frame_id> 随机生成,
        # 避免产生孤立 project。CLI / 测试场景不传 project_id 时走这里)
        from operon.db.session import DEFAULT_PROJECT_ID

        ctx.frame.project_id = DEFAULT_PROJECT_ID

    results = []
    for f in files:
        path = f.get("path")
        if not path:
            results.append({"error": "missing path"})
            continue
        ws_path = (ctx.workspace / path).resolve()
        try:
            ws_path.relative_to(ctx.workspace.resolve())
        except ValueError:
            results.append({"path": path, "error": "outside workspace"})
            continue
        if not ws_path.exists():
            results.append({"path": path, "error": "file not found"})
            continue

        content = ws_path.read_bytes()
        # 有 DB 时走 save_async (内存+文件+SQLite 三写); 否则纯内存 save
        if getattr(store, "has_db", False):
            r = await store.save_async(
                filename=f.get("filename") or ws_path.name,
                content=content,
                project_id=ctx.frame.project_id,
                root_frame_id=ctx.frame.root_frame_id,
                frame_id=ctx.frame.id,
                version_of=f.get("version_of") or version_of,
                agent_name=ctx.frame.agent_name,
                language=f.get("language"),
                content_type=f.get("content_type"),
                is_intermediate=f.get("is_intermediate", False),
                extracted_code=f.get("extracted_code"),
                dependencies=f.get("dependencies"),
            )
        else:
            r = store.save(
                filename=f.get("filename") or ws_path.name,
                content=content,
                project_id=ctx.frame.project_id,
                root_frame_id=ctx.frame.root_frame_id,
                frame_id=ctx.frame.id,
                version_of=f.get("version_of") or version_of,
                agent_name=ctx.frame.agent_name,
                language=f.get("language"),
                content_type=f.get("content_type"),
                is_intermediate=f.get("is_intermediate", False),
                extracted_code=f.get("extracted_code"),
                dependencies=f.get("dependencies"),
            )
        results.append(
            {
                "path": path,
                "artifact_id": r.artifact_id,
                "version_id": r.version_id,
                "version_number": r.version_number,
                "filename": r.filename,
                "is_new": r.is_new_artifact,
                "stale_base": r.stale_base,
                "marker": f"![{r.filename}]({{artifact:{r.version_id}}})",
            }
        )
        # 记到 ctx.artifacts (供前端展示)
        ctx.artifacts[r.filename] = {
            "path": path,
            "size": r.__dict__.get("size", 0),
            "frame_id": ctx.frame.id,
            "artifact_id": r.artifact_id,
            "version_id": r.version_id,
        }

    return json.dumps({"artifacts": results}, ensure_ascii=False, indent=2)


async def get_artifact(ctx: ToolContext, version_id: str) -> str:
    """按 VID 读 artifact 内容。"""
    store = ctx.artifact_store
    if store is None:
        return "Error: artifact store not configured"
    ver = store.get_version(version_id)
    if ver is None:
        return f"Error: version '{version_id}' not found"
    data = store.read(version_id)
    if data is None:
        return f"Error: content for '{version_id}' missing"
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return f"[binary artifact {ver.filename}, {ver.size_bytes} bytes, type={ver.content_type}]"


async def list_artifacts(ctx: ToolContext) -> str:
    """列出会话的所有 artifact。"""
    store = ctx.artifact_store
    if store is None:
        return "Error: artifact store not configured"
    if ctx.frame.project_id is None:
        return "(no artifacts)"
    arts = store.list_artifacts(ctx.frame.project_id)
    if not arts:
        return "(no artifacts)"
    lines = []
    for a in arts:
        latest = store.get_version(a.latest_version_id) if a.latest_version_id else None
        n = len(store.list_versions(a.id))
        vstr = f"v{latest.version_number}" if latest else "?"
        lines.append(f"  {a.filename} [{a.id[:8]}] {vstr} ({n} versions)")
    return "\n".join(lines)


SAVE_ARTIFACTS_SPEC = {
    "name": "save_artifacts",
    "description": (
        "Promote workspace files into versioned artifacts. Returns version_id (VID) for each. "
        "Use the VID in manuscripts as ![caption]({{artifact:VID}}) to embed. "
        "Supports version_of (artifact_id or version_id) to append a new version."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Workspace file path"},
                        "filename": {
                            "type": "string",
                            "description": "Artifact filename (default: basename)",
                        },
                        "version_of": {
                            "type": "string",
                            "description": "Append to this artifact/version",
                        },
                        "language": {"type": "string"},
                        "is_intermediate": {"type": "boolean"},
                    },
                },
                "description": "Files to save",
            },
        },
        "required": ["files"],
    },
}

GET_ARTIFACT_SPEC = {
    "name": "get_artifact",
    "description": "Read an artifact's content by version_id (VID).",
    "parameters": {
        "type": "object",
        "properties": {"version_id": {"type": "string", "description": "Version ID (VID)"}},
        "required": ["version_id"],
    },
}

LIST_ARTIFACTS_SPEC = {
    "name": "list_artifacts",
    "description": "List all versioned artifacts in the session.",
    "parameters": {"type": "object", "properties": {}},
}
