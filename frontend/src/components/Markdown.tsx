// Markdown 渲染组件: 用于 LLM 聊天输出。
// 流式友好: 每次收到增量 text 后, 上层把完整文本传入, 本组件重解析全量。
// 支持: 代码块 (含语言标注)、表格、列表、任务列表、删除线 (GFM) + 数学公式 (KaTeX)。
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { useState } from "react";

export function Markdown({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="prose prose-sm max-w-none dark:prose-invert prose-headings:text-default prose-p:text-default prose-strong:text-default prose-a:text-link hover:prose-a:text-link/80 prose-pre:my-0 prose-pre:p-0 prose-pre:bg-transparent prose-code:before:content-none prose-code:after:content-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          pre: ({ children }) => <>{children}</>,
          code: CodeBlock,
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          table: ({ node, ...props }) => (
            <div className="overflow-x-auto my-3 border border-border rounded-lg">
              <table {...props} className="my-0" />
            </div>
          ),
          th: ({ node, ...props }) => <th {...props} className="bg-subtle text-default font-medium" />,
          td: ({ node, ...props }) => <td {...props} className="border-border text-muted" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function CodeBlock({
  className,
  children,
  ...props
}: {
  className?: string;
  children?: React.ReactNode;
}) {
  const text = String(children ?? "");
  // react-markdown v10 不再传 inline prop; 用 className 判断:
  // 代码块有 language-xxx class, 行内 code 没有
  const match = /language-(\w+)/.exec(className || "");
  const isBlock = match || text.includes("\n");

  if (!isBlock) {
    // 行内代码
    return (
      <code
        className="bg-subtle text-accent px-1 py-0.5 rounded font-mono text-[0.85em]"
        {...props}
      >
        {children}
      </code>
    );
  }

  const lang = match ? match[1] : "";
  const code = text.replace(/\n$/, "");
  return <CodeBlockCard code={code} lang={lang} />;
}

function CodeBlockCard({ code, lang }: { code: string; lang: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="my-3 rounded-xl overflow-hidden border border-border bg-code shadow-lg">
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10">
        <span className="text-[11px] font-mono text-zinc-400 uppercase tracking-wider">
          {lang || "code"}
        </span>
        <button
          onClick={copy}
          className="text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors flex items-center gap-1"
        >
          {copied ? (
            <>
              <CheckIcon /> 已复制
            </>
          ) : (
            <>
              <CopyIcon /> 复制
            </>
          )}
        </button>
      </div>
      <div className="overflow-x-auto p-4">
        <pre className="m-0 text-[13px] leading-[1.6]">
          <code className="font-mono text-zinc-200">{code}</code>
        </pre>
      </div>
    </div>
  );
}

function CopyIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
