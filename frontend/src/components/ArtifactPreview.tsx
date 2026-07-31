// 统一 Artifact 预览浮窗: 点击工作区任意文件即弹出居中可拖动浮窗。
// 替代旧的「查看论文」专用全屏页 (PaperView 已拆为 TexView + PdfView)。
// 按 path 后缀分发到对应渲染器; 带全屏按钮可一键铺满整个 APP。
//
// 支持类型:
//   .tex       → TexView (TeX KaTeX / PDF tectonic 双模式 + 编译)
//   .pdf       → PdfView (PDF.js 直接渲染, 已编译好的 PDF)
//   .md        → Markdown (GFM + KaTeX, 复用聊天渲染组件)
//   图片        → <img> + 滚轮缩放 (png/jpg/jpeg/gif/webp/bmp/svg)
//   .csv       → 可滚动表格
//   .json      → 美化 + 折叠
//   分子        → MoleculeView (3Dmol: pdb/mol/sdf/xyz/cif/mmtf/...)
//   其余文本    → 代码视图 (等宽 + 行号 + 复制)
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase, openInFileManager } from "../api";
import { TexView } from "./TexView";
import { MoleculeView } from "./MoleculeView";
import { Markdown } from "./Markdown";

interface Props {
  sid: string;
  path: string;
  onClose: () => void;
}

const IMAGE_EXTS = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"];
const MOLECULE_EXTS = ["pdb", "ent", "pqr", "mol", "sdf", "mol2", "xyz", "cif", "mmcif", "mmtf", "gro"];

type Kind = "tex" | "pdf" | "markdown" | "image" | "csv" | "json" | "molecule" | "code";

function kindOf(path: string): Kind {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  if (ext === "tex") return "tex";
  if (ext === "pdf") return "pdf";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (IMAGE_EXTS.includes(ext)) return "image";
  if (ext === "csv") return "csv";
  if (ext === "json") return "json";
  if (MOLECULE_EXTS.includes(ext)) return "molecule";
  return "code";
}

const KIND_LABEL: Record<Kind, string> = {
  tex: "TeX 论文",
  pdf: "PDF",
  markdown: "Markdown",
  image: "图片",
  csv: "表格",
  json: "JSON",
  molecule: "3D 分子",
  code: "代码",
};

