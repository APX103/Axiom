// 单条消息渲染: 用户气泡 + AI 卡片 + 代码块 + 工具调用折叠。
import { useState } from "react";
import type { UIMessage } from "../hooks/useSession";
import { Markdown } from "./Markdown";

export function MessageView({ msg }: { msg: UIMessage }) {
  // 跳过空消息:
  // - user: 工具结果被后端存为 role=user 但无文本, 不应显示为用户气泡
  // - assistant: 没有文本且没有工具调用, 不应显示空卡片
  const hasContent = msg.text.trim() || (msg.toolCalls && msg.toolCalls.length > 0);
  if (!hasContent) {
    return null;
  }

  if (msg.role === "user") {
    return (
      <div className="flex justify-end mb-6 animate-fade-in">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent text-inverse px-5 py-3 whitespace-pre-wrap break-words text-[15px] leading-relaxed shadow-md">
          {msg.text}
        </div>
      </div>
    );
  }

  if (msg.role === "system") {
    return (
      <div className="mb-6 flex justify-center animate-fade-in">
        <span className="inline-flex items-center gap-1.5 text-[11px] text-muted bg-elevated rounded-full px-3 py-1 shadow-sm">
          <span className="w-1 h-1 rounded-full bg-accent-secondary" />
          {msg.text}
        </span>
      </div>
    );
  }

  // assistant
  return (
    <div className="flex gap-3 mb-6 animate-fade-in">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-accent to-accent-secondary flex items-center justify-center shrink-0 shadow-glow">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2L2 7l10 5 10-5-10-5z" />
          <path d="M2 12l10 5 10-5" />
        </svg>
      </div>
      <div className="flex-1 min-w-0 pt-0.5">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-xs font-medium text-muted">Axiom</span>
          {msg.iter && (
            <span className="text-[10px] text-faint font-mono">轮次 {msg.iter}</span>
          )}
        </div>
        {msg.thinking && <ThinkingBlock text={msg.thinking} />}
        <div className="text-default text-[15px] leading-relaxed">
          {msg.text && <Markdown text={msg.text} />}
        </div>
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mt-3 space-y-2">
            {msg.toolCalls.map((tc) => (
              <ToolCallView key={tc.id} call={tc} result={msg.toolResults?.find((r) => r.tool_use_id === tc.id)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolCallView({ call, result }: { call: import("../types").ToolCall; result?: import("../types").ToolResult }) {
  const [open, setOpen] = useState(false);
  const isErr = result?.is_error;
  const done = result !== undefined;
  const preview = Object.entries(call.input)
    .map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 40)}`)
    .join(", ");

  const statusColor = isErr ? "bg-error/15" : done ? "bg-success/15" : "bg-elevated";
  const statusDot = isErr ? "bg-error" : done ? "bg-success" : "bg-accent-secondary animate-pulse";
  const statusText = isErr ? "text-error" : done ? "text-success" : "text-muted";

  return (
    <div className={`rounded-xl text-sm overflow-hidden bg-subtle border border-border shadow-md theme-transition ${statusColor}`}>
      <button
        className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-left group"
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`w-1.5 h-1.5 rounded-full ${statusDot}`} />
        <span className={`font-mono text-xs font-medium ${statusText}`}>{call.name}</span>
        <span className="text-muted truncate flex-1 font-mono text-[10px]">{preview}</span>
        <span className="text-muted text-xs group-hover:text-default transition-colors">
          {open ? "收起" : "展开"}
        </span>
      </button>
      {open && (
        <div className="px-3.5 pb-3.5 space-y-3">
          <div>
            <div className="text-[10px] font-semibold text-default uppercase tracking-wider mb-1.5">参数</div>
            <pre className="text-[11px] bg-code text-zinc-200 rounded-lg p-2.5 overflow-x-auto font-mono">
              {JSON.stringify(call.input, null, 2)}
            </pre>
          </div>
          {result && (
            <div>
              <div className={`text-[10px] font-semibold uppercase tracking-wider mb-1.5 ${isErr ? "text-error" : "text-default"}`}>
                {isErr ? "错误" : "结果"}
              </div>
              <pre className={`text-[11px] rounded-lg p-2.5 overflow-x-auto font-mono whitespace-pre-wrap break-all max-h-64 overflow-y-auto ${isErr ? "bg-error/10 text-error" : "bg-subtle text-default"}`}>
                {result.content}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ThinkingBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const preview = text.slice(0, 120).trim();
  const isLong = text.length > 120;
  return (
    <div className="mb-3 rounded-lg border border-border bg-elevated/50 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
      >
        <svg
          className={`w-3.5 h-3.5 text-faint shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
          viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round"
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
        <span className="text-[11px] font-medium text-muted">
          思考过程 {isLong && !open && <span className="text-faint">· {preview}…</span>}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-0">
          <pre className="text-[12px] text-muted leading-relaxed whitespace-pre-wrap break-words font-sans">
            {text}
          </pre>
        </div>
      )}
    </div>
  );
}
