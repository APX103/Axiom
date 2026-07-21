// 通用确认弹窗。Tauri (WKWebView) 不支持 window.confirm/alert (静默失效),
// 所有需要"确认后执行"的破坏性操作都走这个 React 弹窗。
import { useState } from "react";

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "删除",
  cancelLabel = "取消",
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onCancel(); // 成功后关闭
    } catch (e) {
      // 失败留在弹窗内显示错误 (alert 在 Tauri 里同样不可用)
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40"
      onClick={onCancel}
    >
      <div
        className="bg-elevated rounded-xl border border-border shadow-2xl w-full max-w-sm p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-sm font-semibold text-default mb-2">{title}</h2>
        <p className="text-xs text-muted mb-4 break-all leading-relaxed">{message}</p>
        {error && <div className="text-xs text-error mb-3">⚠ {error}</div>}
        <div className="flex gap-2">
          <button
            onClick={onCancel}
            disabled={busy}
            className="flex-1 py-2 rounded-lg bg-page text-sm text-muted hover:bg-hover transition-colors disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            onClick={confirm}
            disabled={busy}
            className="flex-1 py-2 rounded-lg bg-error hover:bg-error/90 text-inverse text-sm font-medium transition-colors disabled:opacity-50"
          >
            {busy ? "处理中..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
