// 论文预览页: TeX (KaTeX 渲染) 与 PDF (tectonic 编译) 两种模式切换。
// TeX 模式: 全屏渲染 .tex (LaTeX→HTML+KaTeX) + 编号引用 + 参考文献列表。
// PDF 模式: 调后端 /compile 编译后用 PDF.js 渲染真 PDF, 排版精确; 失败显示具体错误。
import { useEffect, useMemo, useRef, useState } from "react";
import "katex/dist/katex.min.css";
import { parseTex, formatBibEntry } from "../tex";
import { apiBase, openInFileManager, compilePdf } from "../api";
import type { CompileResult } from "../types";

interface Props {
  sid: string;
  onClose: () => void;
}

interface FileInfo {
  path: string;
  size: number;
  name: string;
}

export function PaperView({ sid, onClose }: Props) {
  const [tex, setTex] = useState<string>("");
  const [bib, setBib] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [selectedTex, setSelectedTex] = useState<string>("");
  // 模式: "tex" (KaTeX 渲染) | "pdf" (tectonic 编译后 PDF.js 渲染)
  const [mode, setMode] = useState<"tex" | "pdf">("tex");
  // PDF 编译状态
  const [compiling, setCompiling] = useState(false);
  const [compileResult, setCompileResult] = useState<CompileResult | null>(null);
  // PDF 渲染用的 URL (带时间戳避免缓存)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  // PDF.js worker (按需加载)
  const pdfjsLib = useRef<typeof import("pdfjs-dist") | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const fl = await fetch(`${apiBase()}/sessions/${sid}/files`).then((r) => r.json());
        const allFiles: FileInfo[] = fl.files || [];
        setFiles(allFiles);
        const texFiles = allFiles.filter((f) => f.path.endsWith(".tex"));
        if (texFiles.length === 0) {
          setError("工作区没有 .tex 文件");
          setLoading(false);
          return;
        }
        // 默认选第一个 .tex; 若已有选择且仍存在则保留
        const target = texFiles.some((f) => f.path === selectedTex) ? selectedTex : texFiles[0].path;
        setSelectedTex(target);
        await loadTex(sid, target, allFiles);
      } catch (e) {
        setError(String(e));
      }
      setLoading(false);
    })();
  }, [sid]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadTex = async (sessionId: string, texPath: string, allFiles: FileInfo[]) => {
    setLoading(true);
    setError(null);
    try {
      const texResp = await fetch(`${apiBase()}/sessions/${sessionId}/files/${encodeURIComponent(texPath)}`);
      setTex(await texResp.text());
      // 优先匹配同名 .bib; 否则取第一个 .bib
      const base = texPath.replace(/\.tex$/i, "");
      const bibFiles = allFiles.filter((f) => f.path.endsWith(".bib"));
      const matched =
        bibFiles.find((f) => f.path === `${base}.bib`) ||
        bibFiles.find((f) => f.path.toLowerCase().includes("reference")) ||
        bibFiles[0];
      if (matched) {
        const bibResp = await fetch(`${apiBase()}/sessions/${sessionId}/files/${encodeURIComponent(matched.path)}`);
        setBib(await bibResp.text());
      } else {
        setBib("");
      }
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  const handleSelectTex = async (path: string) => {
    setSelectedTex(path);
    await loadTex(sid, path, files);
  };

  // 编译当前选中的 .tex → PDF, 成功后刷新 PDF URL 触发重渲染
  const handleCompile = async () => {
    if (!selectedTex || compiling) return;
    setCompiling(true);
    setCompileResult(null);
    try {
      const result = await compilePdf(sid, selectedTex);
      setCompileResult(result);
      if (result.success && result.pdf_path) {
        // 带时间戳破缓存 (重新编译后同路径内容变了)
        setPdfUrl(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(result.pdf_path)}?t=${Date.now()}`);
      }
    } catch (e) {
      setCompileResult({
        success: false,
        pdf_path: "",
        size_kb: 0,
        message: String(e),
        errors: [],
        log_excerpt: "",
      });
    } finally {
      setCompiling(false);
    }
  };

  // 切到 PDF 模式时, 若还没编译过则自动编译一次
  useEffect(() => {
    if (mode === "pdf" && !pdfUrl && !compiling && !compileResult && selectedTex) {
      handleCompile();
    }
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const texFiles = useMemo(() => files.filter((f) => f.path.endsWith(".tex")), [files]);

  // 可打开文件 (横向 tab 条): tex/bib/pdf/md
  const openableFiles = useMemo(
    () => files.filter((p) => /\.(tex|bib|pdf|md)$/.test(p.path)),
    [files],
  );

  // 横向拖拽滚动 (像 tab 条): 鼠标按下后拖动即滚动, 释放时若拖动过则抑制本次 click
  const tabScrollRef = useRef<HTMLDivElement>(null);
  const dragState = useRef<{ active: boolean; moved: boolean; startX: number; startScroll: number }>({
    active: false,
    moved: false,
    startX: 0,
    startScroll: 0,
  });

  const onTabsPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = tabScrollRef.current;
    if (!el) return;
    // 只有内容溢出时才启用拖拽滚动
    if (el.scrollWidth <= el.clientWidth + 1) return;
    dragState.current = { active: true, moved: false, startX: e.clientX, startScroll: el.scrollLeft };
    el.setPointerCapture?.(e.pointerId);
  };

  const onTabsPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragState.current.active) return;
    const el = tabScrollRef.current;
    if (!el) return;
    const dx = e.clientX - dragState.current.startX;
    if (Math.abs(dx) > 4) dragState.current.moved = true;
    el.scrollLeft = dragState.current.startScroll - dx;
  };

  const onTabsPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragState.current.active) {
      tabScrollRef.current?.releasePointerCapture?.(e.pointerId);
    }
    dragState.current.active = false;
  };

  // 点击 tab: 若刚刚是拖动滚动了, 则不触发打开目录
  const onTabClick = (e: React.MouseEvent, path: string) => {
    if (dragState.current.moved) {
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    openDir(path);
  };

  const doc = useMemo(() => parseTex(tex, bib), [tex, bib]);

  const refEntries = useMemo(() => {
    return Object.entries(doc.citeMap)
      .sort(([, a], [, b]) => a - b)
      .map(([key, num]) => {
        const entry = doc.references.find((r) => r.key === key);
        return { key, num, text: entry ? formatBibEntry(entry) : "" };
      })
      .filter((r) => r.text); // 残缺/缺失条目不显示, 避免裸 key
  }, [doc]);

  const openDir = (path: string) => {
    openInFileManager(sid, path);
  };

  if (loading && !tex)
    return (
      <div className="fixed inset-0 bg-page z-50 flex items-center justify-center">
        <div className="text-faint animate-pulse">加载论文…</div>
      </div>
    );
  if (error)
    return (
      <div className="fixed inset-0 bg-page z-50 flex flex-col items-center justify-center gap-4">
        <div className="text-error">{error}</div>
        <button onClick={onClose} className="px-4 py-2 bg-card rounded-lg text-default">
          关闭
        </button>
      </div>
    );

  return (
    <div className="fixed inset-0 bg-page z-50 overflow-y-auto">
      {/* 工具栏 — h-16: 中线 y=32 与红绿灯对齐; traffic-clear 保留红绿灯安全间距 */}
      <div className="sticky top-0 bg-card border-b border-border traffic-clear pr-4 h-16 flex items-center gap-3 z-10 shadow-sm">
        {/* 左侧: 返回按钮始终可见, 永不缩放 */}
        <div className="flex items-center gap-3 shrink-0">
          <button onClick={onClose} className="text-muted hover:text-default text-sm">
            ← 返回工作台
          </button>
          <span className="text-faint">|</span>
          <span className="text-sm text-muted">📄 论文预览</span>
          {/* 模式切换: TeX (KaTeX 渲染) / PDF (tectonic 编译) */}
          <div className="ml-1 flex items-center rounded-md bg-page border border-border p-0.5">
            <button
              onClick={() => setMode("tex")}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${mode === "tex" ? "bg-accent text-inverse" : "text-muted hover:text-default"}`}
            >
              TeX
            </button>
            <button
              onClick={() => setMode("pdf")}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${mode === "pdf" ? "bg-accent text-inverse" : "text-muted hover:text-default"}`}
            >
              PDF
            </button>
          </div>
          {mode === "pdf" && (
            <button
              onClick={handleCompile}
              disabled={compiling || !selectedTex}
              className="px-3 py-1 rounded-md bg-success hover:bg-success/90 text-inverse text-xs font-medium disabled:opacity-50 transition-colors shrink-0"
              title="用 tectonic 重新编译 .tex → PDF"
            >
              {compiling ? "编译中…" : "⟳ 重新编译"}
            </button>
          )}
          {texFiles.length > 0 && (
            <select
              value={selectedTex}
              onChange={(e) => handleSelectTex(e.target.value)}
              className="px-2 py-1 rounded-md bg-page text-xs text-default border border-border focus:outline-none max-w-[200px] truncate"
            >
              {texFiles.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path}
                </option>
              ))}
            </select>
          )}
        </div>
        {/* 右侧: 文件 tab 条 — 占据剩余空间, 横向滚动, 拖拽平移 */}
        {openableFiles.length > 0 && (
          <div
            ref={tabScrollRef}
            onPointerDown={onTabsPointerDown}
            onPointerMove={onTabsPointerMove}
            onPointerUp={onTabsPointerUp}
            onPointerCancel={onTabsPointerUp}
            className="flex-1 min-w-0 flex items-center gap-2 overflow-x-auto scroll-smooth cursor-grab active:cursor-grabbing"
            style={{ scrollbarWidth: "thin" }}
          >
            {openableFiles.map((p) => {
              const name = p.path.split("/").pop();
              return (
                <button
                  key={p.path}
                  onClick={(e) => onTabClick(e, p.path)}
                  className="shrink-0 select-none text-xs px-3 py-1.5 rounded-lg bg-accent hover:bg-accent-hover text-inverse font-mono"
                  title={`打开所在目录: ${p.path}`}
                >
                  📁 {name}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* TeX 模式: KaTeX 渲染 (学术排版) */}
      {mode === "tex" && (
        <div
          className="mx-auto bg-card my-6 px-16 py-12 shadow-sm rounded"
          style={{ width: "51.8vw", minWidth: "680px", maxWidth: "1180px" }}
        >
          {doc.title && (
            <h1 className="text-2xl font-bold text-center mb-2" dangerouslySetInnerHTML={{ __html: doc.title }} />
          )}
          {doc.author && (
            <p
              className="text-center text-muted mb-1"
              dangerouslySetInnerHTML={{ __html: doc.author }}
            />
          )}
          {doc.date && (
            <p
              className="text-center text-faint text-sm mb-6"
              dangerouslySetInnerHTML={{ __html: doc.date }}
            />
          )}
          {doc.abstract && (
            <div className="my-6 px-6 py-3 border-l-4 border-border bg-page text-sm text-default">
              <div className="font-semibold mb-1">Abstract</div>
              <div dangerouslySetInnerHTML={{ __html: doc.abstract }} />
            </div>
          )}
          <div
            className="prose prose-sm max-w-none text-default leading-relaxed [&_h2]:text-xl [&_h2]:font-bold [&_h2]:mt-6 [&_h2]:mb-2 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:mt-4 [&_p]:my-2 [&_table]:text-xs [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_code]:bg-page [&_code]:px-1"
            dangerouslySetInnerHTML={{ __html: doc.body }}
          />
          {refEntries.length > 0 && (
            <div className="mt-10 pt-6 border-t border-border">
              <h2 className="text-lg font-bold mb-3">References</h2>
              <ol className="text-sm text-muted space-y-1.5 list-decimal pl-5">
                {refEntries.map((r) => (
                  <li key={r.key} id={`ref-${r.num}`} className="leading-snug">
                    {r.text}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {/* PDF 模式: tectonic 编译后渲染真 PDF */}
      {mode === "pdf" && (
        <PdfView
          compiling={compiling}
          result={compileResult}
          pdfUrl={pdfUrl}
          onCompile={handleCompile}
        />
      )}
    </div>
  );
}

/** PDF 渲染区: 用 PDF.js 渲染每页为 canvas。编译中/失败有对应状态。 */
function PdfView({
  compiling,
  result,
  pdfUrl,
  onCompile,
}: {
  compiling: boolean;
  result: CompileResult | null;
  pdfUrl: string | null;
  onCompile: () => void;
}) {
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
          共 {pdfDoc.numPages} 页{result && !result.errors.length ? "" : ""}
        </div>
      )}
      <div ref={containerRef} className="flex flex-col items-center" />
    </div>
  );
}
