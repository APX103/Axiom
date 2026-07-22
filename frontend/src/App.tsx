// 主应用: Axiom 桌面工作台。
// 左: 项目/会话侧边栏 | 中: 对话流 | 右: 工作区 + 验证
// 支持月之亮面/暗面主题切换。

import { useCallback, useEffect, useRef, useState } from "react";
import { approvePlan, createProject, createSession, deleteFile, deleteSession, getSessionState, getSettings, health, listProjects, listSessions, listTemplates, updateProject, apiBase } from "./api";
import { useSession } from "./hooks/useSession";
import { useTheme } from "./hooks/useTheme";
import { MessageView } from "./components/Message";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { PlanPanel } from "./components/PlanPanel";
import { ResizableSidebar } from "./components/ResizableSidebar";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { ProjectFormModal } from "./components/ProjectFormModal";
import { ProjectManagerModal, type ProjectManagerEvent } from "./components/ProjectManagerModal";
import { SettingsModal, fromApiSettings, loadConfig, type FullConfig } from "./components/SettingsModal";
import { PaperView } from "./components/PaperView";
import { UpdateBanner } from "./components/UpdateBanner";
import type { ProjectInfo, SessionInfo, TemplateInfo } from "./types";

const SID_KEY = "operon-py-active-sid";

