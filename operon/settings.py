"""APP 内设置持久化。

对应需求: Axiom 桌面端不再要求用户编辑 config.toml, 所有配置通过前端设置弹窗
保存到后端, 后端持久化到 data_dir/settings.json。

本模块提供:
- AppSettings / LLMProvider / MCPServer Pydantic 模型
- SettingsStore 负责读写 settings.json
- 与 operon.config.Settings 的双向转换, 保证后端其它模块仍使用原有配置体系
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from operon.config import ModelTier, ModelsConfig, Settings


class LLMProvider(BaseModel):
    """一个 LLM Provider 配置。"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    context_window: int = 256000
    max_tokens: int = 8192
    enabled: bool = False


class MCPServer(BaseModel):
    """一个 MCP server 配置。"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False


class AppSettings(BaseModel):
    """前端设置弹窗保存的完整配置。"""

    version: int = 1
    llm_providers: list[LLMProvider] = Field(default_factory=list)
    mcp_servers: list[MCPServer] = Field(default_factory=list)
    api_keys: dict[str, str] = Field(default_factory=dict)
    workspace: str | None = None
    plan_mode: bool = False
    default_model_tier: str = "large"
    # session 级启用配置: 默认禁用哪些 skill; 创建会话时传入 SessionConfig。
    disabled_skills: list[str] = Field(default_factory=list)
    # 是否加载 Claude Code 用户级 skill 目录 (~/.claude/skills)。
    load_claude_skills: bool = True
    # 是否加载当前项目/工作区 skill 目录 (workspace/.axiom/skills)。
    load_project_skills: bool = True
    # 额外自定义 skill 目录路径列表 (工具级配置,所有会话共享)。
    skill_extra_dirs: list[str] = Field(default_factory=list)


# ---- key 脱敏 helpers ----


def mask_key(key: str | None) -> str:
    """把 API key 脱敏为 sk-****abcd 形式; 空值返回空字符串。"""
    if not key:
        return ""
    if len(key) <= 12:
        return "*" * len(key)
    return f"{key[:4]}****{key[-4:]}"


def unmask_key(new_value: str, old_value: str | None) -> str:
    """前端提交 mask 值时保持旧值不变, 否则使用新值。"""
    masked_old = mask_key(old_value)
    if new_value == masked_old:
        return old_value or ""
    return new_value


def mask_app_settings(settings: AppSettings) -> AppSettings:
    """返回所有 api_key 被 mask 后的副本, 用于 GET /api/settings。"""
    masked_providers = [
        p.model_copy(update={"api_key": mask_key(p.api_key)}) for p in settings.llm_providers
    ]
    masked_mcps = []
    for s in settings.mcp_servers:
        masked_headers = {k: mask_key(v) for k, v in s.headers.items()}
        masked_mcps.append(s.model_copy(update={"headers": masked_headers}))
    masked_api_keys = {k: mask_key(v) for k, v in settings.api_keys.items()}
    return settings.model_copy(
        update={
            "llm_providers": masked_providers,
            "mcp_servers": masked_mcps,
            "api_keys": masked_api_keys,
        }
    )


def unmask_app_settings(incoming: AppSettings, existing: AppSettings) -> AppSettings:
    """把前端提交的配置与已有配置合并, mask 字段保持旧值。"""
    old_keys = {p.id: p.api_key for p in existing.llm_providers}
    providers = []
    for p in incoming.llm_providers:
        old = old_keys.get(p.id)
        providers.append(p.model_copy(update={"api_key": unmask_key(p.api_key, old)}))

    old_mcp_headers = {s.id: s.headers for s in existing.mcp_servers}
    mcps = []
    for s in incoming.mcp_servers:
        old_headers = old_mcp_headers.get(s.id, {})
        merged_headers = {
            k: unmask_key(v, old_headers.get(k)) for k, v in s.headers.items()
        }
        mcps.append(s.model_copy(update={"headers": merged_headers}))

    old_api_keys = existing.api_keys
    merged_api_keys = {k: unmask_key(v, old_api_keys.get(k)) for k, v in incoming.api_keys.items()}

    return incoming.model_copy(
        update={
            "llm_providers": providers,
            "mcp_servers": mcps,
            "api_keys": merged_api_keys,
        }
    )


# ---- 与 operon.config.Settings 的转换 ----


def app_settings_to_config_settings(app: AppSettings, data_dir: Path | None = None) -> Settings:
    """把 AppSettings 转换为后端使用的 Settings。"""
    enabled = [p for p in app.llm_providers if p.enabled]
    primary = enabled[0] if enabled else None

    models = None
    if primary:
        models = ModelsConfig(
            large=ModelTier(
                model=primary.model,
                base_url=primary.base_url or None,
                api_key=primary.api_key or None,
                max_tokens=primary.max_tokens,
                context_window=primary.context_window or None,
            )
        )

    mcp_servers = []
    for s in app.mcp_servers:
        if not s.enabled:
            continue
        mcp_servers.append(
            {
                "name": s.name or s.id,
                "url": s.url,
                "headers": s.headers,
            }
        )

    kwargs: dict[str, Any] = {
        "models": models,
        "mcp_servers": mcp_servers,
        "api_keys": app.api_keys,
        "default_model_tier": app.default_model_tier,
    }
    if data_dir is not None:
        kwargs["data_dir"] = data_dir
    if app.workspace:
        kwargs["data_dir"] = Path(app.workspace).expanduser().resolve()

    return Settings(**kwargs)


def config_settings_to_app_settings(settings: Settings) -> AppSettings:
    """把 config.toml 加载的 Settings 转换为 AppSettings, 用于首次迁移。"""
    providers: list[LLMProvider] = []
    if settings.models:
        for tier in ("small", "medium", "large", "kernel", "reviewer"):
            t = getattr(settings.models, tier)
            if t is None:
                continue
            providers.append(
                LLMProvider(
                    id=str(uuid.uuid4())[:8],
                    name=tier,
                    base_url=t.base_url or "",
                    api_key=t.api_key or "",
                    model=t.model or "",
                    context_window=t.context_window or 256000,
                    max_tokens=t.max_tokens,
                    enabled=(tier == settings.default_model_tier),
                )
            )

    mcps: list[MCPServer] = []
    for s in settings.mcp_servers:
        mcps.append(
            MCPServer(
                id=str(uuid.uuid4())[:8],
                name=s.get("name", ""),
                url=s.get("url", ""),
                headers=s.get("headers", {}),
                enabled=True,
            )
        )

    return AppSettings(
        llm_providers=providers,
        mcp_servers=mcps,
        api_keys=settings.api_keys,
        workspace=str(settings.data_dir) if settings.data_dir else None,
        plan_mode=False,
        default_model_tier=settings.default_model_tier,
    )


# ---- 持久化 ----


class SettingsStore:
    """读写 data_dir/settings.json。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.path = self.data_dir / "settings.json"

    def load(self) -> AppSettings | None:
        if not self.path.exists():
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return AppSettings(**raw)
        except Exception:
            return None

    def save(self, settings: AppSettings) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(settings.model_dump_json(indent=2))

    def migrate_from_config(self, settings: Settings) -> AppSettings:
        """从 config.toml 迁移到 settings.json 并保存。"""
        app = config_settings_to_app_settings(settings)
        self.save(app)
        return app


def get_app_settings(data_dir: Path) -> AppSettings:
    """获取 AppSettings: 优先读 settings.json, 不存在则从 config.toml 迁移。"""
    from operon.config import load_settings

    store = SettingsStore(data_dir)
    app = store.load()
    if app is not None:
        return app
    # 首次启动: 从 config.toml 迁移并保存
    cfg = load_settings()
    return store.migrate_from_config(cfg)
