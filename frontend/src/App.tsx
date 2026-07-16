// 主应用: Axiom 桌面工作台。
// 左: 项目/会话侧边栏 | 中: 对话流 | 右: 工作区 + 验证
// 支持月之亮面/暗面主题切换。

import { useCallback, useEffect, useRef, useState } from "react";
import { approvePlan, createSession, deleteFile, deleteSession, getSessionState, getSettings, health, listSessions, apiBase } from "./api";
import { useSession } from "./hooks/useSession";
import { useTheme } from "./hooks/useTheme";
import { MessageView } from "./components/Message";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { PlanPanel } from "./components/PlanPanel";
import { ResizableSidebar } from "./components/ResizableSidebar";
import { SettingsModal, fromApiSettings, loadConfig, type FullConfig } from "./components/SettingsModal";
import { PaperView } from "./components/PaperView";
import { UpdateBanner } from "./components/UpdateBanner";
import type { SessionInfo } from "./types";

const SID_KEY = "operon-py-active-sid";

function saveSid(sid: string | null) {
  if (sid) localStorage.setItem(SID_KEY, sid);
  else localStorage.removeItem(SID_KEY);
}

function loadSid(): string | null {
  return localStorage.getItem(SID_KEY);
}

function usePaperRoute(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/^\/paper\/([^/]+)/);
  return m ? m[1] : null;
}

export default function App() {
  const paperSid = usePaperRoute();
  if (paperSid) {
    return <PaperView sid={paperSid} onClose={() => window.history.back()} />;
  }
  return <Workbench />;
}

function isProviderReady(p: FullConfig["llm_providers"][0]) {
  return p.enabled && p.base_url && p.api_key && p.model;
}

