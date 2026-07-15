// 工作区面板: 展示 agent 产出的文件 (artifacts) + 实际工作区文件。
// .tex/.pdf 可点击查看/下载/删除。
import { useEffect, useState } from "react";
import { deleteFile, apiBase } from "../api";
import type { ArtifactInfo } from "../types";

export function WorkspacePanel({
  artifacts,
  sid,
  onViewPaper,
}: {
  artifacts: Record<string, ArtifactInfo>;
  sid: string | null;
  onViewPaper: () => void;
}) {
  const [files, setFiles] = useState<{ path: string; size: number; name: string }[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  const entries = Object.entries(artifacts);

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

  const hasTex = files.some((f) => f.path.endsWith(".tex")) || entries.some(([p]) => p.endsWith(".tex"));

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-default flex items-center gap-2">
            <FolderIcon />
            工作区
          </h2>
          <p className="text-[10px] text-faint mt-0.5">{files.length || entries.length} 个文件</p>
        </div>
        {hasTex && sid && (
          <button
            onClick={onViewPaper}
            className="text-[10px] px-2.5 py-1.5 rounded-lg bg-accent hover:bg-accent-hover text-inverse font-medium transition-colors"
          >
            查看论文
          </button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {files.length === 0 && entries.length === 0 ? (
          <EmptyHint text="还没有产物" sub="agent 写入的文件会显示在这里" />
        ) : (
          <ul className="space-y-0.5">
            {files.length > 0
              ? files.map((f) => <FileItem key={f.path} path={f.path} size={f.size} sid={sid} onDelete={handleDelete} />)
              : entries.map(([path, info]) => (
                  <FileItem key={path} path={path} size={info.size} sid={sid} onDelete={handleDelete} />
                ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function FileItem({
  path,
  size,
  sid,
  onDelete,
}: {
  path: string;
  size: number;
  sid: string | null;
  onDelete: (path: string) => void;
}) {
  const isViewable = /\.(tex|md|txt|py|csv|json|bib|js|ts|tsx|jsx|html|css|yaml|yml|xml|sh)$/.test(path);
  const isDoc = /\.(tex|pdf)$/.test(path);
  const view = () => {
    if (sid && isViewable) window.open(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`, "_blank");
  };
  const download = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (sid) window.open(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}?download=true`, "_blank");
  };
  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete(path);
  };
  return (
    <li
      className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg group ${
        isViewable ? "hover:bg-hover cursor-pointer" : ""
      }`}
      onClick={view}
    >
      <div className="flex items-center gap-2 min-w-0">
        <FileIcon path={path} isDoc={isDoc} />
        <span className="font-mono text-xs text-muted truncate">{path}</span>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <span className="text-[10px] text-faint font-mono">{formatSize(size)}</span>
        <button
          onClick={download}
          className="opacity-0 group-hover:opacity-100 text-[10px] text-faint hover:text-accent p-1 rounded transition-all"
          title="下载"
        >
          <DownloadIcon />
        </button>
        <button
          onClick={handleDelete}
          className="opacity-0 group-hover:opacity-100 text-[10px] text-faint hover:text-error p-1 rounded transition-all"
          title="删除"
        >
          <TrashIcon />
        </button>
      </div>
    </li>
  );
}

function FileIcon({ path, isDoc }: { path: string; isDoc: boolean }) {
  const ext = path.split(".").pop()?.toLowerCase();
  if (isDoc) {
    return (
      <svg className="w-4 h-4 text-error shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <path d="M14 2v6h6" />
        <path d="M16 13H8" />
        <path d="M16 17H8" />
        <path d="M10 9H8" />
      </svg>
    );
  }
  if (ext && ["py", "js", "ts", "tsx", "jsx"].includes(ext)) {
    return (
      <svg className="w-4 h-4 text-accent shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <polyline points="16 18 22 12 16 6" />
        <polyline points="8 6 2 12 8 18" />
      </svg>
    );
  }
  return (
    <svg className="w-4 h-4 text-faint shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M16 13H8" />
      <path d="M16 17H8" />
      <path d="M10 9H8" />
    </svg>
  );
}

function formatSize(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}K`;
  return `${(n / 1024 / 1024).toFixed(1)}M`;
}

function EmptyHint({ text, sub }: { text: string; sub: string }) {
  return (
    <div className="text-center py-10 px-4">
      <div className="w-10 h-10 mx-auto mb-3 rounded-full bg-page flex items-center justify-center">
        <FolderIcon />
      </div>
      <div className="text-sm text-muted">{text}</div>
      <div className="text-xs text-faint mt-1">{sub}</div>
    </div>
  );
}

function FolderIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  );
}

export { EmptyHint };