type BackendStatus = "checking" | "waiting" | "online" | "offline";

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
  const [showSettings, setShowSettings] = useState(false);
  const [showPaper, setShowPaper] = useState(false);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");
  const backendStatusRef = useRef(backendStatus);
  useEffect(() => {
    backendStatusRef.current = backendStatus;
  }, [backendStatus]);
  const backendWaitStartRef = useRef<number | null>(null);
  const [input, setInput] = useState("");
  const [planMode, setPlanMode] = useState(false);
  const [deepReview, setDeepReview] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<"sessions" | "files">("sessions");
  // Layer A.5: project 切换器
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const [showProjectModal, setShowProjectModal] = useState(false);
  // 编辑模式时传入的 project; null=新建
  const [projectModalInitial, setProjectModalInitial] = useState<ProjectInfo | null>(null);
  const [projectDropdownOpen, setProjectDropdownOpen] = useState(false);
  // 独立项目管理面板 (新建/重命名/归档/恢复/删除的统一入口)
  const [showProjectManager, setShowProjectManager] = useState(false);
  // 论文模板 (新建会话时复制进工作区作为 main.tex preamble)
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>("article");
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

  // Layer A.5: 加载 project 列表 + 初始化默认 project + 恢复上次选中
  const refreshProjects = useCallback(async () => {
    try {
      let list = await listProjects();
      // 列表为空时自动创建默认 project (后端迁移会建, 这里双保险)
      if (list.length === 0) {
        await createProject("默认项目");
        list = await listProjects();
      }
      setProjects(list);
      // 恢复上次选中的 project (localStorage 持久化)
      const savedPid = localStorage.getItem("axiom_current_project_id");
      const exists = list.some((p) => p.id === savedPid);
      if (exists) {
        setCurrentProjectId(savedPid);
      } else {
        // 默认选 proj_default (或第一个)
        const def = list.find((p) => p.is_default) ?? list[0];
        if (def) {
          setCurrentProjectId(def.id);
          localStorage.setItem("axiom_current_project_id", def.id);
        }
      }
      return list;
    } catch (e) {
      // 后端启动期可能还未就绪, 此处静默失败; 后端转为 online 后会在下方 effect 里重试。
      // 记录 warn 便于排查 (不弹 UI, 避免启动期网络错误刷屏)。
      console.warn("[refreshProjects] failed:", e);
      return [];
    }
  }, []);

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);

  // 加载论文模板列表 (供新建会话时选择)
  useEffect(() => {
    listTemplates()
      .then((list) => {
        if (list.length > 0) {
          setTemplates(list);
          // 若当前选中的模板不在列表里, 回退到 article
          if (!list.some((t) => t.id === selectedTemplate)) {
            setSelectedTemplate(list[0].id);
          }
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 探测后端 + 同步配置 + 恢复会话
  // 启动时后端可能还没ready, 所以用轮询而不是单次探测; 运行期间也持续心跳。
  // 离线判断有 60s 宽限期: 暂时连不上显示 "等待后端服务", 超过 1min 才显示 "服务离线"。
  useEffect(() => {
    let mounted = true;
    let settingsLoaded = false;

    const transitionStatus = (next: BackendStatus) => {
      if (!mounted) return;
      setBackendStatus((cur) => (cur === next ? cur : next));
      backendStatusRef.current = next;
    };

    const tryConnect = async () => {
      if (!mounted) return;
      try {
        await health();
        if (!mounted) return;
        backendWaitStartRef.current = null;
        transitionStatus("online");

        if (!settingsLoaded) {
          settingsLoaded = true;
          const raw = await getSettings();
          if (!mounted) return;
          const saved = fromApiSettings(raw);
          setConfig(saved);
          const hasEnabled = saved.llm_providers.some(isProviderReady);
          if (!hasEnabled) setShowSettings(true);

          if (!restoredRef.current) {
            restoredRef.current = true;
            // 后端刚就绪: 刷新项目列表。挂载时的 refreshProjects 可能在后端未启动时就失败了,
            // 这里补上, 确保 Tauri 慢启动场景下项目列表能正确加载。
            void refreshProjects();
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
        settingsLoaded = false;
        const cur = backendStatusRef.current;
        if (cur === "online" || cur === "checking") {
          backendWaitStartRef.current = Date.now();
          transitionStatus("waiting");
        } else if (cur === "waiting") {
          const since = backendWaitStartRef.current;
          if (since && Date.now() - since > 60000) {
            transitionStatus("offline");
          }
        }
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

    // 总是用前端当前启用的 provider 建会话 (而非只在后端未配置时兜底)。
    // 否则: 后端一旦配过一次, 之后切换/删除 provider 都不影响新会话 ——
    // 它会一直用后端 config.toml 里 default_model_tier 的旧 provider。
    if (primary) {
      body.base_url = primary.base_url;
      body.api_key = primary.api_key;
      body.model = primary.model;
      body.context_window = Number(primary.context_window) || 256000;
    }
    // MCP / 学术 key 始终带 (它们不依赖 provider 选择)
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
    if (config.plan_mode) body.plan_mode = true;
    if (config.disabled_skills && config.disabled_skills.length > 0) {
      body.disabled_skills = config.disabled_skills;
    }
    if (selectedTemplate) body.template = selectedTemplate;
    // Layer A.5: 带 project_id (用当前选中, 后端 fallback 到 proj_default)
    if (currentProjectId) body.project_id = currentProjectId;
    try {
      const data = await createSession(body);
      setSid(data.id);
      // 更新 project 的 last_session_id
      if (currentProjectId) {
        updateProject(currentProjectId, { last_session_id: data.id }).catch(() => {});
      }
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

  // Tauri 桌面端: 后端启动前显示全屏 splash, 就绪后再展示主界面。
  // 浏览器模式后端走代理立即可用, 跳过 splash 避免闪烁。
  const isTauri = typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;
  if (isTauri && backendStatus !== "online") {
    return <SplashScreen />;
  }

  return (
    <div className="h-full flex flex-col bg-page text-default theme-transition">
      <UpdateBanner />
      {/* 三栏主体 — macOS Overlay 标题栏: 红绿灯悬浮在左栏顶部, 无独立 titlebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* 左: 项目/会话侧边栏 */}
        <ResizableSidebar side="left" defaultWidth={224} minWidth={180} maxWidth={400} storageKey="left">
          {(toggleCollapsed) => (
            <>
              {/* 顶行: 红绿灯避让 + Logo + 收起按钮 (整行作为窗口拖拽区)
                  行高 40px: macOS Overlay 红绿灯中线在 y≈20px, 与行内容中线对齐 */}
              <div
                data-tauri-drag-region
                className="traffic-clear h-10 pl-2.5 pr-2 flex items-center gap-2 shrink-0"
              >
                <div className="w-6 h-6 rounded-md bg-accent/10 flex items-center justify-center shrink-0">
                  <LogoIcon width={13} height={13} className="text-accent" />
                </div>
                <div className="font-semibold text-[13px] tracking-tight flex-1 select-none">Axiom</div>
                <button
                  onClick={toggleCollapsed}
                  className="ghost-icon-btn"
                  title="收起左栏"
                >
                  <ChevronLeftIcon width={14} height={14} />
                </button>
              </div>

              <div className="px-2.5 pb-1 flex items-center gap-2">
                <button
                  onClick={handleNewSession}
                  className="flex-1 h-9 rounded-lg bg-accent hover:bg-accent-hover text-inverse text-sm font-medium shadow-sm transition-all active:scale-[0.98] flex items-center justify-center gap-1.5"
                >
                  <PlusIcon width={14} height={14} />
                  新会话
                </button>
              </div>

              {/* Layer A.5: project 切换器 */}
              <div className="px-2.5 pb-2 relative">
                <button
                  onClick={() => setProjectDropdownOpen((v) => !v)}
                  className="w-full px-2.5 py-2 rounded-md bg-page hover:bg-hover text-xs text-default border border-border flex items-center justify-between gap-2 transition-colors"
                  title="切换项目"
                >
                  <span className="truncate flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
                    <span className="truncate">
                      {projects.find((p) => p.id === currentProjectId)?.name || "选择项目"}
                    </span>
                  </span>
                  <ChevronDownIcon width={12} height={12} className="shrink-0 text-faint" />
                </button>
                {projectDropdownOpen && (
                  <>
                    {/* 点击外部关闭下拉 */}
                    <div
                      className="fixed inset-0 z-10"
                      onClick={() => setProjectDropdownOpen(false)}
                    />
                    <div className="absolute left-2.5 right-2.5 top-full mt-1 z-20 rounded-md bg-elevated border border-border shadow-lg overflow-visible">
                      {projects.map((p) => (
                        <button
                          key={p.id}
                          onClick={() => {
                            setCurrentProjectId(p.id);
                            localStorage.setItem("axiom_current_project_id", p.id);
                            setProjectDropdownOpen(false);
                          }}
                          className={`w-full px-2.5 py-2 text-left text-xs flex items-center justify-between gap-2 transition-colors ${
                            p.id === currentProjectId
                              ? "bg-accent/15 text-accent"
                              : "text-default hover:bg-hover"
                          }`}
                        >
                          <span className="truncate flex items-center gap-1.5">
                            {p.is_default && <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />}
                            {p.name}
                          </span>
                          <span className="text-[10px] text-faint shrink-0">{p.session_count}</span>
                        </button>
                      ))}
                      <div className="border-t border-border">
                        <button
                          onClick={() => {
                            setProjectDropdownOpen(false);
                            setProjectModalInitial(null);
                            setShowProjectModal(true);
                          }}
                          className="w-full px-2.5 py-2 text-left text-xs text-accent hover:bg-accent/10 flex items-center gap-1.5 transition-colors"
                        >
                          <PlusIcon width={12} height={12} />
                          新建项目...
                        </button>
                        <button
                          onClick={() => {
                            setProjectDropdownOpen(false);
                            setShowProjectManager(true);
                          }}
                          className="w-full px-2.5 py-2 text-left text-xs text-muted hover:bg-hover flex items-center gap-1.5 transition-colors"
                        >
                          <ProjectsIcon width={12} height={12} />
                          项目管理...
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* 论文模板选择 (新建会话时复制进工作区作为 main.tex preamble) */}
              {templates.length > 0 && (
                <div className="px-2.5 py-2">
                  <div className="text-[10px] font-medium text-faint uppercase tracking-wider px-2 mb-1.5">
                    论文模板
                  </div>
                  <div className="px-2">
                    <select
                      value={selectedTemplate}
                      onChange={(e) => setSelectedTemplate(e.target.value)}
                      title={templates.find((t) => t.id === selectedTemplate)?.description || ""}
                      className="w-full px-2 py-1.5 rounded-md bg-page text-xs text-default border border-border focus:outline-none focus:border-accent"
                    >
                      {templates.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name} ({t.columns}栏)
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              )}

              <div className="px-2.5 py-2">
                <div className="text-[10px] font-medium text-faint uppercase tracking-wider px-2 mb-1.5">工作区</div>
                <nav className="space-y-0.5">
                  <SidebarItem icon={<ChatIcon />} label="会话" active={sidebarTab === "sessions"} onClick={() => setSidebarTab("sessions")} />
                  <SidebarItem icon={<FolderIcon />} label="项目文件" active={sidebarTab === "files"} onClick={() => setSidebarTab("files")} />
                </nav>
              </div>

              <div className="flex-1 overflow-y-auto px-2.5 py-1 min-h-0">
                {sidebarTab === "sessions" ? (
                  // Layer A.5: 按 currentProjectId 过滤 session 列表
                  (currentProjectId
                    ? sessions.filter((s) => (s.project_id || "proj_default") === currentProjectId)
                    : sessions
                  ).length === 0 ? (
                    <EmptyState icon="" text="此项目暂无会话" sub="点击上方开始新会话" />
                  ) : (
                    <div className="space-y-0.5">
                      {(currentProjectId
                        ? sessions.filter((s) => (s.project_id || "proj_default") === currentProjectId)
                        : sessions
                      ).map((s) => (
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
            </>
          )}
        </ResizableSidebar>

        {/* 中: 对话流 — 圆角卡片悬浮于窗口背景之上 */}
        <main className="flex-1 flex flex-col min-w-0 relative app-main-card m-2">
          {/* 卡内顶栏: 拖拽区 + 服务状态 / 模型 / 主题 / 设置 */}
          <div
            data-tauri-drag-region
            className="h-10 flex items-center justify-end gap-1.5 px-3 shrink-0 hairline-b"
          >
            <BackendBadge status={backendStatus} />
            <ModelBadge config={config} />
            <button
              onClick={toggleTheme}
              className="ghost-icon-btn"
              title={resolvedTheme === "dark" ? "切换到月之亮面" : "切换到月之暗面"}
            >
              {resolvedTheme === "dark" ? <MoonIcon /> : <SunIcon />}
            </button>
            <button
              onClick={() => setShowSettings(true)}
              className="ghost-icon-btn"
              title="设置"
            >
              <SettingsIcon />
            </button>
          </div>
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
            {session.status === "running" &&
              (session.messages.length === 0 ||
                session.messages[session.messages.length - 1].role !== "assistant") && (
                <div className="max-w-3xl mx-auto mt-6 flex items-center gap-2 text-sm text-muted">
                  <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
                  Axiom 正在思考…
                </div>
              )}
            {session.status === "awaiting" &&
              session.awaiting === "user_response" &&
              session.pendingAsk && (
                <AskUserCard
                  question={session.pendingAsk.question}
                  options={session.pendingAsk.options}
                  onAnswer={(ans) => {
                    if (!sid) return;
                    session.start(sid, ans);
                  }}
                />
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
                    {session.status === "running" ? (
                      <button
                        onClick={session.stop}
                        className="w-8 h-8 rounded-full bg-error hover:bg-error/90 text-inverse flex items-center justify-center transition-all active:scale-[0.98]"
                        title="停止生成"
                      >
                        <StopIcon />
                      </button>
                    ) : (
                      <button
                        onClick={send}
                        disabled={!input.trim()}
                        className="w-8 h-8 rounded-full bg-accent hover:bg-accent-hover disabled:bg-card disabled:text-faint text-inverse flex items-center justify-center transition-all active:scale-[0.98]"
                        title="发送"
                      >
                        <SendIcon />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </main>

        {/* 右: 工作区 + 验证 — 浮动圆角卡片 */}
        <ResizableSidebar side="right" defaultWidth={256} minWidth={200} maxWidth={480} storageKey="right" floating>
          {(toggleCollapsed) => (
            <>
              <div className="h-10 px-2 flex items-center justify-end hairline-b shrink-0">
                <button
                  onClick={toggleCollapsed}
                  className="ghost-icon-btn"
                  title="收起右栏"
                >
                  <ChevronRightIcon width={14} height={14} />
                </button>
              </div>
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
            </>
          )}
        </ResizableSidebar>
      </div>

      {/* 底部状态栏 - SciForge 风格 */}
      <footer className="app-footer h-7 bg-subtle flex items-center justify-end px-3 text-[11px] text-faint shrink-0 shadow-[0_-1px_0_0_rgba(15,23,42,0.04)]">
        <div className="flex items-center gap-3">
          <BackendStatusText status={backendStatus} />
          <span>Local runtime</span>
        </div>
      </footer>

      {showSettings && (
        <SettingsModal
          initial={config}
          onClose={() => setShowSettings(false)}
          onSave={(c) => {
            setConfig(c);
            setShowSettings(false);
            setBackendStatus("checking");
            health().then(() => setBackendStatus("online")).catch(() => setBackendStatus("offline"));
          }}
        />
      )}

      {/* Layer A.5: 新建/编辑 project modal */}
      {showProjectModal && (
        <ProjectFormModal
          initial={projectModalInitial}
          onClose={() => {
            setShowProjectModal(false);
            setProjectModalInitial(null);
          }}
          onDone={(p) => {
            // 新建: 切到新 project; 编辑: 只刷新列表
            if (!projectModalInitial) {
              setCurrentProjectId(p.id);
              localStorage.setItem("axiom_current_project_id", p.id);
            }
            setShowProjectModal(false);
            setProjectModalInitial(null);
            refreshProjects();
          }}
        />
      )}

      {/* 独立项目管理面板: 新建/重命名/归档/恢复/删除的统一入口 */}
      {showProjectManager && (
        <ProjectManagerModal
          onClose={() => setShowProjectManager(false)}
          currentProjectId={currentProjectId}
          onChanged={async ({ switchTo }) => {
            if (switchTo) {
              setCurrentProjectId(switchTo);
              localStorage.setItem("axiom_current_project_id", switchTo);
            }
            await refreshProjects();
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

  const [hovered, setHovered] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={`flex items-start gap-2 px-2.5 py-2 cursor-pointer rounded-lg transition-all ${
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
        className={`shrink-0 mt-0.5 p-0.5 text-faint hover:text-error rounded transition-all ${
          hovered ? "opacity-100" : "opacity-0"
        }`}
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
  const [hovered, setHovered] = useState<string | null>(null);
  // 待确认删除的文件 — Tauri 不支持 window.confirm, 用 React 弹窗走确认流程
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
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
    await deleteFile(sid, path);
    setRefreshTick((n) => n + 1);
  };

  if (!sid) {
    return <EmptyState icon="📁" text="未选择项目" sub="先创建一个会话" />;
  }
  if (files.length === 0) {
    return <EmptyState icon="📂" text="项目为空" sub="Agent 写入的文件会显示在这里" />;
  }

  return (
    <div
      className="space-y-0.5"
      onMouseLeave={() => setHovered(null)}
      onScroll={() => setHovered(null)}
    >
      {files.map((f) => (
        <div
          key={f.path}
          className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-hover cursor-pointer text-xs"
          onClick={() => window.open(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(f.path)}`, "_blank")}
          onMouseEnter={() => setHovered(f.path)}
          onMouseLeave={() => setHovered(null)}
        >
          <FileIcon path={f.path} />
          <span className="flex-1 truncate text-muted">{f.name}</span>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setPendingDelete(f.path);
            }}
            className={`text-faint hover:text-error p-0.5 rounded transition-all ${
              hovered === f.path ? "opacity-100" : "opacity-0"
            }`}
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
      {/* 删除确认弹窗 (替代 Tauri 不可用的 window.confirm) */}
      {pendingDelete && (
        <ConfirmDialog
          title="删除文件"
          message={`确定删除 ${pendingDelete} 吗？此操作不可撤销。`}
          onConfirm={() => handleDelete(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
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

// 全屏启动屏: Tauri 桌面端后端启动期间显示, 就绪后由 App 切换到主界面。
// 只保留 Logo + 转圈, 不显示状态文字。
function SplashScreen() {
  return (
    <div data-tauri-drag-region className="h-full w-full flex flex-col items-center justify-center gap-5 bg-page animate-fade-in">
      <div className="relative flex items-center justify-center">
        {/* 外圈柔和光晕 */}
        <div className="absolute w-20 h-20 rounded-2xl bg-accent/10 blur-xl" />
        <div className="relative w-16 h-16 rounded-2xl bg-gradient-to-br from-emerald-400 to-teal-600 flex items-center justify-center shadow-lg">
          <LogoIcon width={30} height={30} className="text-white" />
        </div>
      </div>
      <SpinnerIcon className="w-4 h-4 text-accent" />
    </div>
  );
}

function BackendBadge({ status }: { status: BackendStatus }) {
  if (status === "checking" || status === "waiting") {
    return (
      <span className="text-[11px] flex items-center gap-1.5 px-2 py-1 rounded-full text-warning bg-warning/15">
        <SpinnerIcon className="w-3 h-3" />
        等待后端服务
      </span>
    );
  }
  const up = status === "online";
  return (
    <span className={`text-[11px] flex items-center gap-1.5 px-2 py-1 rounded-full ${up ? "text-success bg-success/15" : "text-error bg-error/15"}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${up ? "bg-success" : "bg-error"} ${up ? "animate-pulse" : ""}`} />
      {up ? "服务在线" : "服务离线"}
    </span>
  );
}

function BackendStatusText({ status }: { status: BackendStatus }) {
  if (status === "checking" || status === "waiting") {
    return <span>等待后端服务</span>;
  }
  return <span>{status === "online" ? "服务就绪" : "服务离线"}</span>;
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

function AskUserCard({
  question,
  options,
  onAnswer,
}: {
  question: string;
  options: string[];
  onAnswer: (ans: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <div className="max-w-3xl mx-auto mt-6 p-4 rounded-xl border border-accent/30 bg-accent/5">
      <div className="flex items-start gap-2 mb-3">
        <span className="text-accent text-sm mt-0.5">❓</span>
        <div className="text-sm text-default leading-relaxed whitespace-pre-wrap">{question}</div>
      </div>
      {options.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {options.map((opt) => (
            <button
              key={opt}
              onClick={() => onAnswer(opt)}
              className="text-xs px-3 py-2 rounded-lg bg-card hover:bg-elevated text-default border border-border transition-colors"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && text.trim()) {
              onAnswer(text.trim());
            }
          }}
          placeholder="输入回答…"
          className="flex-1 px-3 py-2 text-sm bg-card rounded-lg border border-border text-default placeholder:text-faint focus:outline-none focus:border-accent"
        />
        <button
          onClick={() => text.trim() && onAnswer(text.trim())}
          disabled={!text.trim()}
          className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-hover text-inverse text-sm font-medium disabled:opacity-40 transition-colors"
        >
          回答
        </button>
      </div>
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

function EmptyState({ icon, text, sub }: { icon: string; text: string; sub: string }) {  return (
    <div className="text-center py-10 px-4">
      {icon && <div className="text-2xl mb-2 opacity-40">{icon}</div>}
      <div className="text-[13px] text-muted">{text}</div>
      <div className="text-[11px] text-faint mt-1">{sub}</div>
    </div>
  );
}

// SVG 图标组件
function SpinnerIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

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

function StopIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" stroke="none">
      <rect x="4" y="4" width="16" height="16" rx="2" />
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

function ChevronDownIcon({ width = 14, height = 14, className = "" }: { width?: number; height?: number; className?: string }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

// 项目管理图标 (项目管理入口)
function ProjectsIcon({ width = 14, height = 14 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function ChevronLeftIcon({ width = 14, height = 14 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

function ChevronRightIcon({ width = 14, height = 14 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
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

