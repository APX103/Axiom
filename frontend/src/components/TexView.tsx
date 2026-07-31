// TeX 文档预览: TeX (KaTeX 渲染) 与 PDF (tectonic 编译) 两种模式切换。
// 从旧 PaperView 迁出 — 现在只负责单个 .tex 文件的渲染, 不含工具栏/全屏逻辑
// (工具栏与浮窗外壳由 ArtifactPreview 承担)。
// TeX 模式: 全屏渲染 .tex (LaTeX→HTML+KaTeX) + 编号引用 + 参考文献列表。
// PDF 模式: 调后端 /compile 编译后用 PDF.js 渲染真 PDF, 排版精确; 失败显示具体错误。
import { useEffect, useMemo, useState } from "react";
import { parseTex, formatBibEntry } from "../tex";
import { apiBase, compilePdf } from "../api";
import { PdfView } from "./PdfView";
import type { CompileResult } from "../types";

interface FileInfo {
  path: string;
  size: number;
  name: string;
}

interface Props {
  sid: string;
  path: string;
}

export function TexView({ sid, path }: Props) {
  const [tex, setTex] = useState<string>("");
  const [bib, setBib] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 模式: "tex" (KaTeX 渲染) | "pdf" (tectonic 编译后 PDF.js 渲染)
  const [mode, setMode] = useState<"tex" | "pdf">("tex");
  // PDF 编译状态
  const [compiling, setCompiling] = useState(false);
  const [compileResult, setCompileResult] = useState<CompileResult | null>(null);
  // PDF 渲染用的 URL (带时间戳避免缓存)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        // 拉 .tex 内容
        const texResp = await fetch(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`);
        if (!alive) return;
        setTex(await texResp.text());
        // 拉工作区文件清单以寻找 .bib
        let allFiles: FileInfo[] = [];
        try {
          const fl = await fetch(`${apiBase()}/sessions/${sid}/files`).then((r) => r.json());
          allFiles = fl.files || [];
        } catch { /* 无 bib 也能渲染 */ }
        // 优先匹配同名 .bib; 否则取第一个 .bib
        const base = path.replace(/\.tex$/i, "").split("/").pop() || "";
        const bibFiles = allFiles.filter((f) => f.path.endsWith(".bib"));
        const matched =
          bibFiles.find((f) => f.path === `${base}.bib`) ||
          bibFiles.find((f) => f.path === path.replace(/[^/]+\.tex$/i, `${base}.bib`)) ||
          bibFiles.find((f) => f.path.toLowerCase().includes("reference")) ||
          bibFiles[0];
        if (matched) {
          const bibResp = await fetch(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(matched.path)}`);
          if (alive) setBib(await bibResp.text());
        } else {
          setBib("");
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
      if (alive) setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, [sid, path]);

  // 编译当前选中的 .tex → PDF, 成功后刷新 PDF URL 触发重渲染
  const handleCompile = async () => {
    if (compiling) return;
    setCompiling(true);
    setCompileResult(null);
    try {
      const result = await compilePdf(sid, path);
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
    if (mode === "pdf" && !pdfUrl && !compiling && !compileResult) {
      handleCompile();
    }
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // 切换 .tex 时重置 PDF 编译状态 (避免显示上一份的 PDF)
  useEffect(() => {
    setMode("tex");
    setCompileResult(null);
    setPdfUrl(null);
  }, [path]);

  if (loading && !tex)
    return <div className="flex items-center justify-center py-20 text-faint animate-pulse">加载论文…</div>;
  if (error)
    return <div className="py-20 text-center text-error text-sm">{error}</div>;

  return (
    <div className="h-full overflow-y-auto">
      {/* 模式切换工具条: TeX (KaTeX 渲染) / PDF (tectonic 编译) + 重新编译 */}
      <div className="sticky top-0 z-10 bg-card/95 backdrop-blur border-b border-border px-4 py-2 flex items-center gap-2">
        <div className="flex items-center rounded-md bg-page border border-border p-0.5">
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
            disabled={compiling}
            className="px-3 py-1 rounded-md bg-success hover:bg-success/90 text-inverse text-xs font-medium disabled:opacity-50 transition-colors"
            title="用 tectonic 重新编译 .tex → PDF"
          >
            {compiling ? "编译中…" : "⟳ 重新编译"}
          </button>
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
            <p className="text-center text-muted mb-1" dangerouslySetInnerHTML={{ __html: doc.author }} />
          )}
          {doc.date && (
            <p className="text-center text-faint text-sm mb-6" dangerouslySetInnerHTML={{ __html: doc.date }} />
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
        <PdfView compiling={compiling} result={compileResult} pdfUrl={pdfUrl} onCompile={handleCompile} />
      )}
    </div>
  );
}
