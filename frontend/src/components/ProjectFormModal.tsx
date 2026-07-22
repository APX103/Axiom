// 新建/编辑 project 的小 modal (编辑模式传入 initial)。
// 从 App.tsx 抽取, 供 App 和 ProjectManagerModal 共用。
import { useState } from "react";
import { createProject, updateProject } from "../api";
import type { ProjectInfo } from "../types";

export function ProjectFormModal({
  onClose,
  onDone,
  initial,
}: {
  onClose: () => void;
  onDone: (p: ProjectInfo) => void;
  initial?: ProjectInfo | null;
}) {
  const editing = !!initial;
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim()) {
      setError("项目名不能为空");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const desc = description.trim() || undefined;
      if (editing && initial) {
        const p = await updateProject(initial.id, {
          name: name.trim(),
          description: desc ?? null,
        });
        onDone(p);
      } else {
        const p = await createProject(name.trim(), desc);
        onDone(p);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-elevated rounded-xl border border-border shadow-2xl w-full max-w-md p-6 animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-default mb-4">
          {editing ? "编辑项目" : "新建项目"}
        </h2>
        <div className="space-y-3">
          <label className="block">
            <span className="text-xs text-muted">项目名 *</span>
            <input
              type="text"
              value={name}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="如: kinase 142 位点研究"
              className="mt-1 w-full px-3 py-2 bg-page rounded-md text-sm text-default border border-border focus:outline-none focus:border-accent"
            />
          </label>
          <label className="block">
            <span className="text-xs text-muted">描述 (可选)</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="这个项目做什么..."
              rows={2}
              className="mt-1 w-full px-3 py-2 bg-page rounded-md text-sm text-default border border-border focus:outline-none focus:border-accent resize-none"
            />
          </label>
          {error && <div className="text-xs text-error">{error}</div>}
        </div>
        <div className="flex gap-2 mt-5">
          <button
            onClick={onClose}
            className="flex-1 py-2 rounded-lg bg-page text-sm text-muted hover:bg-hover transition-colors"
          >
            取消
          </button>
          <button
            onClick={submit}
            disabled={submitting || !name.trim()}
            className="flex-1 py-2 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent-hover disabled:opacity-50 transition-colors"
          >
            {submitting ? (editing ? "保存中..." : "创建中...") : (editing ? "保存" : "创建")}
          </button>
        </div>
      </div>
    </div>
  );
}