export function ArtifactPreview({ sid, path, onClose }: Props) {
  const kind = kindOf(path);
  const fileName = path.split("/").pop() || path;
  // 全屏模式: 铺满整个 APP; 非全屏: 居中浮窗可拖动
  const [fullscreen, setFullscreen] = useState(false);
  // 浮窗位置: null = CSS 居中; 拖动后转为绝对坐标 {x, y}
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const windowRef = useRef<HTMLDivElement>(null);
  // 拖动状态: 记录起点, 区分"拖动"与"点击"避免误关遮罩
  const dragState = useRef<{ active: boolean; moved: boolean; startX: number; startY: number; originX: number; originY: number }>({
    active: false, moved: false, startX: 0, startY: 0, originX: 0, originY: 0,
  });

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 标题栏拖动: mousedown 记录起点与浮窗当前左上角坐标 → mousemove 改 pos
  const onTitleMouseDown = useCallback((e: React.MouseEvent) => {
    if (fullscreen) return; // 全屏不可拖
    const el = windowRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    dragState.current = {
      active: true,
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      originX: rect.left,
      originY: rect.top,
    };
    // 一旦开始拖动, 把定位从 CSS 居中切到绝对坐标 (以当前实际位置为基准)
    setPos({ x: rect.left, y: rect.top });
  }, [fullscreen]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const ds = dragState.current;
      if (!ds.active) return;
      const dx = e.clientX - ds.startX;
      const dy = e.clientY - ds.startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) ds.moved = true;
      setPos({ x: ds.originX + dx, y: ds.originY + dy });
    };
    const onUp = () => {
      dragState.current.active = false;
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, []);

  // 切到全屏时清掉自定义坐标, 回到 inset-0
  useEffect(() => {
    if (fullscreen) setPos(null);
  }, [fullscreen]);

  const handleOpenDir = async () => {
    try {
      await openInFileManager(sid, path);
    } catch (e) {
      alert(`打开文件位置失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // 浮窗定位样式
  const windowStyle: React.CSSProperties = fullscreen
    ? { left: 0, top: 0, right: 0, bottom: 0, width: "auto", height: "auto" }
    : pos
    ? { left: `${pos.x}px`, top: `${pos.y}px` }
    : {}; // 无 pos: 用 CSS 居中类 (left-1/2 top-1/2 -translate-1/2)

  const windowClass = fullscreen
    ? "fixed inset-0"
    : pos
    ? "fixed"
    : "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2";

  return (
    <>
      {/* 半透明遮罩: 点击关闭 (拖动误触由 suppressClick 抑制) */}
      {!fullscreen && (
        <div
          className="fixed inset-0 bg-black/40 backdrop-blur-[2px] z-40 animate-fade-in"
          onMouseDown={() => onClose()}
        />
      )}
      {/* 浮窗本体 */}
      <div
        ref={windowRef}
        style={windowStyle}
        className={`${windowClass} z-50 flex flex-col bg-card border border-border shadow-2xl overflow-hidden ${
          fullscreen ? "rounded-none" : "rounded-xl"
        } ${!fullscreen && !pos ? "w-[min(960px,92vw)] h-[85vh]" : !fullscreen ? "w-[min(960px,92vw)] h-[85vh]" : ""}`}
      >
        {/* 标题栏: 可拖动 (非全屏); 左侧文件名 + 类型, 右侧操作按钮 */}
        <div
          data-tauri-drag-region="false"
          onMouseDown={onTitleMouseDown}
          className={`shrink-0 h-11 flex items-center gap-2 px-3 border-b border-border bg-elevated ${
            fullscreen ? "" : "cursor-move"
          }`}
        >
          <KindIcon kind={kind} />
          <span className="text-xs text-muted truncate flex-1 min-w-0 font-mono" title={path}>
            {fileName}
          </span>
          <span className="text-[10px] text-faint px-1.5 py-0.5 rounded bg-page shrink-0">
            {KIND_LABEL[kind]}
          </span>
          <div className="flex items-center gap-0.5 shrink-0">
            <button
              onClick={() => setFullscreen((v) => !v)}
              className="ghost-icon-btn"
              title={fullscreen ? "退出全屏" : "全屏"}
            >
              {fullscreen ? <ExitFullscreenIcon /> : <FullscreenIcon />}
            </button>
            <button onClick={handleOpenDir} className="ghost-icon-btn" title="打开文件所在目录">
              <FolderOpenIcon />
            </button>
            <button onClick={onClose} className="ghost-icon-btn hover:text-error" title="关闭 (Esc)">
              <CloseIcon />
            </button>
          </div>
        </div>

        {/* 内容区: 按类型分发 */}
        <div className="flex-1 min-h-0 bg-page overflow-hidden">
          {kind === "tex" && <TexView sid={sid} path={path} />}
          {kind === "pdf" && <DirectPdfView sid={sid} path={path} />}
          {kind === "markdown" && <MarkdownPreview sid={sid} path={path} />}
          {kind === "image" && <ImageView sid={sid} path={path} />}
          {kind === "csv" && <CsvView sid={sid} path={path} />}
          {kind === "json" && <JsonView sid={sid} path={path} />}
          {kind === "molecule" && <MoleculeView sid={sid} path={path} />}
          {kind === "code" && <CodeView sid={sid} path={path} />}
        </div>
      </div>
    </>
  );
}

/** 已编译好的 PDF 直接用 PDF.js 内嵌渲染 (不经 tectonic 编译流程) */
function DirectPdfView({ sid, path }: { sid: string; path: string }) {
  // 带时间戳避免缓存; 复用 PdfView 组件但绕过编译逻辑
  const url = `${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}?t=${Date.now()}`;
  return (
    <div className="h-full overflow-y-auto">
      <iframe src={url} title={path} className="w-full h-full border-0 bg-white" />
    </div>
  );
}

/** Markdown 预览: fetch 文本后复用聊天 Markdown 组件渲染 */
function MarkdownPreview({ sid, path }: { sid: string; path: string }) {
  const [text, setText] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
      .then((t) => { if (alive) { setText(t); setLoading(false); } })
      .catch((e) => { if (alive) { setError(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [sid, path]);

  if (loading) return <div className="py-20 text-center text-faint animate-pulse text-sm">加载…</div>;
  if (error) return <div className="py-20 text-center text-error text-sm">{error}</div>;
  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="max-w-3xl mx-auto">
        <Markdown text={text} />
      </div>
    </div>
  );
}

/** 图片预览: 居中展示 + 滚轮缩放 */
function ImageView({ sid, path }: { sid: string; path: string }) {
  const url = `${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`;
  const [scale, setScale] = useState(1);
  const [error, setError] = useState(false);

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setScale((s) => Math.min(8, Math.max(0.1, s * (e.deltaY < 0 ? 1.1 : 0.9))));
  };

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-error text-sm">
        图片加载失败
      </div>
    );
  }
  return (
    <div
      className="h-full w-full overflow-auto flex items-center justify-center p-4"
      onWheel={onWheel}
    >
      <img
        src={url}
        alt={path}
        onError={() => setError(true)}
        draggable={false}
        style={{ transform: `scale(${scale})`, transformOrigin: "center center" }}
        className="max-w-none transition-transform"
      />
    </div>
  );
}

/** CSV 预览: 解析为表格, 首行作表头 */
function CsvView({ sid, path }: { sid: string; path: string }) {
  const [rows, setRows] = useState<string[][]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
      .then((t) => {
        if (!alive) return;
        setRows(parseCsv(t));
        setLoading(false);
      })
      .catch((e) => { if (alive) { setError(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [sid, path]);

  if (loading) return <div className="py-20 text-center text-faint animate-pulse text-sm">加载…</div>;
  if (error) return <div className="py-20 text-center text-error text-sm">{error}</div>;
  if (rows.length === 0) return <div className="py-20 text-center text-faint text-sm">空文件</div>;

  const header = rows[0];
  const body = rows.slice(1);
  return (
    <div className="h-full overflow-auto p-4">
      <div className="text-[10px] text-faint mb-2">{rows.length} 行 · {header.length} 列</div>
      <table className="text-xs border-collapse">
        <thead className="sticky top-0">
          <tr>
            {header.map((h, i) => (
              <th key={i} className="bg-subtle border border-border px-2.5 py-1.5 text-left text-default font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri} className="hover:bg-hover">
              {row.map((c, ci) => (
                <td key={ci} className="border border-border px-2.5 py-1.5 text-muted whitespace-nowrap">{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** JSON 预览: 美化 + 高亮键名 */
function JsonView({ sid, path }: { sid: string; path: string }) {
  const [raw, setRaw] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
      .then((t) => {
        if (!alive) return;
        try {
          setRaw(JSON.stringify(JSON.parse(t), null, 2));
          setError(null);
        } catch {
          setRaw(t); // 非合法 JSON, 回退显示原文
          setError("这不是合法的 JSON, 以纯文本显示");
        }
        setLoading(false);
      })
      .catch((e) => { if (alive) { setError(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [sid, path]);

  if (loading) return <div className="py-20 text-center text-faint animate-pulse text-sm">加载…</div>;
  return (
    <div className="h-full overflow-auto p-4">
      {error && <div className="text-[11px] text-warning mb-2">⚠ {error}</div>}
      <CodeBlock code={raw} lang="json" />
    </div>
  );
}

/** 代码/文本预览: 等宽 + 行号 + 复制 */
function CodeView({ sid, path }: { sid: string; path: string }) {
  const [code, setCode] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const ext = path.split(".").pop() || "";

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
      .then((t) => { if (alive) { setCode(t); setLoading(false); } })
      .catch((e) => { if (alive) { setError(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [sid, path]);

  if (loading) return <div className="py-20 text-center text-faint animate-pulse text-sm">加载…</div>;
  if (error) return <div className="py-20 text-center text-error text-sm">{error}</div>;
  return (
    <div className="h-full overflow-auto p-4">
      <CodeBlock code={code.replace(/\n$/, "")} lang={ext} withLineNumbers />
    </div>
  );
}

/** 带行号 + 复制的代码块 (从 Markdown 组件风格抽出的简化版) */
function CodeBlock({ code, lang, withLineNumbers }: { code: string; lang: string; withLineNumbers?: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* ignore */ }
  };
  const lines = code.split("\n");
  return (
    <div className="rounded-xl overflow-hidden border border-border bg-code shadow-lg">
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10">
        <span className="text-[11px] font-mono text-zinc-400 uppercase tracking-wider">{lang || "text"}</span>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-faint font-mono">{lines.length} 行</span>
          <button onClick={copy} className="text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors flex items-center gap-1">
            {copied ? "✓ 已复制" : "复制"}
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        {withLineNumbers ? (
          <div className="flex font-mono text-[13px] leading-[1.6]">
            <pre className="select-none text-right text-zinc-600 px-3 py-3 border-r border-white/10 bg-black/10">
              {lines.map((_, i) => `${i + 1}\n`).join("")}
            </pre>
            <pre className="px-4 py-3 text-zinc-200 flex-1"><code>{code}</code></pre>
          </div>
        ) : (
          <pre className="px-4 py-3 text-zinc-200"><code>{code}</code></pre>
        )}
      </div>
    </div>
  );
}

// ---- 工具函数 ----

/** 极简 CSV 解析: 支持引号包裹 + 引号内逗号/换行; 不处理转义引号的复杂情况 */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else {
      if (c === '"') inQuotes = true;
      else if (c === ",") { row.push(field); field = ""; }
      else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
      else if (c === "\r") { /* skip */ }
      else field += c;
    }
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ""));
}

// ---- 图标 ----

function KindIcon({ kind }: { kind: Kind }) {
  const cls = "w-4 h-4 shrink-0 ";
  const colors: Record<Kind, string> = {
    tex: "text-error", pdf: "text-error", markdown: "text-muted",
    image: "text-accent-secondary", csv: "text-accent", json: "text-accent",
    molecule: "text-accent-secondary", code: "text-accent",
  };
  return (
    <svg className={cls + colors[kind]} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      {kind === "code" || kind === "json" ? (
        <>
          <polyline points="16 18 22 12 16 6" />
          <polyline points="8 6 2 12 8 18" />
        </>
      ) : (
        <>
          <path d="M16 13H8" />
          <path d="M16 17H8" />
        </>
      )}
    </svg>
  );
}

function FullscreenIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3" />
    </svg>
  );
}

function ExitFullscreenIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 3v3a2 2 0 0 1-2 2H3M21 8h-3a2 2 0 0 1-2-2V3M3 16h3a2 2 0 0 1 2 2v3M16 21v-3a2 2 0 0 1 2-2h3" />
    </svg>
  );
}

function FolderOpenIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v4" />
      <path d="M2 10h20" />
      <path d="M9 15l3-3 3 3" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 6L6 18M6 6l12 12" />
    </svg>
  );
}