function Workbench() {
  const session = useSession();
  const { resolvedTheme, toggleTheme } = useTheme();
  const [sid, setSidRaw] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [config, setConfig] = useState<FullConfig>(loadConfig());
  const [serverConfigured, setServerConfigured] = useState<boolean | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showPaper, setShowPaper] = useState(false);
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const [input, setInput] = useState("");
  const [planMode, setPlanMode] = useState(false);
  const [deepReview, setDeepReview] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<"sessions" | "files">("sessions");
  const scrollRef = useRef<HTMLDivElement>(null);
  const restoredRef = useRef(false);

  const setSid = useCallback((newSid: string | null) => {
    setSidRaw(newSid);
    saveSid(newSid);
  }, []);

  const refreshSessionList = useCallback(async () => {
    try {
      const list = await listSessions();
      setSessions(list);
      return list;
    } catch {
      return [];
    }
  }, []);

  // 探测后端 + 同步配置 + 恢复会话
  // 启动时后端可能还没ready, 所以用轮询而不是单次探测; 运行期间也持续心跳。
  useEffect(() => {
    let mounted = true;
    let settingsLoaded = false;

    const tryConnect = async () => {
      if (!mounted) return;
      try {
        await health();
        if (!mounted) return;
        setBackendUp(true);

        if (!settingsLoaded) {
          settingsLoaded = true;
          const raw = await getSettings();
          if (!mounted) return;
          const saved = fromApiSettings(raw);
          setConfig(saved);
          const hasEnabled = saved.llm_providers.some(isProviderReady);
          setServerConfigured(hasEnabled);
          if (!hasEnabled) setShowSettings(true);

          if (!restoredRef.current) {
            restoredRef.current = true;
            refreshSessionList().then(async (list) => {
              const savedSid = loadSid();
              if (savedSid && list.some((s) => s.id === savedSid)) {
                try {
                  const state = await getSessionState(savedSid);
                  setSid(savedSid);
                  session.loadFromState(state);
                } catch {
                  saveSid(null);
                }
              }
            });
          }
        }
      } catch {
        if (!mounted) return;
        setBackendUp(false);
        settingsLoaded = false;
      }
    };

    // 首次立即探测, 之后每 2 秒心跳
    tryConnect();
    const interval = setInterval(tryConnect, 2000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 自动滚动到底部 (用 rAF 节流, 避免流式 text 每帧触发 smooth scroll)
  const scrollRafRef = useRef<number | null>(null);
  useEffect(() => {
    if (scrollRafRef.current != null) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null;
      const el = scrollRef.current;
      if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    });
    return () => {
      if (scrollRafRef.current != null) {
        cancelAnimationFrame(scrollRafRef.current);
        scrollRafRef.current = null;
      }
    };
  }, [session.messages]);

  const ensureSession = async (): Promise<string> => {
    if (sid) return sid;
    const body: Record<string, unknown> = {};
    const primary = config.llm_providers.find(isProviderReady);

    // 若后端未持久化有效配置, 用前端当前配置兜底创建会话
    if (!serverConfigured && primary) {
      body.base_url = primary.base_url;
      body.api_key = primary.api_key;
      body.model = primary.model;
      body.context_window = Number(primary.context_window) || 256000;
      const enabledMcps = config.mcp_servers.filter((s) => s.enabled && s.url);
      if (enabledMcps.length > 0) {
        body.mcp_servers = enabledMcps.map((s) => ({
          name: s.name || s.id,
          url: s.url,
          headers: s.key ? { Authorization: `Bearer ${s.key}` } : {},
        }));
      }
      if (config.api_keys.OPENALEX_API_KEY) {
        body.api_keys = { OPENALEX_API_KEY: config.api_keys.OPENALEX_API_KEY };
      }
    }
    if (config.plan_mode) body.plan_mode = true;
    if (config.disabled_skills && config.disabled_skills.length > 0) {
      body.disabled_skills = config.disabled_skills;
    }
    try {
      const data = await createSession(body);
      setSid(data.id);
      refreshSessionList();
      return data.id;
    } catch (e) {
      alert(`创建会话失败: ${e instanceof Error ? e.message : e}`);
      throw e;
    }
  };

  const send = async () => {
    let prompt = input.trim();
    if (!prompt || session.status === "running") return;

    // /plan 前缀: 单条消息强制 plan mode
    let usePlanMode = planMode;
    if (prompt.startsWith("/plan ")) {
      prompt = prompt.slice(6).trim();
      usePlanMode = true;
    }

    setInput("");
    try {
      const id = await ensureSession();
      session.start(id, prompt, usePlanMode, deepReview);
    } catch (e) {
      session.reset();
      alert(`创建会话失败: ${e instanceof Error ? e.message : e}`);
    }
  };

  const switchSession = async (targetSid: string) => {
    if (targetSid === sid) return;
    try {
      const state = await getSessionState(targetSid);
      session.loadFromState(state);
      setSid(targetSid);
    } catch {
      alert("无法加载该会话");
    }
  };

  const handleDeleteSession = async (targetSid: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteSession(targetSid);
      if (targetSid === sid) {
        session.reset();
        setSid(null);
      }
      refreshSessionList();
    } catch {
      alert("删除失败");
    }
  };

  const handleNewSession = () => {
    session.reset();
    setSid(null);
  };

  const onApprove = async () => {
    if (!sid) return;
    try {
      await approvePlan(sid);
      session.setPlan((p) => (p ? { ...p, approved: true } : p));
      session.setAwaiting(null);
      session.setStatus("idle");
      // 自动继续执行已批准的计划,无需用户再手动发消息
      const resumePrompt = "继续执行已批准的计划。";
      session.start(sid, resumePrompt);
    } catch (e) {
      alert(`批准失败: ${e instanceof Error ? e.message : e}`);
    }
  };

  return (
    <div className="h-full flex flex-col bg-page text-default theme-transition">
      <UpdateBanner />
      {/* 顶部标题栏 - SciForge 风格 */}
      <header className="h-12 bg-subtle flex items-center justify-between px-3 shrink-0 z-20 border-b border-border">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center">
            <LogoIcon width={16} height={16} className="text-accent" />
          </div>
          <div className="font-semibold text-sm tracking-tight">Axiom</div>
        </div>

        <div className="flex items-center gap-1.5">
          <BackendBadge up={backendUp} />
          <ModelBadge config={config} />
          <button
            onClick={toggleTheme}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-muted hover:bg-hover/60 transition-colors"
            title={resolvedTheme === "dark" ? "切换到月之亮面" : "切换到月之暗面"}
          >
            {resolvedTheme === "dark" ? <MoonIcon /> : <SunIcon />}
          </button>
          <button
            onClick={() => setShowSettings(true)}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-muted hover:bg-hover/60 transition-colors"
            title="设置"
          >
            <SettingsIcon />
          </button>
        </div>
      </header>

      {/* 三栏主体 */}
      <div className="flex-1 flex overflow-hidden">
        {/* 左: 项目/会话侧边栏 - SciForge 风格 */}
        <ResizableSidebar side="left" defaultWidth={224} minWidth={180} maxWidth={400} storageKey="left">
          <div className="p-2.5">
            <button
              onClick={handleNewSession}
              className="w-full h-9 rounded-lg bg-accent hover:bg-accent-hover text-inverse text-sm font-medium shadow-sm transition-all active:scale-[0.98] flex items-center justify-center gap-1.5"
            >
              <PlusIcon width={14} height={14} />
              新会话
            </button>
          </div>

          <div className="px-2.5 py-2">
            <div className="text-[10px] font-medium text-faint uppercase tracking-wider px-2 mb-1.5">工作区</div>
            <nav className="space-y-0.5">
              <SidebarItem icon={<ChatIcon />} label="会话" active={sidebarTab === "sessions"} onClick={() => setSidebarTab("sessions")} />
              <SidebarItem icon={<FolderIcon />} label="项目文件" active={sidebarTab === "files"} onClick={() => setSidebarTab("files")} />
            </nav>
          </div>

          <div className="flex-1 overflow-y-auto px-2.5 py-1 min-h-0">
            {sidebarTab === "sessions" ? (
              sessions.length === 0 ? (
                <EmptyState icon="" text="暂无会话" sub="点击上方开始新会话" />
              ) : (
                <div className="space-y-0.5">
                  {sessions.map((s) => (
                    <SessionItem
                      key={s.id}
                      info={s}
                      active={s.id === sid}
                      running={s.id === sid && session.status === "running"}
                      iteration={s.id === sid ? session.iteration : 0}
                      onClick={() => switchSession(s.id)}
                      onDelete={(e) => handleDeleteSession(s.id, e)}
                    />
                  ))}
                </div>
              )
            ) : (
              <ProjectTree sid={sid} />
            )}
          </div>

          <div className="p-2.5 mt-auto">
            <UsageFooter usage={session.usage} />
          </div>
        </ResizableSidebar>

        {/* 中: 对话流 */}
        <main className="flex-1 flex flex-col min-w-0 relative">
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto px-6 py-6 pb-32 scroll-smooth"
          >
            {session.messages.length === 0 ? (
              <Welcome onPick={(t) => setInput(t)} />
            ) : (
              <div className="max-w-3xl mx-auto space-y-6">
                {session.messages.map((m) => (
                  <MessageView key={m.id} msg={m} />
                ))}
              </div>
            )}
            {session.error && (
              <div className="max-w-3xl mx-auto my-4 p-4 rounded-lg bg-error/10 text-sm text-error">
                ⚠ {session.error}
              </div>
            )}
          </div>

          {/* 悬浮输入区 - SciForge 风格药丸条 */}
          <div className="absolute bottom-5 left-0 right-0 px-6">
            <div className="max-w-3xl mx-auto">
              <div className="floating-input p-2">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  placeholder="输入任务或问题..."
                  rows={1}
                  className="w-full px-3 py-2 bg-transparent text-sm text-default placeholder:text-faint resize-none focus:outline-none min-h-[40px] max-h-[160px]"
                />
                <div className="flex items-center justify-between px-2 pt-1">
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setPlanMode((v) => !v)}
                      className={`text-[10px] px-2 py-1 rounded-md font-medium transition-colors ${
                        planMode
                          ? "bg-accent/15 text-accent"
                          : "text-faint hover:text-muted hover:bg-hover"
                      }`}
                      title="Plan Mode: 先规划后执行"
                    >
                      ◇ Plan
                    </button>
                    <button
                      onClick={() => setDeepReview((v) => !v)}
                      className={`text-[10px] px-2 py-1 rounded-md font-medium transition-colors ${
                        deepReview
                          ? "bg-accent-secondary/15 text-accent-secondary"
                          : "text-faint hover:text-muted hover:bg-hover"
                      }`}
                      title="深度综述: 强制走 paper-writing 多阶段流程 (文献评分→结构→实验→图表→同行评审迭代)"
                    >
                      ★ Review
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-faint">
                      {config.llm_providers.find((p) => p.enabled)?.model || "未配置"}
                    </span>
                    <button
                      onClick={send}
                      disabled={!input.trim() || session.status === "running"}
                      className="w-8 h-8 rounded-full bg-accent hover:bg-accent-hover disabled:bg-card disabled:text-faint text-inverse flex items-center justify-center transition-all active:scale-[0.98]"
                    >
                      <SendIcon />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </main>

        {/* 右: 工作区 + 验证 */}
        <ResizableSidebar side="right" defaultWidth={256} minWidth={200} maxWidth={480} storageKey="right">
          <div className="flex-1 overflow-hidden">
            <WorkspacePanel
              artifacts={session.artifacts}
              sid={sid}
              onViewPaper={() => setShowPaper(true)}
            />
          </div>
          <div className="flex-1 overflow-hidden">
            <PlanPanel
              plan={session.plan}
              status={session.status}
              awaiting={session.awaiting}
              onApprove={onApprove}
            />
          </div>
        </ResizableSidebar>
      </div>

      {/* 底部状态栏 - SciForge 风格 */}
      <footer className="h-7 bg-subtle flex items-center justify-between px-3 text-[11px] text-faint shrink-0 shadow-[0_-1px_0_0_rgba(15,23,42,0.04)]">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5">
            <GitBranchIcon />
            No Git repo
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span>{backendUp ? "服务就绪" : "服务离线"}</span>
          <span>Local runtime</span>
        </div>
      </footer>

      {showSettings && (
        <SettingsModal
          initial={config}
          onClose={() => setShowSettings(false)}
          onSave={(c) => {
            setConfig(c);
            setServerConfigured(c.llm_providers.some(isProviderReady));
            setShowSettings(false);
            setBackendUp(null);
            health().then(() => setBackendUp(true)).catch(() => setBackendUp(false));
          }}
        />
      )}

      {showPaper && sid && <PaperView sid={sid} onClose={() => setShowPaper(false)} />}
    </div>
  );
}

