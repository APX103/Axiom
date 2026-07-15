"""把百篇综述调研 (.tex + .bib) 入库为 ArtifactStore 版本化产物。

阶段 4 落库的示例入口: 读 articles/survey-baijian-zongshu/{main.tex, references.bib},
用带 SQLite 的 ArtifactStore 存成 artifact (含版本元数据 + DAG), 同时把文件 copy
到目标 workspace, 使前端 PaperView (扫 workspace 磁盘) 能渲染 + 下载。

用法 (程序内):
    from operon.scripts.ingest_survey import ingest_survey
    result = await ingest_survey(workspace=Path("./demo_ws"), db_url=...)
    # result = {tex_vid, bib_vid, artifact_ids, workspace, project_id}

命令行: operon demo-survey (见 cli/main.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

# 固定的 project_id (跨重启稳定, 便于 demo-survey 复用同一份产物)
PROJECT_ID = "survey-baijian-zongshu"
ROOT_FRAME_ID = "frame_survey_root"

# 调研源文件目录 (打包在 operon-py/articles/)
def _article_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "articles" / "survey-baijian-zongshu"


async def ingest_article(
    *,
    src_dir: Path,
    workspace: Path,
    db_session_factory: Any,
    project_id: str,
    root_frame_id: str = "frame_article_root",
    agent_name: str = "ingest_article",
) -> dict[str, Any]:
    """通用: 把一个文章目录的 main.tex + references.bib 入库 + copy 到 workspace。

    Args:
        src_dir: 文章源目录 (含 main.tex, 可选 references.bib)
        workspace: 目标工作区 (PaperView 扫这里)
        db_session_factory: SQLAlchemy async_sessionmaker (落库用)
        project_id: artifact 的 project_id
    Returns:
        {tex_version_id, bib_version_id, artifact_ids, files, workspace, project_id}
    """
    from operon.artifacts.store import ArtifactStore

    tex_src = src_dir / "main.tex"
    bib_src = src_dir / "references.bib"
    if not tex_src.exists():
        raise FileNotFoundError(f"文章源文件不存在: {tex_src}")

    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    # 1. copy 到 workspace (PaperView 扫磁盘, 必须在 workspace 里)
    tex_dst = workspace / "main.tex"
    bib_dst = workspace / "references.bib"
    shutil.copy2(tex_src, tex_dst)
    if bib_src.exists():
        shutil.copy2(bib_src, bib_dst)

    # 2. 用带 DB 的 ArtifactStore 版本化存储
    store = ArtifactStore(workspace, db_session_factory=db_session_factory)
    # 启动时先回放 (幂等: 重复入库不重复建 artifact, 因 by_filename 命中)
    await store.load_from_db(project_id=project_id)

    tex_result = await store.save_async(
        filename="main.tex",
        content=tex_src.read_bytes(),
        project_id=project_id,
        root_frame_id=root_frame_id,
        frame_id=root_frame_id,
        agent_name=agent_name,
        language="latex",
    )
    files_info: list[dict[str, str]] = [
        {
            "path": "main.tex",
            "version_id": tex_result.version_id,
            "artifact_id": tex_result.artifact_id,
        }
    ]
    bib_vid = None
    if bib_src.exists():
        bib_result = await store.save_async(
            filename="references.bib",
            content=bib_src.read_bytes(),
            project_id=project_id,
            root_frame_id=root_frame_id,
            frame_id=root_frame_id,
            agent_name=agent_name,
            dependencies=[{"version_id": tex_result.version_id, "ref_name": "tex"}],
        )
        bib_vid = bib_result.version_id
        files_info.append(
            {
                "path": "references.bib",
                "version_id": bib_result.version_id,
                "artifact_id": bib_result.artifact_id,
            }
        )

    return {
        "tex_version_id": tex_result.version_id,
        "bib_version_id": bib_vid,
        "artifact_ids": [f["artifact_id"] for f in files_info],
        "files": files_info,
        "workspace": str(workspace),
        "project_id": project_id,
        "tex_is_new": tex_result.is_new_artifact,
    }


async def ingest_survey(
    *,
    workspace: Path,
    db_session_factory: Any,
    project_id: str = PROJECT_ID,
    overwrite: bool = True,
) -> dict[str, Any]:
    """百篇综述调研入库 (向后兼容; 内部调 ingest_article)。"""
    return await ingest_article(
        src_dir=_article_dir(),
        workspace=workspace,
        db_session_factory=db_session_factory,
        project_id=project_id,
        root_frame_id=ROOT_FRAME_ID,
        agent_name="ingest_survey",
    )
