// 已归档项目管理弹窗: 列出所有归档 project, 支持「恢复」和「永久删除」。
// 永久删除走 ConfirmDialog 二次确认 (会解绑其下 sessions)。
import { useEffect, useState } from "react";
import { deleteProject, listProjects, unarchiveProject } from "../api";
import type { ProjectInfo } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

export function ArchivedProjectsModal({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  // 归档/恢复/删除后通知父组件刷新活跃列表; restored 可带出被恢复的 project 供父组件切换
  onChanged?: (event: { action: "unarchive" | "archive" | "delete"; project?: ProjectInfo }) => void;
}) {
  const [items, setItems] = useState<ProjectInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 待二次确认的永久删除目标
  const [pendingDelete, setPendingDelete] = useState<ProjectInfo | null>(null);
  // 行级 busy (禁用按钮, 防止重复点)
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listProjects(true);
      setItems(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const handleRestore = async (p: ProjectInfo) => {
    setBusyId(p.id);
    try {
      await unarchiveProject(p.id);
      onChanged?.({ action: "unarchive", project: p });
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
      onChanged?.({ action: "delete", project: target });
      setPendingDelete(null);
      await reload();
    } catch (e) {
      // 失败留在 ConfirmDialog 内显示, 这里清 busy 让用户能重试/取消
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-elevated rounded-xl border border-border shadow-2xl w-full max-w-lg p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-default">已归档项目</h2>
          <button
            onClick={onClose}
            className="text-faint hover:text-default text-sm"
            title="关闭"
          >
            ✕
          </button>
        </div>

        {loading && <div className="text-xs text-muted py-8 text-center">加载中...</div>}

        {!loading && error && (
          <div className="text-xs text-error py-4 break-all">⚠ {error}</div>
        )}

        {!loading && !error && items.length === 0 && (
          <div className="text-xs text-muted py-8 text-center">没有已归档的项目</div>
        )}

        {!loading && !error && items.length > 0 && (
          <div className="space-y-1.5 max-h-[60vh] overflow-y-auto">
            {items.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between gap-3 px-3 py-2 rounded-md bg-page border border-border"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-default truncate">{p.name}</div>
                  <div className="text-[10px] text-faint">
                    {p.session_count} 个会话
                    {p.description ? ` · ${p.description}` : ""}
                  </div>
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <button
                    onClick={() => handleRestore(p)}
                    disabled={busyId === p.id}
                    className="px-2.5 py-1 rounded text-xs bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50 transition-colors"
                  >
                    恢复
                  </button>
                  <button
                    onClick={() => setPendingDelete(p)}
                    disabled={busyId === p.id}
                    className="px-2.5 py-1 rounded text-xs bg-error/10 text-error hover:bg-error/20 disabled:opacity-50 transition-colors"
                  >
                    永久删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="text-[10px] text-faint mt-4 leading-relaxed">
          归档项目会从侧栏隐藏其所有会话,但数据保留。恢复后会话重新可见。
          永久删除会解绑其下会话(会话本身不丢失),此操作不可撤销。
        </div>
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title="永久删除项目"
          message={`确定永久删除「${pendingDelete.name}」?`}
          confirmLabel="永久删除"
          onConfirm={handleConfirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
