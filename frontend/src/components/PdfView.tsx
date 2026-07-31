// PDF 渲染区: 用 PDF.js 渲染每页为 canvas。编译中/失败有对应状态。
// 从旧 PaperView 迁出, 逻辑不变, 供 TexView 和 ArtifactPreview 复用。
import { useEffect, useRef, useState } from "react";
import type { CompileResult } from "../types";

interface Props {
  compiling: boolean;
  result: CompileResult | null;
  pdfUrl: string | null;
  onCompile: () => void;
}

export function PdfView({ compiling, result, pdfUrl, onCompile }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pdfDoc, setPdfDoc] = useState<{ numPages: number } | null>(null);
  const [renderErr, setRenderErr] = useState<string | null>(null);

  // 加载 + 渲染 PDF (pdfjs-dist 动态 import)
  useEffect(() => {
    if (!pdfUrl) {
      setPdfDoc(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        // worker 用 CDN (避免 vite 打包 worker 的复杂配置); 版本与 pdfjs-dist 对齐
        pdfjs.GlobalWorkerOptions.workerSrc = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;
        const loadingTask = pdfjs.getDocument({ url: pdfUrl });
        const doc = await loadingTask.promise;
        if (cancelled) return;
        setPdfDoc({ numPages: doc.numPages });
        const container = containerRef.current;
        if (!container) return;
        container.innerHTML = "";
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          if (cancelled) return;
          const viewport = page.getViewport({ scale: 1.3 });
          const canvas = document.createElement("canvas");
          canvas.className = "shadow-sm rounded my-3 mx-auto block bg-white";
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          container.appendChild(canvas);
          await page.render({ canvas, viewport }).promise;
        }
      } catch (e) {
        if (!cancelled) setRenderErr(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pdfUrl]);

  if (compiling && !result) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted text-sm gap-2">
        <span className="animate-pulse">⚙️ 正在编译 (首次会下载宏包，可能较慢)…</span>
      </div>
    );
  }

  if (result && !result.success) {
    return (
      <div className="mx-auto my-6 max-w-3xl px-6">
        <div className="p-4 rounded-lg bg-error/10 border border-error/30">
          <div className="text-error font-medium text-sm mb-2">⚠️ 编译失败</div>
          <div className="text-xs text-default mb-3">{result.message}</div>
          {result.errors.length > 0 && (
            <div className="text-xs text-muted mb-3">
              <div className="font-medium mb-1">具体错误：</div>
              <ul className="list-disc pl-4 space-y-1 font-mono">
                {result.errors.slice(0, 8).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          {result.log_excerpt && (
            <details className="mt-2">
              <summary className="text-xs text-faint cursor-pointer">完整日志尾部</summary>
              <pre className="text-[10px] text-faint mt-2 overflow-auto max-h-60 bg-page p-2 rounded font-mono whitespace-pre-wrap">
                {result.log_excerpt}
              </pre>
            </details>
          )}
        </div>
        <button
          onClick={onCompile}
          className="mt-3 px-4 py-2 rounded-lg bg-accent hover:bg-accent-hover text-inverse text-sm font-medium"
        >
          ⟳ 重新编译
        </button>
      </div>
    );
  }

  if (renderErr) {
    return (
      <div className="mx-auto my-6 max-w-3xl px-6 text-sm text-error">
        PDF 渲染失败: {renderErr}
      </div>
    );
  }

  return (
    <div className="py-4">
      {pdfDoc && (
        <div className="text-center text-xs text-faint mb-3">
          共 {pdfDoc.numPages} 页
        </div>
      )}
      <div ref={containerRef} className="flex flex-col items-center" />
    </div>
  );
}
