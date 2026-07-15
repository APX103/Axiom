"""SQLAlchemy 2.0 数据模型。

对应原版: 0110.js 的 Drizzle ORM schema (0088.js/0108.js 的 CREATE TABLE)。
本模块实现的核心表:
- frames (0110.js:51-133, 变量 P)
- projects (简化)
- artifacts (0110.js:302-349, 变量 T_)
- artifact_versions (0110.js:351-419, 变量 N_)
- artifact_dependencies (0110.js, DAG 边 — 阶段 4 新增)
- verification_checks (0110.js:977-1007, 变量 k3)
- compaction_archives (0110.js:176, 变量 mx)

其余表 (content_snapshots, transcript_annotations 等) 在对应阶段实现时补全。

列名采用原版的 snake_case 物理列名 (如 parent_frame_id),Python 属性用 snake_case。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """ORM 基类。对应原版 Drizzle 的 schema 总入口。"""

    pass


class Frame(Base):
    """会话帧 — 核心执行单元。

    对应原版: 0110.js:51-133, 变量 P。
    一个 frame = 一次 agent 调用 (一个 runner 执行单元),不是单条消息。
    一个对话/会话是一棵 frame 树: parent_frame_id → root_frame_id 形成树。

    关键设计 (照搬):
    - 根 frame: parent_frame_id=NULL, root_frame_id=id
    - 子 frame (delegate/reviewer/bookmarker): parent+root 都指向父/根
    - REVIEWER/BOOKMARKER 是 is_hidden=true 的子 frame
    """

    __tablename__ = "frames"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_frame_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("frames.id", ondelete="SET NULL"), nullable=True
    )
    root_frame_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("frames.id", ondelete="SET NULL"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False)
    delegate_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # status: 见 FrameStatus 枚举 (operon.agent.states)
    # processing|completed|failed|success|replaced|cancelled|awaiting_plan_approval|awaiting_user_response
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="processing")
    input_data: Mapped[dict | None] = mapped_column(Text, nullable=True)  # JSON
    output_data: Mapped[dict | None] = mapped_column(Text, nullable=True)
    context_data: Mapped[dict | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    aux_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aux_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aux_cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aux_cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aux_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_class_usage: Mapped[dict | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_user_message_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_extract_msg_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    project_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_type: Mapped[str] = mapped_column(String(50), nullable=False, default="agent")
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mentioned_artifact_ids: Mapped[dict | None] = mapped_column(Text, nullable=True)
    specialists_used: Mapped[dict | None] = mapped_column(Text, nullable=True)
    is_hidden: Mapped[bool] = mapped_column(default=False)
    compute_enabled: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("ix_frames_parent_frame_id", "parent_frame_id"),
        Index("ix_frames_root_frame_id", "root_frame_id"),
        Index("ix_frames_agent_name", "agent_name"),
        Index("ix_frames_status", "status"),
        Index("ix_frames_project_id", "project_id"),
        Index("ix_frames_artifact_id", "artifact_id"),
        Index("ix_frames_created_at", "created_at"),
        Index("ix_frames_status_updated", "status", "updated_at"),
        Index("ix_frames_updated_at", "updated_at"),
        Index("ix_frames_root_updated", "root_frame_id", "updated_at"),
        Index("ix_frames_root_seq", "root_frame_id", "root_seq"),
    )


class Project(Base):
    """项目。简化版 (原版 0110.js projects 表更复杂,首版够用即可)。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    frames: Mapped[list[Frame]] = relationship(backref="project")


class Artifact(Base):
    """逻辑产物文件 (可有多版本)。

    对应原版: 0110.js:302-349, 变量 T_。
    一个 artifact 是逻辑文件,物理内容在 artifact_versions。
    latest_version_id 指向当前最新版本。
    """

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    root_frame_id: Mapped[str] = mapped_column(String(36), nullable=False)
    frame_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    latest_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("artifact_versions.id", ondelete="SET NULL"), nullable=True
    )
    is_user_upload: Mapped[bool] = mapped_column(default=False)
    is_branch_mint: Mapped[bool] = mapped_column(default=False)
    is_ephemeral: Mapped[bool] = mapped_column(default=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[str] = mapped_column(String(20), default="unknown")
    superseded_by_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    versions: Mapped[list[ArtifactVersion]] = relationship(
        backref="artifact", foreign_keys="ArtifactVersion.artifact_id"
    )

    __table_args__ = (
        Index("ix_artifacts_project_id", "project_id"),
        Index("ix_artifacts_root_frame_id", "root_frame_id"),
        Index("ix_artifacts_frame_id", "frame_id"),
        Index("ix_artifacts_is_user_upload", "is_user_upload"),
        Index("ix_artifacts_latest_version_id", "latest_version_id"),
        Index("ix_artifacts_created_at", "created_at"),
    )


class ArtifactVersion(Base):
    """产物版本 — 物理内容 + lineage。

    对应原版: 0110.js:351-419, 变量 N_。
    每次保存 artifact 追加一个 version,parent_version_id 指向上一版。
    带 extracted_code/lineage_messages/environment_snapshot 用于复现。
    """

    __tablename__ = "artifact_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    extracted_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lineage_messages: Mapped[dict | None] = mapped_column(Text, nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_intermediate: Mapped[bool] = mapped_column(default=False)
    dependency_mappings: Mapped[dict | None] = mapped_column(Text, nullable=True)
    environment_snapshot: Mapped[dict | None] = mapped_column(Text, nullable=True)
    annotations: Mapped[dict | None] = mapped_column(Text, nullable=True)
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lineage_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    env_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    producing_cell_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_checkpoint: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        Index("ix_artifact_versions_artifact_id", "artifact_id"),
        Index("ix_artifact_versions_frame_id", "frame_id"),
        Index("ix_artifact_versions_parent_version_id", "parent_version_id"),
        Index("ix_artifact_versions_lineage_snapshot_hash", "lineage_snapshot_hash"),
        Index("ix_artifact_versions_env_snapshot_hash", "env_snapshot_hash"),
        # 原版 uq_artifact_versions_artifact_version (artifact_id, version_number) 唯一
        Index(
            "uq_artifact_versions_artifact_version",
            "artifact_id",
            "version_number",
            unique=True,
        ),
    )