function SidebarItem({
  icon,
  label,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
        active
          ? "bg-accent/15 text-default"
          : "text-muted hover:bg-hover hover:text-default"
      }`}
    >
      <span className={active ? "text-accent" : "text-faint"}>{icon}</span>
      {label}
    </button>
  );
}

function SessionItem({
  info,
  active,
  running,
  iteration,
  onClick,
  onDelete,
}: {
  info: SessionInfo;
  active: boolean;
  running: boolean;
  iteration: number;
  onClick: () => void;
  onDelete: (e: React.MouseEvent) => void;
}) {
  const label = info.title || `会话 ${info.id.slice(0, 8)}`;
  const timeStr = info.created_at
    ? new Date(info.created_at).toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

  return (
    <div
      onClick={onClick}
      className={`group flex items-start gap-2 px-2.5 py-2 cursor-pointer rounded-lg transition-all ${
        active
          ? "bg-accent/15"
          : "hover:bg-hover"
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className={`text-[13px] truncate leading-tight ${active ? "font-medium text-default" : "text-muted"}`}>
          {label}
        </div>
        <div className="flex items-center gap-1.5 mt-0.5">
          {running && (
            <span className="text-[10px] text-accent animate-pulse flex items-center gap-1">
              <span className="w-1 h-1 rounded-full bg-accent" />
              轮次 {iteration}
            </span>
          )}
          {!running && info.live && (
            <span className="w-1 h-1 rounded-full bg-success shrink-0" />
          )}
          <span className="text-[10px] text-faint">{timeStr}</span>
        </div>
      </div>
      <button
        onClick={onDelete}
        className="opacity-0 group-hover:opacity-100 shrink-0 mt-0.5 p-0.5 text-faint hover:text-error rounded transition-all"
        title="删除"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}

function ProjectTree({ sid }: { sid: string | null }) {
  const [files, setFiles] = useState<{ path: string; size: number; name: string }[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => {
    if (!sid) {
      setFiles([]);
      return;
    }
    let alive = true;
    const poll = () => {
      fetch(`${apiBase()}/sessions/${sid}/files`)
        .then((r) => r.json())
        .then((d) => {
          if (alive) setFiles(d.files || []);
        })
        .catch(() => {});
    };
    poll();
    const iv = setInterval(poll, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [sid, refreshTick]);

  const handleDelete = async (path: string) => {
    if (!sid) return;
    if (!confirm(`删除 ${path}?`)) return;
    try {
      await deleteFile(sid, path);
      setRefreshTick((n) => n + 1);
    } catch (e) {
      alert(`删除失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  if (!sid) {
    return <EmptyState icon="📁" text="未选择项目" sub="先创建一个会话" />;
  }
  if (files.length === 0) {
    return <EmptyState icon="📂" text="项目为空" sub="Agent 写入的文件会显示在这里" />;
  }

  return (
    <div className="space-y-0.5">
      {files.map((f) => (
        <div
          key={f.path}
          className="group flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-hover cursor-pointer text-xs"
          onClick={() => window.open(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(f.path)}`, "_blank")}
        >
          <FileIcon path={f.path} />
          <span className="flex-1 truncate text-muted">{f.name}</span>
          <button
            onClick={(e) => {
              e.stopPropagation();
              handleDelete(f.path);
            }}
            className="opacity-0 group-hover:opacity-100 text-faint hover:text-error p-0.5 rounded transition-all"
            title="删除"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              <line x1="10" y1="11" x2="10" y2="17" />
              <line x1="14" y1="11" x2="14" y2="17" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
}

function FileIcon({ path }: { path: string }) {
  const ext = path.split(".").pop()?.toLowerCase();
  const color =
    ext && ["py", "js", "ts", "tsx", "jsx"].includes(ext)
      ? "text-accent"
      : ext && ["md", "txt"].includes(ext)
      ? "text-muted"
      : ext && ["pdf", "tex"].includes(ext)
      ? "text-error"
      : "text-faint";
  return (
    <svg className={`w-4 h-4 ${color}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M16 13H8" />
      <path d="M16 17H8" />
      <path d="M10 9H8" />
    </svg>
  );
}

function BackendBadge({ up }: { up: boolean | null }) {
  if (up === null) return <span className="text-[11px] text-faint">检测中…</span>;
  return (
    <span className={`text-[11px] flex items-center gap-1.5 px-2 py-1 rounded-full ${up ? "text-success bg-success/15" : "text-error bg-error/15"}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${up ? "bg-success" : "bg-error"} ${up ? "animate-pulse" : ""}`} />
      {up ? "服务在线" : "服务离线"}
    </span>
  );
}

function ModelBadge({ config }: { config: FullConfig }) {
  const primary = config.llm_providers.find((p) => p.enabled);
  return (
    <span className="text-[11px] text-muted font-mono px-2 py-1 rounded-full bg-page truncate max-w-[120px]">
      {primary ? primary.model : "未配置"}
    </span>
  );
}

function UsageFooter({ usage }: { usage: { in: number; out: number } }) {
  return (
    <div className="text-[10px] text-faint flex items-center justify-between">
      <span>Tokens</span>
      <span className="font-mono">
        ↑{usage.in} ↓{usage.out}
      </span>
    </div>
  );
}

function Welcome({ onPick }: { onPick: (t: string) => void }) {
  const examples = [
    "推导史瓦西黑洞的霍金辐射温度公式，并画出 T-M 关系曲线",
    "调研量子纠错码最新进展，整理一份带参考文献的综述",
    "模拟致密天体周围的引力透镜效应，可视化爱因斯坦环",
  ];
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-6 min-h-[60vh]">
      <div className="w-16 h-16 rounded-2xl bg-accent/10 flex items-center justify-center mb-6">
        <LogoIcon width={32} height={32} className="text-accent" />
      </div>

      <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-accent/15 text-accent text-[10px] font-semibold uppercase tracking-wider mb-4">
        <SparkleIcon />
        AI Research Core
      </div>
      <h1 className="text-3xl font-semibold text-default mb-2 tracking-tight">Axiom</h1>
      <p className="text-sm text-muted mb-8 max-w-md">
        本地科研 AI 工作台。文献综述、数据分析、数学建模、代码实验，全流程在你电脑上完成。
      </p>

      <div className="flex flex-wrap justify-center gap-2 max-w-xl">
        {examples.map((t) => (
          <button
            key={t}
            onClick={() => onPick(t)}
            className="pill-btn"
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}

function EmptyState({ icon, text, sub }: { icon: string; text: string; sub: string }) {
  return (
    <div className="text-center py-10 px-4">
      {icon && <div className="text-2xl mb-2 opacity-40">{icon}</div>}
      <div className="text-[13px] text-muted">{text}</div>
      <div className="text-[11px] text-faint mt-1">{sub}</div>
    </div>
  );
}

// SVG 图标组件
function LogoIcon({ width = 16, height = 16, className = "" }: { width?: number; height?: number; className?: string }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" className={className} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2L2 7l10 5 10-5-10-5z" />
      <path d="M2 17l10 5 10-5" />
      <path d="M2 12l10 5 10-5" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function PlusIcon({ width = 14, height = 14 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function SparkleIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3L14.5 8.5L20 11L14.5 13.5L12 19L9.5 13.5L4 11L9.5 8.5L12 3Z" />
    </svg>
  );
}

function GitBranchIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  );
}
