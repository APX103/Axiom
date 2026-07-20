// 设置对话框: 配置 LLM Provider / MCP / Skills / 学术 API / 通用选项。
// 配置保存到后端, 后端持久化到 settings.json; localStorage 仅作缓存。
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { saveSettings, listSkills, getMcpTools, listMemories, deleteMemory } from "../api";
import { version as CURRENT_VERSION } from "../../package.json";
import type { AppSettings, LLMProvider, MCPServer, SkillInfo, McpServerStatus, MemoryInfo, VerificationConfig, TraceConfig } from "../types";

const STORAGE_KEY = "operon-py-app-config";

export type FullConfig = AppSettings;

function newProvider(): LLMProvider {
  return {
    id: Math.random().toString(36).slice(2, 10),
    name: "",
    base_url: "",
    api_key: "",
    model: "",
    context_window: 256000,
    max_tokens: 8192,
    enabled: false,
  };
}

function newMcp(): MCPServer {
  return {
    id: Math.random().toString(36).slice(2, 10),
    name: "",
    url: "",
    key: "",
    enabled: false,
  };
}

function newVerification(): VerificationConfig {
  return { enabled: false };
}

function newTrace(): TraceConfig {
  return { enabled: false };
}

function migrateOldConfig(old: Record<string, unknown>): FullConfig {
  // 旧格式: { base_url, api_key, model, context_window, workspace, plan_mode, mcp_url, mcp_key, openalex_key }
  const p: LLMProvider = {
    ...newProvider(),
    name: "default",
    base_url: (old.base_url as string) || "",
    api_key: (old.api_key as string) || "",
    model: (old.model as string) || "",
    context_window: Number(old.context_window) || 256000,
    enabled: true,
  };
  const mcp: MCPServer[] = old.mcp_key
    ? [
        {
          ...newMcp(),
          name: "web_search_prime",
          url: (old.mcp_url as string) || "",
          key: (old.mcp_key as string) || "",
          enabled: true,
        },
      ]
    : [];
  return {
    version: 1,
    llm_providers: [p],
    mcp_servers: mcp,
    api_keys: old.openalex_key ? { OPENALEX_API_KEY: old.openalex_key as string } : {},
    workspace: (old.workspace as string) || ".",
    plan_mode: !!old.plan_mode,
    default_model_tier: "large",
    disabled_skills: [],
    load_claude_skills: true,
    load_project_skills: true,
    skill_extra_dirs: [],
    verification: newVerification(),
    trace: newTrace(),
  };
}

export function loadConfig(): FullConfig {
  try {
    // 新格式
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
    // 旧格式迁移
    const oldRaw = localStorage.getItem("operon-py-model-config");
    if (oldRaw) {
      const migrated = migrateOldConfig(JSON.parse(oldRaw));
      localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated));
      return migrated;
    }
  } catch {
    /* ignore */
  }
  return {
    version: 1,
    llm_providers: [newProvider()],
    mcp_servers: [],
    api_keys: {},
    workspace: ".",
    plan_mode: false,
    default_model_tier: "large",
    disabled_skills: [],
    load_claude_skills: true,
    load_project_skills: true,
    skill_extra_dirs: [],
    verification: newVerification(),
    trace: newTrace(),
  };
}

function saveLocalCache(c: FullConfig) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(c));
}

// 把后端返回的 settings (headers 结构) 转成前端简化结构
export function fromApiSettings(raw: Record<string, unknown>): FullConfig {
  const providers = ((raw.llm_providers as LLMProvider[]) || []).map((p) => ({
    ...p,
    api_key: p.api_key || "",
  }));
  if (providers.length === 0) providers.push(newProvider());

  const mcps = ((raw.mcp_servers as any[]) || []).map((s) => ({
    id: s.id,
    name: s.name,
    url: s.url,
    key: (s.headers?.Authorization as string) || "",
    enabled: s.enabled,
  }));

  return {
    version: (raw.version as number) || 1,
    llm_providers: providers,
    mcp_servers: mcps,
    api_keys: (raw.api_keys as Record<string, string>) || {},
    workspace: (raw.workspace as string) || ".",
    plan_mode: !!raw.plan_mode,
    default_model_tier: (raw.default_model_tier as string) || "large",
    disabled_skills: (raw.disabled_skills as string[]) || [],
    load_claude_skills: raw.load_claude_skills !== false,
    load_project_skills: raw.load_project_skills !== false,
    skill_extra_dirs: (raw.skill_extra_dirs as string[]) || [],
    verification: (raw.verification as VerificationConfig) || newVerification(),
    trace: (raw.trace as TraceConfig) || newTrace(),
  };
}