class VerificationCheck(Base):
    """验证检查记录。

    对应原版: 0110.js:977-1007, 变量 k3。
    每条记录是 reviewer 对某个 claim 的一次裁决。
    verdict: pass|warn|fail|inconclusive (照搬原版 enum)
    status: open|resolved|unaddressed (照搬原版 enum)
    """

    __tablename__ = "verification_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    root_frame_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("frames.id", ondelete="CASCADE"), nullable=False
    )
    artifact_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)  # pass|warn|fail|inconclusive
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    rebuttal: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_frame_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewer_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    reflag_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        Index("ix_verification_checks_root", "root_frame_id"),
        Index("ix_verification_checks_status", "root_frame_id", "status"),
        Index("ix_verification_checks_claim", "claim_id"),
    )


class CompactionArchive(Base):
    """Rolling Compact 压缩归档。

    对应原版: 0110.js:176, 变量 mx。
    每次压缩 (L1/L2) 记录被压缩的消息和生成的 summary。
    summary_query 工具用此表按需取回原文细节。
    """

    __tablename__ = "compaction_archives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    frame_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("frames.id", ondelete="CASCADE"), nullable=False
    )
    compaction_index: Mapped[int] = mapped_column(Integer, nullable=False)
    fold_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # rc_fold_l1|rc_fold_l2|compact_destructive
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    archived_messages: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (Index("ix_compaction_archives_frame", "frame_id"),)


class ArtifactDependency(Base):
    """Artifact 版本间的依赖 DAG 边。

    对应原版: 0110.js 的 artifact_dependencies 表。
    ArtifactStore 内存态用 self._deps (list[(version_id, depends_on_version_id, ref_name)])
    承载; 落库时写这张表, 供 get_lineage_topology 做递归/BFS 拓扑查询。

    每行是一条边: 某个 version (version_id) 依赖另一个 version (depends_on_version_id),
    ref_name 是引用名 (如 figure 引用时的 ref)。
    双向索引: 拓扑查询既要从上往下 (version → deps),也要能从下往上 (被谁依赖)。
    """

    __tablename__ = "artifact_dependencies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("artifact_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("artifact_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ref_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        Index("ix_artifact_dependencies_version_id", "version_id"),
        Index("ix_artifact_dependencies_depends_on", "depends_on_version_id"),
        # 同一对 (version, dep, ref) 唯一, 避免重复入库
        Index(
            "uq_artifact_dependencies_edge",
            "version_id",
            "depends_on_version_id",
            "ref_name",
            unique=True,
        ),
    )


class SessionRecord(Base):
    """持久化会话元数据。

    一个 session 对应一次用户交互会话 (可包含多轮 agent run)。
    session 与 frame 是不同粒度: frame 是 agent 执行单元, session 是用户视角的对话。
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace: Mapped[str] = mapped_column(String(512), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_mode: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    messages: Mapped[list[SessionMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="SessionMessage.seq"
    )

    __table_args__ = (
        Index("ix_sessions_status", "status"),
        Index("ix_sessions_updated_at", "updated_at"),
    )


class SessionMessage(Base):
    """持久化会话消息 (用户/助手/系统的每轮对话内容)。"""

    __tablename__ = "session_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    session: Mapped[SessionRecord] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_session_messages_session_id", "session_id"),
        Index("ix_session_messages_session_seq", "session_id", "seq"),
    )


class MemoryRecord(Base):
    """三层记忆: profile (用户全局) / project (跨会话) / frame (会话级)。

    对应原版 memories 表 (0026_memory.sql + 0037 + 0052)。
    简化: 去掉 user_id (单用户), 去掉 categories, 去掉 supersede chain (用 replace 直接更新)。
    保留: entity 分层 + evidence 标签 + origin 来源 + last_surfaced_at 召回追踪。
    """

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # mem_<12hex>
    entity: Mapped[str] = mapped_column(String(20), nullable=False)  # profile / project / frame
    body: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(String(20), nullable=False, default="stated")
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    frame_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # frame 层用
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
    last_surfaced_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_memories_entity", "entity"),
        Index("ix_memories_frame_id", "frame_id"),
    )
