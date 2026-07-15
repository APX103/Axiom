"""配置体系。

对应原版: 0039.js 的 Zod config schema + 0038.js release 固化 + config.toml。
重设计 (divergences §7): 用 Pydantic-Settings 替代 Zod,支持环境变量 + TOML。

模型分级 (divergences §9): 原版硬编码 haiku/sonnet/opus,改为可配三档。
Rolling Compact 配置严格对照原版默认值 (见 mapping.md 常数表)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelTier(BaseModel):
    """单个模型档位配置。

    对应原版 0234.js:16 dF 的 small/medium/large,但 model 名可配。
    base_url 为空则用 provider 默认。
    context_window: 模型上下文长度 (token),None 时用 RollingCompactConfig.context_ceiling 兜底。
    """

    model: str
    base_url: str | None = None
    api_key: str | None = None
    max_tokens: int = 8192
    context_window: int | None = None  # 模型实际上下文长度 (如 256000)


class ModelsConfig(BaseModel):
    """模型分级配置。对应原版 dF。

    small/medium 可选: 未配置时降级到 large (首版单模型也能跑)。
    """

    large: ModelTier
    small: ModelTier | None = None
    medium: ModelTier | None = None
    # kernel SDK 子调用用的轻量模型 (原版 kernel_default_model)
    kernel: ModelTier | None = None
    # reviewer 模型 (原版 [verification].reviewer_model)
    reviewer: ModelTier | None = None

    def tier(self, name: str) -> ModelTier:
        """按档位名取配置,缺失则降级到 large。"""
        configured = getattr(self, name, None)
        if configured is not None:
            return configured
        return self.large


class RollingCompactConfig(BaseModel):
    """Rolling Compact 配置。对照原版 0039.js:304-309 + 0848.js 常数。

    默认值严格对照原版 (见 docs/mapping.md 常数表)。
    """

    enabled: bool = True
    context_ceiling: int = 500000  # 原 rolling_compact_context_ceiling
    ka_ratio: float = 0.2  # 原 rolling_compact_ka_ratio
    ka_floor: int = 50000  # 原 KA_FLOOR (mSz)
    kb_ratio: float = 0.7  # 原 KB_RATIO (cSz)
    l2_prefix_budget_ratio: float = 0.4  # 原 lSz
    min_chunk_tokens: int = 4096  # 原 Xx_
    output_ceiling: int = 32000  # 原 aSz
    max_fork_failures: int = 3  # 原 po
    chars_per_token: int = 4  # 原 d8 (0836.js:417)
    degenerate_draft_ratio: float = 0.25  # 原 dSz
    l1_fold: str = "auto"  # 原 rolling_compact_l1_fold


class MemoryConfig(BaseModel):
    """三层记忆配置。对照原版 0039.js:101-127 [memory]。"""

    enabled: bool = True
    extract_enabled: bool = True  # 每轮结束后自动提取记忆
    extract_max_per_run: int = 5  # 每次提取最多写入条数
    recall_inject_max: int = 6  # 每轮最多召回注入条数
    profile_max_rows: int = 40  # system prompt 里最多注入的 profile 条数


class VerificationConfig(BaseModel):
    """验证 harness 配置。对照原版 0039.js:223-229 [verification]。

    enabled 默认 False: 需要时显式开启 (避免单元测试/简单场景自动审稿)。
    """

    enabled: bool = False
    reviewer_max_iterations: int = 20
    reviewer_operon_budget: int = 8  # 原 0039.js:229
    shadow_reviewer: bool = False  # 原版 release 关闭,对应 0871.js:996
    bookmarks_enabled: bool = False
    max_consecutive_bounces: int = 3
    min_checkpoint_interval_ms: int = 30000


class InvalidationConfig(BaseModel):
    """invalidation loop 配置。对照原版 0233.js:49 Ls_=2。"""

    max_output_invalidations: int = 2  # 原 Ls_


class SandboxConfig(BaseModel):
    """沙箱配置。对应原版 0039.js:158 [sandbox]。首版简化。"""

    disabled: bool = False
    network_isolated: bool = False
    writable_roots: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    """顶层配置。对应原版 0039.js d9w + 0038.js release 固化。

    优先级: 环境变量 > TOML 文件 > 代码默认值。
    环境变量前缀 OPERON_ (如 OPERON_DATA_DIR)。
    """

    model_config = SettingsConfigDict(
        env_prefix="operon_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # 顶层 (原版 d9w:265)
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path.home() / ".axiom"
    default_model_tier: str = "large"  # small/medium/large
    db_path: Path | None = None  # None → data_dir/operon.db

    # 分系统
    models: ModelsConfig | None = None
    rolling_compact: RollingCompactConfig = Field(default_factory=RollingCompactConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    invalidation: InvalidationConfig = Field(default_factory=InvalidationConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    # 数据源 API keys (OpenAlex / Semantic Scholar / 搜索 API 等)。
    # 对应原版 host.credentials。config.toml 集中存, 不进 git (.gitignore 已含 config.toml)。
    # 工具通过 ctx.api_keys.get("OPENALEX_API_KEY") 等读取。
    api_keys: dict[str, str] = Field(default_factory=dict)
    # MCP servers (搜索 MCP 等)。每项 {name, url, headers}。
    # 前端创建会话时也可传; config.toml 里的作为默认。
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)

    def db_url(self) -> str:
        path = self.db_path or (self.data_dir / "operon.db")
        return f"sqlite:///{path}"

    def data_dir_resolved(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


def _default_config_path() -> Path | None:
    """默认搜索 config.toml: 当前工作目录 → 包根目录 (operon-py/)。"""
    candidates = [
        Path.cwd() / "config.toml",
        Path(__file__).resolve().parent.parent / "config.toml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_settings(config_path: Path | None = None) -> Settings:
    """加载配置。

    优先级:
    1. 显式 config_path
    2. data_dir/settings.json (APP 内设置持久化)
    3. 自动搜索 (cwd/ 包根) 的 config.toml
    4. 代码默认值
    config.toml 在 .gitignore 里 (含 key, 不进 git)。
    """
    # 1. 显式 config_path 最高优先级
    if config_path and config_path.exists():
        import tomllib

        with open(config_path, "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        return Settings(**data)

    # 先拿一个默认 Settings 以确定 data_dir (可能被 OPERON_DATA_DIR 覆盖)
    default_settings = Settings()

    # 2. APP 内持久化设置
    try:
        from operon.settings import SettingsStore, app_settings_to_config_settings

        store = SettingsStore(default_settings.data_dir)
        app_cfg = store.load()
        if app_cfg is not None:
            return app_settings_to_config_settings(app_cfg, default_settings.data_dir)
    except Exception:
        pass

    # 3. 自动搜索 config.toml
    path = _default_config_path()
    if path and path.exists():
        import tomllib

        with open(path, "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        return Settings(**data)
    return Settings()
