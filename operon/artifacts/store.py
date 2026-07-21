"""Artifact 版本化存储。



三层结构 (照搬原版):
- artifacts (逻辑文件,可多版本)
- artifact_versions (物理版本,带 lineage)
- content_snapshots (内容级去重,首版用 storage_path,内容去重后置)

核心能力:
- save: version_of (artifact_id 或 version_id) 解析 + 乐观并发 stale_base
- read: 按 version_id 读
- 版本链: parent_version_id
- 依赖 DAG: artifact_dependencies (artifact 层的 DAG,不是 frame 树)
- marker: {{artifact:<VID>}} 语法 (手稿引用图表)

存储: 工作区文件 (storage_path) + 内存索引 (artifact/version 元数据)。
阶段 4 落库: 内存优先 + 可选 SQLite 持久层。
  - db_session_factory=None (缺省): 纯内存 (向后兼容现有测试/CLI run)。
  - db_session_factory 提供: save_async() 同步写 SQLite (artifacts/artifact_versions/
    artifact_dependencies), load_from_db() 启动时回放内存。
  内容本身始终在工作区文件, DB 只存元数据 + 版本链 + DAG。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return str(uuid.uuid4())


def _short(vid: str) -> str:
    return vid[:8]


@dataclass
class ArtifactRecord:
    """逻辑 artifact。"""

    id: str
    project_id: str
    root_frame_id: str
    filename: str
    frame_id: str | None = None
    latest_version_id: str | None = None
    is_user_upload: bool = False
    created_at: str = ""


@dataclass
class VersionRecord:
    """物理版本。"""

    id: str  # VID
    artifact_id: str
    version_number: int
    content_type: str
    size_bytes: int
    checksum: str
    storage_path: str  # 相对工作区
    agent_name: str | None = None
    language: str | None = None
    parent_version_id: str | None = None
    is_intermediate: bool = False
    created_at: str = ""
    # frame_id: 该版本由哪个 frame 产生。save() 一直收到但此前没存进 record (bug),
    # 落库需要它 (ArtifactVersion.frame_id 列 NOT NULL-able 为 nullable, 但应填)。
    frame_id: str | None = None
    # lineage (复现相关)
    extracted_code: str | None = None
    dependency_mappings: dict[str, Any] | None = None


@dataclass
class SaveResult:
    """save 的返回。对照原版返回对象 + stale_base。"""

    artifact_id: str
    version_id: str
    version_number: int
    filename: str
    storage_path: str
    is_new_artifact: bool
    parent_version_id: str | None = None
    size_bytes: int = 0
    checksum: str = ""
    stale_base: dict[str, Any] | None = None  # 乐观并发冲突信息
    # 本次 save 新写入的 DAG 边 [(depends_on_version_id, ref_name)] — 供 _persist 落库
    new_deps: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stale_base is None


class ArtifactStore:
    """Artifact 版本化存储。

    内存态 + 工作区文件。乐观并发用 latest_version_id 乐观锁。

    阶段 4: 可选 SQLite 持久层。db_session_factory=None 时纯内存 (向后兼容);
    提供时调 save_async() 落库, load_from_db() 回放。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        db_session_factory: Any = None,
    ):
        self.workspace = workspace.resolve()
        self._artifacts: dict[str, ArtifactRecord] = {}  # artifact_id → record
        self._versions: dict[str, VersionRecord] = {}  # version_id → record
        # (project,frame,filename) → artifact_id
        self._by_filename: dict[tuple[str, str, str], str] = {}
        # [(version_id, depends_on_version_id, ref_name)] DAG
        self._deps: list[tuple[str, str, str]] = []
        # SQLAlchemy async_sessionmaker (None=纯内存)
        self._db = db_session_factory

    def save(
        self,
        *,
        filename: str,
        content: str | bytes,
        project_id: str,
        root_frame_id: str,
        frame_id: str | None = None,
        version_of: str | None = None,
        agent_name: str | None = None,
        language: str | None = None,
        content_type: str | None = None,
        is_intermediate: bool = False,
        extracted_code: str | None = None,
        dependencies: list[dict[str, str]] | None = None,
    ) -> SaveResult:
        """保存一个版本。

        Args:
            version_of: artifact_id 或 version_id (追加版本); None=按 filename 查/建
            dependencies: [{version_id, ref_name}] 声明依赖的版本 (写 DAG)
        Returns:
            SaveResult (含 stale_base 若乐观并发冲突)
        """
        if content_type is None:
            content_type = _guess_content_type(filename)

        artifact_id: str | None = None
        parent_version_id: str | None = None
        is_new = False

        if version_of:
            # version_of 解析: 先 artifact 表,再 version 表 (对照 0187.js:55-68)
            art = self._artifacts.get(version_of)
            if art is None:
                ver = self._versions.get(version_of)
                if ver:
                    artifact_id = ver.artifact_id
                    parent_version_id = ver.id
                    art = self._artifacts.get(artifact_id)
                else:
                    raise ValueError(
                        f'version_of="{version_of}" does not match any artifact_id or version_id'
                    )
            else:
                artifact_id = art.id
                parent_version_id = art.latest_version_id
        else:
            # 按 (project, frame, filename) 查 (对照 0187.js:70-91)
            key = (project_id, frame_id or "", filename)
            existing_id = self._by_filename.get(key)
            if existing_id and existing_id in self._artifacts:
                art = self._artifacts[existing_id]
                artifact_id = art.id
                parent_version_id = art.latest_version_id
            else:
                artifact_id = _uuid()
                art = ArtifactRecord(
                    id=artifact_id,
                    project_id=project_id,
                    root_frame_id=root_frame_id,
                    filename=filename,
                    frame_id=frame_id,
                )
                self._artifacts[artifact_id] = art
                self._by_filename[key] = artifact_id
                is_new = True

        art = self._artifacts[artifact_id]

        # 乐观并发检测 (对照 0187.js:120-138)
        stale_base = None
        expected_parent = parent_version_id
        if (
            expected_parent is not None
            and art.latest_version_id is not None
            and art.latest_version_id != expected_parent
        ):
            cur_latest = self._versions.get(art.latest_version_id)
            cur_num = cur_latest.version_number if cur_latest else 0
            base_num = (
                self._versions[expected_parent].version_number
                if expected_parent in self._versions
                else 0
            )
            stale_base = {
                "based_on_version_id": expected_parent,
                "based_on_version_number": base_num,
                "current_latest_version_id": art.latest_version_id,
                "current_latest_version_number": cur_num,
            }

        # 写文件 (storage_path)
        vid = _uuid()
        rel_dir = Path("artifacts") / artifact_id
        rel_path = rel_dir / f"v{_short(vid)}_{_sanitize(filename)}"
        abs_path = self.workspace / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            abs_path.write_bytes(content)
            size = len(content)
        else:
            abs_path.write_text(content, encoding="utf-8")
            size = len(content.encode("utf-8"))
        checksum = hashlib.sha256(
            content if isinstance(content, bytes) else content.encode("utf-8")
        ).hexdigest()

        # 版本号
        version_number = 1
        if parent_version_id and parent_version_id in self._versions:
            version_number = self._versions[parent_version_id].version_number + 1

        ver = VersionRecord(
            id=vid,
            artifact_id=artifact_id,
            version_number=version_number,
            content_type=content_type,
            size_bytes=size,
            checksum=checksum,
            storage_path=str(rel_path),
            agent_name=agent_name,
            language=language,
            parent_version_id=parent_version_id,
            is_intermediate=is_intermediate,
            frame_id=frame_id,
            extracted_code=extracted_code,
        )
        self._versions[vid] = ver

        # 更新 latest (intermediate 不更新)
        if not is_intermediate:
            art.latest_version_id = vid

        # 依赖 DAG
        new_deps: list[tuple[str, str]] = []
        if dependencies:
            for d in dependencies:
                dep_vid = d.get("version_id")
                ref = d.get("ref_name", "")
                if dep_vid and dep_vid in self._versions:
                    edge = (vid, dep_vid, ref)
                    if edge not in self._deps:
                        self._deps.append(edge)
                    new_deps.append((dep_vid, ref))

        return SaveResult(
            artifact_id=artifact_id,
            version_id=vid,
            version_number=version_number,
            filename=filename,
            storage_path=str(rel_path),
            is_new_artifact=is_new,
            parent_version_id=parent_version_id,
            size_bytes=size,
            checksum=checksum,
            stale_base=stale_base,
            new_deps=new_deps,
        )

    def read(self, version_id: str) -> bytes | None:
        """按 version_id 读内容。"""
        ver = self._versions.get(version_id)
        if ver is None:
            return None
        path = self.workspace / ver.storage_path
        if not path.exists():
            return None
        return path.read_bytes()

    def read_text(self, version_id: str) -> str | None:
        data = self.read(version_id)
        return data.decode("utf-8") if data else None

    def get_version(self, version_id: str) -> VersionRecord | None:
        return self._versions.get(version_id)

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return self._artifacts.get(artifact_id)

    def list_versions(self, artifact_id: str) -> list[VersionRecord]:
        """列出 artifact 的所有版本 (按版本号)。"""
        return sorted(
            [v for v in self._versions.values() if v.artifact_id == artifact_id],
            key=lambda v: v.version_number,
        )

    def list_artifacts(self, project_id: str | None = None) -> list[ArtifactRecord]:
        if project_id is None:
            return list(self._artifacts.values())
        return [a for a in self._artifacts.values() if a.project_id == project_id]

    def get_lineage(self, version_id: str) -> dict[str, Any]:
        """取一个版本的 lineage (代码 + 依赖链)。对照原版 get_lineage。"""
        ver = self._versions.get(version_id)
        if ver is None:
            return {}
        return {
            "version_id": ver.id,
            "artifact_id": ver.artifact_id,
            "filename": next(
                (a.filename for a in self._artifacts.values() if a.id == ver.artifact_id),
                "",
            ),
            "extracted_code": ver.extracted_code,
            "parent_version_id": ver.parent_version_id,
            "checksum": ver.checksum,
        }

    def get_lineage_topology(self, version_id: str) -> dict[str, Any]:
        """依赖 DAG 拓扑。对照原版 get_lineage_topology。"""
        nodes = []
        edges = []
        visited = set()
        queue = [version_id]
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            ver = self._versions.get(cur)
            if ver:
                nodes.append({"version_id": cur, "artifact_id": ver.artifact_id})
            for src, dep, ref in self._deps:
                if src == cur and dep not in visited:
                    edges.append({"from": cur, "to": dep, "ref": ref})
                    queue.append(dep)
        return {"root_version_id": version_id, "nodes": nodes, "edges": edges}

    # ===== 阶段 4: SQLite 持久层 (内存优先, DB 可选) =====

    @property
    def has_db(self) -> bool:
        """是否启用了 SQLite 持久层。"""
        return self._db is not None

    async def save_async(self, *args: Any, **kwargs: Any) -> SaveResult:
        """save 的异步版本: 内存 + 文件 + SQLite 三写。

        与 save() 同签名。先调同步 save() (写文件 + 内存), 成功后若有 DB
        则把 artifact/version/deps 落库。失败仅记日志不中断 (文件已写, 保证可用)。
        agent 工具 (artifact_tool.save_artifacts, async) 应调本方法。
        """
        result = self.save(*args, **kwargs)
        if result.ok and self._db is not None:
            try:
                await self._persist(result)
            except Exception as e:
                # DB 写失败不阻断 agent 循环 (内存态已正确, 文件已落盘)
                logger.warning("artifact DB persist failed (mem ok): %s", e)
        return result

    async def _persist(self, result: SaveResult) -> None:
        """把一次 save 的结果写进 SQLite。

        对照原版 _saveArtifactCommon 的 DB 写路径。在一个事务里:
        先 INSERT/UPDATE artifact (latest_version_id 暂留空, 避免指向不存在的 version),
        再 INSERT version, 最后回填 artifact.latest_version_id (非中间件), 再 INSERT deps。
        """
        from sqlalchemy import select

        from operon.db.schema import (
            Artifact,
            ArtifactDependency,
            ArtifactVersion,
        )

        art = self._artifacts[result.artifact_id]
        ver = self._versions[result.version_id]

        async with self._db() as session:
            async with session.begin():
                # 0. 确保 project 行存在 (Artifact.project_id FK→projects.id CASCADE, NOT NULL)
                #    agent 场景下 project_id 通常是运行时生成的 (如 proj_<root>), DB 里没有对应行。
                from operon.db.schema import Project

                proj_exists = (
                    await session.execute(
                        select(Project).where(Project.id == art.project_id)
                    )
                ).scalar_one_or_none()
                if proj_exists is None:
                    session.add(Project(id=art.project_id, name=art.project_id))

                # 1. UPSERT artifact (latest_version_id 先留空, 末尾回填 — 避免外键指向
                #    尚未 INSERT 的 version 行触发 FOREIGN KEY constraint failed)
                existing = (
                    await session.execute(
                        select(Artifact).where(Artifact.id == art.id)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(
                        Artifact(
                            id=art.id,
                            project_id=art.project_id,
                            root_frame_id=art.root_frame_id,
                            frame_id=art.frame_id,
                            filename=art.filename,
                            is_user_upload=art.is_user_upload,
                            latest_version_id=None,
                        )
                    )

                # 2. INSERT version (uq_artifact_versions_artifact_version 防重复)
                dup = (
                    await session.execute(
                        select(ArtifactVersion).where(
                            ArtifactVersion.id == ver.id
                        )
                    )
                ).scalar_one_or_none()
                if dup is None:
                    session.add(
                        ArtifactVersion(
                            id=ver.id,
                            artifact_id=ver.artifact_id,
                            version_number=ver.version_number,
                            frame_id=ver.frame_id,
                            content_type=ver.content_type,
                            size_bytes=ver.size_bytes,
                            checksum=ver.checksum,
                            storage_path=ver.storage_path,
                            agent_name=ver.agent_name,
                            language=ver.language,
                            is_intermediate=ver.is_intermediate,
                            parent_version_id=ver.parent_version_id,
                            extracted_code=ver.extracted_code,
                            dependency_mappings=ver.dependency_mappings,
                        )
                    )
                # flush 让 version 行可见, 之后才能被 artifact.latest_version_id 引用
                await session.flush()

                # 3. 回填 artifact.latest_version_id (非中间件才更新)
                if art.latest_version_id:
                    db_art = (
                        await session.execute(
                            select(Artifact).where(Artifact.id == art.id)
                        )
                    ).scalar_one()
                    db_art.latest_version_id = art.latest_version_id

                # 4. INSERT deps (DAG) — 只写本次 save 新增的边 (来自 SaveResult.new_deps)
                for dep_vid, ref in result.new_deps:
                    if not dep_vid:
                        continue
                    edge_dup = (
                        await session.execute(
                            select(ArtifactDependency).where(
                                ArtifactDependency.version_id == ver.id,
                                ArtifactDependency.depends_on_version_id
                                == dep_vid,
                                ArtifactDependency.ref_name == ref,
                            )
                        )
                    ).scalar_one_or_none()
                    if edge_dup is None:
                        session.add(
                            ArtifactDependency(
                                id=str(uuid.uuid4()),
                                version_id=ver.id,
                                depends_on_version_id=dep_vid,
                                ref_name=ref,
                            )
                        )

    async def load_from_db(self, project_id: str | None = None) -> int:
        """从 SQLite 回放 artifacts/versions/deps 到内存。

        用于断点续会话: 服务重启后, 已有 artifact 的元数据从 DB 加载回内存,
        使 host.lineage / host.query / get_version 等读路径可用。
        文件内容仍在工作区 (DB 不存内容), read() 不受影响。

        Args:
            project_id: 只加载该 project 的 artifact; None=全部
        Returns:
            加载的 version 数
        """
        from sqlalchemy import select

        from operon.db.schema import (
            Artifact,
            ArtifactDependency,
            ArtifactVersion,
        )

        if self._db is None:
            return 0
        n_versions = 0
        async with self._db() as session:
            # artifacts
            art_q = select(Artifact)
            if project_id is not None:
                art_q = art_q.where(Artifact.project_id == project_id)
            arts = (await session.execute(art_q)).scalars().all()
            art_ids = set()
            for a in arts:
                art_ids.add(a.id)
                self._artifacts[a.id] = ArtifactRecord(
                    id=a.id,
                    project_id=a.project_id,
                    root_frame_id=a.root_frame_id,
                    filename=a.filename,
                    frame_id=a.frame_id,
                    latest_version_id=a.latest_version_id,
                    is_user_upload=a.is_user_upload,
                )
                self._by_filename[
                    (a.project_id, a.frame_id or "", a.filename)
                ] = a.id

            # versions (只加载属于已选 artifacts 的)
            ver_q = select(ArtifactVersion)
            if art_ids:
                ver_q = ver_q.where(
                    ArtifactVersion.artifact_id.in_(art_ids)
                )
            elif project_id is not None:
                # 无 artifact 命中则不加载 version
                return 0
            vers = (await session.execute(ver_q)).scalars().all()
            for v in vers:
                self._versions[v.id] = VersionRecord(
                    id=v.id,
                    artifact_id=v.artifact_id,
                    version_number=v.version_number,
                    content_type=v.content_type,
                    size_bytes=v.size_bytes,
                    checksum=v.checksum,
                    storage_path=v.storage_path,
                    agent_name=v.agent_name,
                    language=v.language,
                    parent_version_id=v.parent_version_id,
                    is_intermediate=v.is_intermediate,
                    frame_id=v.frame_id,
                    extracted_code=v.extracted_code,
                    dependency_mappings=v.dependency_mappings,
                )
                n_versions += 1

            # deps (只加载属于已加载 versions 的边)
            if self._versions:
                vids = list(self._versions.keys())
                dep_q = select(ArtifactDependency).where(
                    ArtifactDependency.version_id.in_(vids)
                )
                deps = (await session.execute(dep_q)).scalars().all()
                for d in deps:
                    self._deps.append(
                        (d.version_id, d.depends_on_version_id, d.ref_name)
                    )
        return n_versions


def _guess_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".py": "text/x-python",
        ".tex": "application/x-tex",
        ".bib": "application/x-bibtex",
        ".csv": "text/csv",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


def _sanitize(filename: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9._-]", "_", filename)


# ===== {{artifact:VID}} marker 语法 (对照 0249.js:119) =====
# 注意: {{artifact:VID}} 是双花括号; VID 允许字母数字和连字符
MARKER_RE = r"!\[([^\]]*)\]\(\{\{artifact:([0-9a-zA-Z_-]+)\}\}\)"


def make_marker(vid: str, caption: str = "") -> str:
    """生成 {{artifact:VID}} 标记。对照原版 D7_ = "![caption]({{artifact:<vid>}})"。"""
    return "![" + caption + "]({{artifact:" + vid + "}})"


def extract_markers(text: str) -> list[tuple[str, str]]:
    """从文本提取所有 marker。返回 [(caption, vid)]。"""
    import re

    return [(m.group(1), m.group(2)) for m in re.finditer(MARKER_RE, text)]
