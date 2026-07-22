// 项目管理面板: 统一管理活跃/归档项目。
// 合并并取代了原下拉 ⋯ 菜单 + ArchivedProjectsModal 的职责。
// 支持: 新建 / 重命名 / 归档 / 恢复 / 永久删除; 点击项目名切换到该项目。
import { useEffect, useState } from "react";
import {
  archiveProject,
  deleteProject,
  listProjects,
  unarchiveProject,
} from "../api";
import type { ProjectInfo } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { ProjectFormModal } from "./ProjectFormModal";

// 事件: 通知父组件刷新列表, switchTo 指示操作后应切换到的 project id
export interface ProjectManagerEvent {
  action: "create" | "rename" | "archive" | "unarchive" | "delete";
  project?: ProjectInfo;
  switchTo?: string;
}

// --- 内联图标 (遵循项目约定: 图标在使用处内联自定义 SVG) ---
function PlusIcon({ width = 14, height = 14 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}
function EditIcon({ width = 13, height = 13 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z" />
    </svg>
  );
}
function ArchiveIcon({ width = 13, height = 13 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="20" height="5" rx="1" />
      <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" />
      <line x1="10" y1="12" x2="14" y2="12" />
    </svg>
  );
}
function RestoreIcon({ width = 13, height = 13 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
      <path d="M3 3v5h5" />
    </svg>
  );
}
function TrashIcon({ width = 13, height = 13 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  );
}
function ChevronDownIcon({ width = 12, height = 12 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

export function ProjectManagerModal({
  onClose,
  currentProjectId,
  onChanged,
}: {
  onClose: () => void;
  currentProjectId: string | null;
  onChanged?: (e: ProjectManagerEvent) => void;
}) {
  const [active, setActive] = useState<ProjectInfo[]>([]);
  const [archived, setArchived] = useState<ProjectInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // 归档的行内二次确认目标 (轻量, 不用 ConfirmDialog 的红色按钮)
  const [archiveTarget, setArchiveTarget] = useState<ProjectInfo | null>(null);
  // 永久删除走 ConfirmDialog
  const [pendingDelete, setPendingDelete] = useState<ProjectInfo | null>(null);
  // 新建/编辑表单
  const [formInitial, setFormInitial] = useState<ProjectInfo | null>(null);
  const [showForm, setShowForm] = useState(false);
  // 归档分区展开
  const [archivedExpanded, setArchivedExpanded] = useState(true);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const [a, b] = await Promise.all([listProjects(false), listProjects(true)]);
      setActive(a);
      setArchived(b);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const emit = (e: ProjectManagerEvent) => onChanged?.(e);

  // --- 操作处理 ---
  const handleArchive = async (p: ProjectInfo) => {
    setBusyId(p.id);
    try {
      await archiveProject(p.id);
      emit({ action: "archive", project: p, switchTo: p.id === currentProjectId ? "proj_default" : undefined });
      setArchiveTarget(null);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const handleUnarchive = async (p: ProjectInfo) => {
    setBusyId(p.id);
    try {
      await unarchiveProject(p.id);
      emit({ action: "unarchive", project: p, switchTo: p.id });
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    setBusyId(target.id);
    try {
      await deleteProject(target.id, true);
      emit({ action: "delete", project: target, switchTo: target.id === currentProjectId ? "proj_default" : undefined });
      setPendingDelete(null);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const handleSwitchTo = (p: ProjectInfo) => {
    emit({ action: "rename", project: p, switchTo: p.id });
    onClose();
  };

  const handleFormDone = (p: ProjectInfo) => {
    const isEdit = !!formInitial;
    emit({ action: isEdit ? "rename" : "create", project: p, switchTo: isEdit ? undefined : p.id });
    setShowForm(false);
    setFormInitial(null);
    void reload();
  };

  const fmtDate = (s: string | null) => {
    if (!s) return null;
    try {
      return new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    } catch {
      return null;
    }
  };

  // --- 行渲染 ---
  const renderRow = (p: ProjectInfo, opts: { archived: boolean }) => {
    const isCurrent = p.id === currentProjectId;
    return (
      <div
        key={p.id}
        className={`flex items-center justify-between gap-3 px-3 py-2.5 rounded-md border transition-colors ${
          isCurrent ? "bg-accent/10 border-accent/30" : "bg-page border-border hover:bg-hover"
        }`}
      >
        <button
          onClick={() => handleSwitchTo(p)}
          className="min-w-0 flex-1 text-left"
          title={`切换到「${p.name}」`}
        >
          <div className="flex items-center gap-1.5">
            {p.is_default && <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />}
            <span className="text-sm text-default truncate">{p.name}</span>
            {isCurrent && <span className="text-[10px] text-accent shrink-0">当前</span>}
          </div>
          <div className="text-[10px] text-faint mt-0.5 truncate">
            {p.session_count} 个会话
            {fmtDate(p.last_activity_at) ? ` · ${fmtDate(p.last_activity_at)}` : ""}
            {p.description ? ` · ${p.description}` : ""}
          </div>
        </button>

        <div className="flex gap-1 shrink-0">
          {opts.archived ? (
            <>
              <button
                onClick={() => handleUnarchive(p)}
                disabled={busyId === p.id}
                className="w-7 h-7 rounded flex items-center justify-center text-accent hover:bg-accent/15 disabled:opacity-50 transition-colors"
                title="恢复"
              >
                <RestoreIcon />
              </button>
              <button
                onClick={() => setPendingDelete(p)}
                disabled={busyId === p.id}
                className="w-7 h-7 rounded flex items-center justify-center text-error hover:bg-error/15 disabled:opacity-50 transition-colors"
                title="永久删除"
              >
                <TrashIcon />
              </button>
            </>
          ) : p.is_default ? (
            <span className="text-[10px] text-faint pr-1">默认</span>
          ) : (
            <>
              {/* 归档二次确认: 行内切换成"确认归档" */}
              {archiveTarget?.id === p.id ? (
                <>
                  <button
                    onClick={() => handleArchive(p)}
                    disabled={busyId === p.id}
                    className="px-2 h-7 rounded text-[11px] bg-error/10 text-error hover:bg-error/20 disabled:opacity-50 transition-colors"
                  >
                    确认归档
                  </button>
                  <button
                    onClick={() => setArchiveTarget(null)}
                    className="px-2 h-7 rounded text-[11px] text-muted hover:bg-hover transition-colors"
                  >
                    取消
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={() => { setFormInitial(p); setShowForm(true); }}
                    disabled={busyId === p.id}
                    className="w-7 h-7 rounded flex items-center justify-center text-muted hover:text-default hover:bg-hover disabled:opacity-50 transition-colors"
                    title="重命名"
                  >
                    <EditIcon />
                  </button>
                  <button
                    onClick={() => setArchiveTarget(p)}
                    disabled={busyId === p.id}
                    className="w-7 h-7 rounded flex items-center justify-center text-muted hover:text-warning hover:bg-warning/15 disabled:opacity-50 transition-colors"
                    title="归档"
                  >
                    <ArchiveIcon />
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>
    );
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-elevated rounded-xl border border-border shadow-2xl w-full max-w-2xl p-6 animate-fade-in flex flex-col"
        style={{ maxHeight: "85vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div className="flex items-center justify-between mb-4 shrink-0">
          <h2 className="text-base font-semibold text-default">项目管理</h2>
          <button
            onClick={onClose}
            className="text-faint hover:text-default text-sm"
            title="关闭"
          >
            ✕
          </button>
        </div>

        {/* 新建按钮 */}
        <button
          onClick={() => { setFormInitial(null); setShowForm(true); }}
          className="mb-4 shrink-0 px-3 py-2 rounded-lg bg-accent hover:bg-accent-hover text-inverse text-sm font-medium shadow-sm transition-colors flex items-center justify-center gap-1.5"
        >
          <PlusIcon width={14} height={14} />
          新建项目
        </button>

        {/* 错误提示 */}
        {error && (
          <div className="text-xs text-error py-2 mb-2 break-all shrink-0">⚠ {error}</div>
        )}

        {/* 滚动区 */}
        <div className="flex-1 overflow-y-auto min-h-0 pr-1">
          {loading ? (
            <div className="text-xs text-muted py-8 text-center">加载中...</div>
          ) : (
            <>
              {/* 活跃项目 */}
              <div className="mb-4">
                <div className="text-[10px] font-medium text-faint uppercase tracking-wider px-1 mb-2">
                  活跃项目 ({active.length})
                </div>
                {active.length === 0 ? (
                  <div className="text-xs text-muted py-4 text-center">暂无活跃项目</div>
                ) : (
                  <div className="space-y-1.5">
                    {active.map((p) => renderRow(p, { archived: false }))}
                  </div>
                )}
              </div>

              {/* 已归档 */}
              {archived.length > 0 && (
                <div>
                  <button
                    onClick={() => setArchivedExpanded((v) => !v)}
                    className="flex items-center gap-1 text-[10px] font-medium text-faint uppercase tracking-wider px-1 mb-2 hover:text-muted transition-colors"
                  >
                    <span
                      className="inline-block transition-transform"
                      style={{ transform: archivedExpanded ? "none" : "rotate(-90deg)" }}
                    >
                      <ChevronDownIcon width={10} height={10} />
                    </span>
                    已归档 ({archived.length})
                  </button>
                  {archivedExpanded && (
                    <div className="space-y-1.5">
                      {archived.map((p) => renderRow(p, { archived: true }))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* 底部说明 */}
        <div className="text-[10px] text-faint mt-4 leading-relaxed shrink-0 border-t border-border pt-3">
          点击项目名切换到该项目 · 默认项目受保护不可改名/归档/删除
          <br />
          归档会隐藏其所有会话但数据保留,恢复后重新可见;永久删除解绑其下会话(会话本身不丢失),不可撤销。
        </div>
      </div>

      {/* 新建/编辑表单 */}
      {showForm && (
        <ProjectFormModal
          initial={formInitial}
          onClose={() => { setShowForm(false); setFormInitial(null); }}
          onDone={handleFormDone}
        />
      )}

      {/* 永久删除二次确认 */}
      {pendingDelete && (
        <ConfirmDialog
          title="永久删除项目"
          message={`确定永久删除「${pendingDelete.name}」? 该操作会解绑其下 ${pendingDelete.session_count} 个会话(会话本身不丢失),且不可撤销。`}
          confirmLabel="永久删除"
          onConfirm={handleConfirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