// 把前端结构转回后端格式
export function toApiSettings(c: FullConfig): Record<string, unknown> {
  return {
    ...c,
    mcp_servers: c.mcp_servers.map((s) => ({
      ...s,
      headers: s.key ? { Authorization: `Bearer ${s.key}` } : {},
    })),
    verification: c.verification,
    trace: c.trace,
  };
}

export function SettingsModal({
  initial,
  onClose,
  onSave,
}: {
  initial: FullConfig;
  onClose: () => void;
  onSave: (c: FullConfig) => void;
}) {
  const [cfg, setCfg] = useState<FullConfig>(initial);
  const [tab, setTab] = useState<"models" | "mcp" | "skills" | "memory" | "academic" | "general">("models");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Skills 列表 (从后端加载)
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  const [skillDirInput, setSkillDirInput] = useState("");

  // MCP 工具探测状态: serverName → status
  const [mcpStatus, setMcpStatus] = useState<Record<string, McpServerStatus>>({});
  const [mcpExpanded, setMcpExpanded] = useState<Set<string>>(new Set());
  const [mcpProbing, setMcpProbing] = useState<Set<string>>(new Set());

  // 记忆列表
  const [memories, setMemories] = useState<MemoryInfo[]>([]);
  const [memLoading, setMemLoading] = useState(false);

  // 若打开设置时存在有效 Provider 但未启用, 自动启用第一个, 减少用户困惑
  useEffect(() => {
    const hasEnabled = cfg.llm_providers.some((p) => p.enabled);
    if (hasEnabled) return;
    const firstReady = cfg.llm_providers.find(
      (p) => p.base_url && p.api_key && p.model
    );
    if (firstReady) {
      setCfg((c) => ({
        ...c,
        llm_providers: c.llm_providers.map((p) =>
          p.id === firstReady.id ? { ...p, enabled: true } : p
        ),
      }));
    }
  }, []);

  // 切到 Skills tab 时加载列表
  useEffect(() => {
    if (tab === "skills" && skills.length === 0 && !skillsLoading) {
      setSkillsLoading(true);
      listSkills()
        .then(setSkills)
        .catch(() => {})
        .finally(() => setSkillsLoading(false));
    }
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  // 切到 Memory tab 时加载列表
  useEffect(() => {
    if (tab === "memory" && !memLoading) {
      setMemLoading(true);
      listMemories()
        .then(setMemories)
        .catch(() => {})
        .finally(() => setMemLoading(false));
    }
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDeleteMemory = async (memId: string) => {
    try {
      await deleteMemory(memId);
      setMemories((prev) => prev.filter((m) => m.id !== memId));
    } catch {
      /* ignore */
    }
  };

  // 切换 skill 启用状态
  const toggleSkill = (name: string) => {
    setCfg((c) => {
      const disabled = new Set(c.disabled_skills);
      if (disabled.has(name)) disabled.delete(name);
      else disabled.add(name);
      return { ...c, disabled_skills: [...disabled] };
    });
    setSkills((prev) =>
      prev.map((s) => (s.name === name ? { ...s, enabled: !s.enabled } : s))
    );
  };

  // 切换 skill 来源
  const toggleLoadClaude = () =>
    setCfg((c) => ({ ...c, load_claude_skills: !c.load_claude_skills }));
  const toggleLoadProject = () =>
    setCfg((c) => ({ ...c, load_project_skills: !c.load_project_skills }));

  // 自定义 skill 目录
  const addSkillDir = () => {
    const p = skillDirInput.trim();
    if (!p) return;
    setCfg((c) => ({ ...c, skill_extra_dirs: [...c.skill_extra_dirs, p] }));
    setSkillDirInput("");
  };
  const removeSkillDir = (idx: number) =>
    setCfg((c) => ({
      ...c,
      skill_extra_dirs: c.skill_extra_dirs.filter((_, i) => i !== idx),
    }));

  // 展开/折叠 MCP server 工具探测
  const toggleMcpExpand = async (serverName: string) => {
    const expanded = new Set(mcpExpanded);
    if (expanded.has(serverName)) {
      expanded.delete(serverName);
      setMcpExpanded(expanded);
      return;
    }
    expanded.add(serverName);
    setMcpExpanded(expanded);

    // 首次展开时探测工具
    if (!mcpStatus[serverName] && !mcpProbing.has(serverName)) {
      const probing = new Set(mcpProbing);
      probing.add(serverName);
      setMcpProbing(probing);
      try {
        const status = await getMcpTools(serverName);
        setMcpStatus((prev) => ({ ...prev, [serverName]: status }));
      } catch {
        setMcpStatus((prev) => ({
          ...prev,
          [serverName]: {
            name: serverName, url: "", enabled: false,
            connected: false, tools: [], error: "探测失败",
          },
        }));
      } finally {
        probing.delete(serverName);
        setMcpProbing(probing);
      }
    }
  };

  const enabledCount = cfg.llm_providers.filter((p) => p.enabled).length;

  const updateProvider = (id: string, patch: Partial<LLMProvider>) => {
    setCfg((c) => ({
      ...c,
      llm_providers: c.llm_providers.map((p) => {
        if (p.id !== id) return p;
        // enabled 是单选: 启用当前则禁用其它
        if (patch.enabled && !p.enabled) {
          return { ...p, ...patch };
        }
        return { ...p, ...patch };
      }).map((p) => (patch.enabled && p.id !== id ? { ...p, enabled: false } : p)),
    }));
  };

  const removeProvider = (id: string) => {
    setCfg((c) => ({
      ...c,
      llm_providers: c.llm_providers.filter((p) => p.id !== id),
    }));
  };

  const updateMcp = (id: string, patch: Partial<MCPServer>) => {
    setCfg((c) => ({
      ...c,
      mcp_servers: c.mcp_servers.map((s) => (s.id === id ? { ...s, ...patch } : s)),
    }));
  };

  const removeMcp = (id: string) => {
    setCfg((c) => ({
      ...c,
      mcp_servers: c.mcp_servers.filter((s) => s.id !== id),
    }));
  };

  const handleSave = async () => {
    if (enabledCount === 0) {
      setError("至少需要启用一个 LLM Provider");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await saveSettings(toApiSettings(cfg));
      saveLocalCache(cfg);
      onSave(cfg);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const TabBtn = ({
    id,
    label,
  }: {
    id: typeof tab;
    label: string;
  }) => (
    <button
      onClick={() => setTab(id)}
      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
        tab === id ? "bg-accent text-inverse" : "text-muted hover:bg-hover"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[100]" onClick={onClose}>
      <div
        className="bg-card rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col border border-border"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b border-border">
          <h2 className="text-lg font-semibold text-default">设置</h2>
          <p className="text-xs text-faint mt-0.5">配置模型、MCP 与数据源, 保存到本机。</p>
        </div>

        <div className="px-6 pt-4 flex gap-2">
          <TabBtn id="models" label="模型" />
          <TabBtn id="mcp" label="MCP" />
          <TabBtn id="skills" label="Skills" />
          <TabBtn id="memory" label="记忆" />
          <TabBtn id="academic" label="学术" />
          <TabBtn id="general" label="通用" />
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {tab === "models" && (
            <div className="space-y-4">
              <div className="text-[10px] font-semibold text-faint uppercase tracking-wider">
                LLM Providers (启用 {enabledCount} 个)
              </div>
              {cfg.llm_providers.map((p) => (
                <div
                  key={p.id}
                  className={`p-4 rounded-xl border space-y-3 transition-colors ${
                    p.enabled
                      ? "bg-accent/5 border-accent/30"
                      : "bg-page border-border"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => updateProvider(p.id, { enabled: true })}
                      className={`px-2.5 py-1 rounded-md text-[10px] font-semibold uppercase tracking-wider transition-colors ${
                        p.enabled
                          ? "bg-accent text-inverse"
                          : "bg-card border border-border text-muted hover:text-default"
                      }`}
                      title="启用此 Provider"
                    >
                      {p.enabled ? "已启用" : "启用"}
                    </button>
                    <input
                      type="text"
                      value={p.name}
                      placeholder="名称 (如 large)"
                      onChange={(e) => updateProvider(p.id, { name: e.target.value })}
                      className="flex-1 px-2.5 py-1.5 bg-card rounded-md text-sm text-default placeholder:text-faint focus:outline-none input-glow border border-border"
                    />
                    <button
                      onClick={() => removeProvider(p.id)}
                      className="text-faint hover:text-error text-xs px-2"
                      title="删除"
                    >
                      删除
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                      <span className="text-[10px] text-faint">API Base URL</span>
                      <input
                        type="text"
                        value={p.base_url}
                        placeholder="https://api.example.com/v1"
                        onChange={(e) => updateProvider(p.id, { base_url: e.target.value })}
                        className="mt-1 w-full px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow border border-border"
                      />
                    </label>
                    <label className="block">
                      <span className="text-[10px] text-faint">模型名</span>
                      <input
                        type="text"
                        value={p.model}
                        placeholder="gpt-4o"
                        onChange={(e) => updateProvider(p.id, { model: e.target.value })}
                        className="mt-1 w-full px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow border border-border"
                      />
                    </label>
                    <label className="block">
                      <span className="text-[10px] text-faint">API Key</span>
                      <input
                        type="password"
                        value={p.api_key}
                        placeholder="sk-..."
                        onChange={(e) => updateProvider(p.id, { api_key: e.target.value })}
                        className="mt-1 w-full px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow border border-border"
                      />
                    </label>
                    <label className="block">
                      <span className="text-[10px] text-faint">上下文长度</span>
                      <input
                        type="number"
                        value={p.context_window}
                        placeholder="256000"
                        onChange={(e) => updateProvider(p.id, { context_window: Number(e.target.value) || 0 })}
                        className="mt-1 w-full px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow border border-border"
                      />
                    </label>
                  </div>
                </div>
              ))}
              <button
                onClick={() => setCfg((c) => ({ ...c, llm_providers: [...c.llm_providers, newProvider()] }))}
                className="w-full py-2 rounded-lg border border-dashed border-faint text-xs text-muted hover:text-default hover:border-muted transition-colors"
              >
                + 添加 Provider
              </button>
            </div>
          )}

          {tab === "mcp" && (
            <div className="space-y-4">
              <div className="text-[10px] font-semibold text-faint uppercase tracking-wider">MCP Servers</div>
              {cfg.mcp_servers.length === 0 && <div className="text-xs text-faint">暂无 MCP server</div>}
              {cfg.mcp_servers.map((s) => {
                const status = mcpStatus[s.name];
                const expanded = mcpExpanded.has(s.name);
                const probing = mcpProbing.has(s.name);
                return (
                  <div key={s.id} className="p-3 rounded-lg bg-page space-y-3">
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={s.enabled}
                        onChange={(e) => updateMcp(s.id, { enabled: e.target.checked })}
                        style={{ accentColor: "var(--accent)" }}
                      />
                      <input
                        type="text"
                        value={s.name}
                        placeholder="名称"
                        onChange={(e) => updateMcp(s.id, { name: e.target.value })}
                        className="flex-1 px-2.5 py-1.5 bg-card rounded-md text-sm text-default placeholder:text-faint focus:outline-none input-glow"
                      />
                      {/* 连接状态指示灯 */}
                      {s.url && status && (
                        <span
                          className={`w-2 h-2 rounded-full shrink-0 ${
                            status.connected ? "bg-success" : "bg-error"
                          }`}
                          title={status.connected ? "已连接" : status.error || "连接失败"}
                        />
                      )}
                      {s.url && !s.enabled && (
                        <span className="w-2 h-2 rounded-full bg-faint shrink-0" title="未启用" />
                      )}
                      {/* 工具列表展开按钮 */}
                      {s.url && s.name && (
                        <button
                          onClick={() => toggleMcpExpand(s.name)}
                          className="text-xs text-muted hover:text-default px-2 shrink-0"
                        >
                          {expanded ? "▼" : "▶"} 工具
                        </button>
                      )}
                      <button
                        onClick={() => removeMcp(s.id)}
                        className="text-faint hover:text-error text-xs px-2 shrink-0"
                      >
                        删除
                      </button>
                    </div>
                    <div className="grid grid-cols-1 gap-3">
                      <input
                        type="text"
                        value={s.url}
                        placeholder="MCP Server URL"
                        onChange={(e) => updateMcp(s.id, { url: e.target.value })}
                        className="px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow"
                      />
                      <input
                        type="password"
                        value={s.key}
                        placeholder="Authorization Bearer Token (可选)"
                        onChange={(e) => updateMcp(s.id, { key: e.target.value })}
                        className="px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow"
                      />
                    </div>
                    {/* 工具列表 */}
                    {expanded && s.name && (
                      <div className="mt-2 pt-2 border-t border-border">
                        {probing && (
                          <div className="text-xs text-faint animate-pulse">探测工具中…</div>
                        )}
                        {!probing && status && status.connected && (
                          <>
                            <div className="text-[10px] text-faint mb-1.5">
                              {status.tools.length} 个工具
                            </div>
                            <div className="space-y-1.5 max-h-48 overflow-y-auto">
                              {status.tools.map((t) => (
                                <div key={t.name} className="flex flex-col">
                                  <span className="text-xs font-mono text-accent">{t.name}</span>
                                  {t.description && (
                                    <span className="text-[10px] text-muted leading-snug">
                                      {t.description.slice(0, 200)}
                                      {t.description.length > 200 ? "…" : ""}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          </>
                        )}
                        {!probing && status && !status.connected && (
                          <div className="text-xs text-error">
                            连接失败{status.error ? `: ${status.error}` : ""}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              <button
                onClick={() => setCfg((c) => ({ ...c, mcp_servers: [...c.mcp_servers, newMcp()] }))}
                className="w-full py-2 rounded-lg border border-dashed border-faint text-xs text-muted hover:text-default hover:border-muted transition-colors"
              >
                + 添加 MCP Server
              </button>
            </div>
          )}

          {tab === "skills" && (
            <div className="space-y-4">
              {/* Skill 来源配置 */}
              <div className="text-[10px] font-semibold text-faint uppercase tracking-wider">
                Skill 来源
              </div>
              <div className="space-y-2">
                <label className="flex items-center gap-3 cursor-pointer p-3 rounded-lg bg-page">
                  <input
                    type="checkbox"
                    checked={cfg.load_claude_skills}
                    onChange={toggleLoadClaude}
                    className="w-4 h-4 rounded"
                    style={{ accentColor: "var(--accent)" }}
                  />
                  <div>
                    <div className="text-sm text-default">Claude skills</div>
                    <div className="text-[10px] text-faint">加载 ~/.claude/skills</div>
                  </div>
                </label>
                <label className="flex items-center gap-3 cursor-pointer p-3 rounded-lg bg-page">
                  <input
                    type="checkbox"
                    checked={cfg.load_project_skills}
                    onChange={toggleLoadProject}
                    className="w-4 h-4 rounded"
                    style={{ accentColor: "var(--accent)" }}
                  />
                  <div>
                    <div className="text-sm text-default">项目 skills</div>
                    <div className="text-[10px] text-faint">加载 workspace/.axiom/skills</div>
                  </div>
                </label>
                <div className="p-3 rounded-lg bg-page space-y-2">
                  <div className="text-sm text-default">自定义目录</div>
                  <div className="text-[10px] text-faint">添加额外的 skill 目录路径</div>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={skillDirInput}
                      onChange={(e) => setSkillDirInput(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && addSkillDir()}
                      placeholder="/path/to/skills"
                      className="flex-1 px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow border border-border"
                    />
                    <button
                      onClick={addSkillDir}
                      className="px-3 py-1.5 rounded-md bg-elevated hover:bg-hover text-xs text-muted hover:text-default transition-colors"
                    >
                      添加
                    </button>
                  </div>
                  {cfg.skill_extra_dirs.length === 0 && (
                    <div className="text-xs text-faint">暂无自定义目录</div>
                  )}
                  {cfg.skill_extra_dirs.map((p, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <span className="flex-1 text-xs font-mono text-muted truncate">{p}</span>
                      <button
                        onClick={() => removeSkillDir(idx)}
                        className="text-faint hover:text-error text-xs px-2"
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border-t border-border pt-4 space-y-4">
                <div className="text-[10px] font-semibold text-faint uppercase tracking-wider">
                  Skills ({skills.length})
                </div>
                {/* 搜索框 */}
                <input
                  type="text"
                  value={skillSearch}
                  onChange={(e) => setSkillSearch(e.target.value)}
                  placeholder="搜索 skill…"
                  className="w-full px-3 py-2 bg-page rounded-lg text-sm text-default placeholder:text-faint focus:outline-none input-glow"
                />
                {skillsLoading && (
                  <div className="text-xs text-faint animate-pulse">加载中…</div>
                )}
                {/* 分组 skills */}
                {(() => {
                  const filtered = skills.filter(
                    (s) =>
                      !skillSearch ||
                      s.name.toLowerCase().includes(skillSearch.toLowerCase()) ||
                      s.description.toLowerCase().includes(skillSearch.toLowerCase())
                  );
                  const groups: { key: SkillInfo["source"]; label: string }[] = [
                    { key: "anthropic", label: "内置 Skills" },
                    { key: "global", label: "全局 Skills" },
                    { key: "claude", label: "Claude Skills" },
                    { key: "custom", label: "自定义 Skills" },
                  ];
                  return (
                    <>
                      {groups.map(({ key, label }) => {
                        const items = filtered.filter((s) => s.source === key);
                        if (items.length === 0) return null;
                        return (
                          <div key={key}>
                            <div className="text-[10px] text-faint mt-2">{label}</div>
                            {items.map((s) => (
                              <SkillCard key={s.name} skill={s} onToggle={toggleSkill} />
                            ))}
                          </div>
                        );
                      })}
                      {filtered.length === 0 && !skillsLoading && (
                        <div className="text-xs text-faint">无匹配 skill</div>
                      )}
                    </>
                  );
                })()}
              </div>
            </div>
          )}

          {tab === "memory" && (
            <div className="space-y-4">
              <div className="text-[10px] font-semibold text-faint uppercase tracking-wider">
                记忆 ({memories.length})
              </div>
              <p className="text-xs text-muted">
                Agent 从对话中自动提取的持久记忆。Profile 层跨所有会话生效，Project 层跨同项目会话，Frame 层随会话删除。
              </p>
              {memLoading && <div className="text-xs text-faint animate-pulse">加载中…</div>}
              {!memLoading && memories.length === 0 && (
                <div className="text-xs text-faint">暂无记忆 — 对话中 Agent 会自动学习</div>
              )}
              {memories.map((m) => (
                <div key={m.id} className="flex items-start gap-3 p-3 rounded-lg bg-page">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-[9px] px-1.5 py-0.5 rounded uppercase tracking-wide ${
                        m.entity === "profile" ? "bg-accent/15 text-accent" :
                        m.entity === "project" ? "bg-info/15 text-info" :
                        "bg-elevated text-muted"
                      }`}>
                        {m.entity}
                      </span>
                      <span className="text-[9px] text-faint">{m.evidence}</span>
                      <span className="text-[9px] text-faint">· {m.origin}</span>
                    </div>
                    <p className="text-xs text-default leading-snug">{m.body}</p>
                  </div>
                  <button
                    onClick={() => handleDeleteMemory(m.id)}
                    className="text-[10px] text-faint hover:text-error shrink-0"
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
          )}

          {tab === "academic" && (
            <div className="space-y-4">
              <div className="text-[10px] font-semibold text-faint uppercase tracking-wider">学术数据源</div>
              <label className="block">
                <span className="text-xs font-medium text-muted">OpenAlex API Key</span>
                <input
                  type="password"
                  value={cfg.api_keys.OPENALEX_API_KEY || ""}
                  placeholder="(可选)"
                  onChange={(e) =>
                    setCfg((c) => ({
                      ...c,
                      api_keys: { ...c.api_keys, OPENALEX_API_KEY: e.target.value },
                    }))
                  }
                  className="mt-1.5 w-full px-3 py-2 bg-page rounded-lg text-sm text-default placeholder:text-faint focus:outline-none input-glow font-mono"
                />
              </label>
            </div>
          )}

          {tab === "general" && (
            <div className="space-y-4">
              <div className="text-[10px] font-semibold text-faint uppercase tracking-wider">通用</div>
              <div className="p-3 rounded-lg bg-page">
                <div className="text-sm text-default">工作区</div>
                <div className="text-[10px] text-faint mt-0.5">
                  每个会话自动创建独立工作区目录，无需手动配置
                </div>
              </div>
              <label className="flex items-center gap-3 cursor-pointer p-3 rounded-lg bg-page">
                <input
                  type="checkbox"
                  checked={cfg.plan_mode}
                  onChange={(e) => setCfg((c) => ({ ...c, plan_mode: e.target.checked }))}
                  className="w-4 h-4 rounded" style={{ accentColor: "var(--accent)" }}
                />
                <div>
                  <div className="text-sm text-default">Plan Mode</div>
                  <div className="text-[10px] text-faint">先规划后执行</div>
                </div>
              </label>

              {/* 审稿 / 收敛审查 */}
              <div className="p-3 rounded-lg bg-page space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm text-default">审稿 (Verification)</div>
                    <div className="text-[10px] text-faint">长任务中自动触发 reviewer checkpoint, 检查引用与跑偏</div>
                  </div>
                  <input
                    type="checkbox"
                    checked={cfg.verification.enabled}
                    onChange={(e) =>
                      setCfg((c) => ({
                        ...c,
                        verification: { ...c.verification, enabled: e.target.checked },
                      }))
                    }
                    className="w-4 h-4 rounded"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                <label className="block">
                  <span className="text-[10px] text-faint">Reviewer 模型 (可选)</span>
                  <input
                    type="text"
                    value={cfg.verification.reviewer_model || ""}
                    placeholder="不填则使用主模型"
                    onChange={(e) =>
                      setCfg((c) => ({
                        ...c,
                        verification: { ...c.verification, reviewer_model: e.target.value || undefined },
                      }))
                    }
                    className="mt-1 w-full px-2.5 py-1.5 bg-card rounded-md text-xs font-mono text-default placeholder:text-faint focus:outline-none input-glow border border-border"
                  />
                </label>
              </div>

              {/* Trace 可观测 */}
              <div className="p-3 rounded-lg bg-page space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm text-default">Trace (可观测)</div>
                    <div className="text-[10px] text-faint">
                      落盘 LLM/工具调用 span 到 ~/.axiom/trace/ (JSONL), 便于调试 agent 行为
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    checked={cfg.trace.enabled}
                    onChange={(e) =>
                      setCfg((c) => ({
                        ...c,
                        trace: { ...c.trace, enabled: e.target.checked },
                      }))
                    }
                    className="w-4 h-4 rounded"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                {cfg.trace.enabled && (
                  <>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={!!cfg.trace.log_llm_payload}
                        onChange={(e) =>
                          setCfg((c) => ({
                            ...c,
                            trace: { ...c.trace, log_llm_payload: e.target.checked },
                          }))
                        }
                        className="w-3.5 h-3.5 rounded"
                        style={{ accentColor: "var(--accent)" }}
                      />
                      <span className="text-xs text-muted">
                        记录 LLM 输入/输出全文 (含敏感内容, 慎用)
                      </span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={cfg.trace.log_tool_args !== false}
                        onChange={(e) =>
                          setCfg((c) => ({
                            ...c,
                            trace: { ...c.trace, log_tool_args: e.target.checked },
                          }))
                        }
                        className="w-3.5 h-3.5 rounded"
                        style={{ accentColor: "var(--accent)" }}
                      />
                      <span className="text-xs text-muted">记录工具调用参数</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={cfg.trace.log_tool_result_summary !== false}
                        onChange={(e) =>
                          setCfg((c) => ({
                            ...c,
                            trace: { ...c.trace, log_tool_result_summary: e.target.checked },
                          }))
                        }
                        className="w-3.5 h-3.5 rounded"
                        style={{ accentColor: "var(--accent)" }}
                      />
                      <span className="text-xs text-muted">记录工具结果摘要 (前 200 字符)</span>
                    </label>
                  </>
                )}
              </div>

              <UpdateCheck />
            </div>
          )}

          {error && <div className="text-xs text-error mt-3">{error}</div>}
        </div>

        <div className="p-6 border-t border-border flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 py-2.5 rounded-lg bg-page text-sm text-muted hover:bg-hover transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 py-2.5 rounded-lg bg-accent hover:bg-accent-hover disabled:bg-card disabled:text-faint text-inverse text-sm font-medium shadow-md transition-all active:scale-[0.98]"
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SkillCard({
  skill,
  onToggle,
}: {
  skill: SkillInfo;
  onToggle: (name: string) => void;
}) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg bg-page">
      <button
        onClick={() => onToggle(skill.name)}
        className={`mt-0.5 shrink-0 w-9 h-5 rounded-full transition-colors relative ${
          skill.enabled ? "bg-accent" : "bg-border"
        }`}
        title={skill.enabled ? "已启用 — 点击禁用" : "已禁用 — 点击启用"}
      >
        <span
          className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
            skill.enabled ? "left-[18px]" : "left-0.5"
          }`}
        />
      </button>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-mono text-default">{skill.name}</span>
          {skill.source !== "anthropic" && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent uppercase tracking-wide">
              {skill.source === "global" && "全局"}
              {skill.source === "claude" && "Claude"}
              {skill.source === "project" && "项目"}
              {skill.source === "custom" && "自定义"}
            </span>
          )}
        </div>
        <p className="text-xs text-muted leading-snug mt-0.5 line-clamp-2">
          {skill.description}
        </p>
      </div>
    </div>
  );
}

function UpdateCheck() {
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const check = async () => {
    setChecking(true);
    setResult(null);
    try {
      const result = await invoke<[string, string] | null>("check_update", {
        current: CURRENT_VERSION,
      });
      if (result) {
        setResult(`发现新版本 ${result[0]} (当前 ${CURRENT_VERSION})`);
        window.open(result[1], "_blank");
      } else {
        setResult(`已是最新版本 (${CURRENT_VERSION})`);
      }
    } catch (e) {
      setResult(`检查更新失败: ${e}`);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="p-3 rounded-lg bg-page">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm text-default">版本 {CURRENT_VERSION}</div>
          <div className="text-[10px] text-faint mt-0.5">
            {result || "检查是否有新版本"}
          </div>
        </div>
        <button
          onClick={check}
          disabled={checking}
          className="text-xs px-3 py-1.5 rounded-lg bg-elevated hover:bg-hover text-muted hover:text-default transition-colors disabled:opacity-50"
        >
          {checking ? "检查中…" : "检查更新"}
        </button>
      </div>
    </div>
  );
}
