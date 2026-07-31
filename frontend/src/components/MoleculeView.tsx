// 3D 分子结构预览: 用 3Dmol.js (WebGL) 渲染 .pdb/.mol/.sdf/.xyz/.mmcif 等。
// 3Dmol 体积较大且依赖 window/WebGL, 用动态 import 按需加载, 不进主 bundle。
// 支持鼠标旋转/缩放/平移 (3Dmol 内置), 适合科研中查看蛋白/小分子产物。
import { useEffect, useRef, useState } from "react";
import { apiBase } from "../api";

// 3Dmol 格式标识: 取扩展名映射, 缺省交给 3Dmol 自动识别
const FORMAT_BY_EXT: Record<string, string> = {
  pdb: "pdb",
  ent: "pdb",
  pqr: "pqr",
  mol: "sdf",
  sdf: "sdf",
  mol2: "mol2",
  xyz: "xyz",
  cif: "mmcif",
  mmcif: "mmcif",
  mmtf: "mmtf",
  gro: "gro",
  lammpstrj: "lammpstrj",
  cube: "cube",
  dx: "dx",
};

interface Props {
  sid: string;
  path: string;
}

// 3Dmol 的 GLViewer; 其官方 .d.ts 对 setStyle 等方法签名标注有误,
// 动态加载场景下用 any 规避类型噪音最稳妥。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Viewer = any;

export function MoleculeView({ sid, path }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const viewerRef = useRef<any>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string>("");

  const ext = path.split(".").pop()?.toLowerCase() || "";
  const fmt = FORMAT_BY_EXT[ext];

  useEffect(() => {
    let alive = true;
    let viewer: Viewer | null = null;

    (async () => {
      try {
        // 1. 拉文件内容 (分子文件多为文本; cube/dx 体积数据除外)
        const resp = await fetch(`${apiBase()}/sessions/${sid}/files/${encodeURIComponent(path)}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.text();
        if (!alive) return;

        // 2. 动态加载 3Dmol
        // 3Dmol 是 CommonJS (export =), esModuleInterop 下 default 即整个模块。
        // 其 .d.ts 对 setStyle 等方法签名标注不全/有误, 用 any 规避类型噪音。
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const mod: any = await import("3dmol");
        const $3Dmol: any = mod.default ?? mod;
        const createViewer = $3Dmol.createViewer as
          | ((el: HTMLElement, cfg?: Record<string, unknown>) => Viewer)
          | undefined;
        if (typeof createViewer !== "function") {
          throw new Error("3Dmol.createViewer 不可用");
        }

        // 3. 创建 viewer 并渲染
        const el = containerRef.current;
        if (!el || !alive) return;
        // 容器需要有显式尺寸, 3Dmol 才能初始化 canvas
        el.innerHTML = "";
        const cfg = {
          backgroundColor: "rgba(0,0,0,0)",
          antialias: true,
        };
        viewer = createViewer(el, cfg);
        viewerRef.current = viewer;
        // addModel: 格式缺省时让 3Dmol 自动嗅探
        const model = viewer.addModel(data, fmt);
        if (!model) {
          throw new Error("无法解析分子结构 (格式不支持或文件损坏)");
        }
        // 默认 stick+sphere 风格: 兼顾骨架可读性与原子细节
        viewer.setStyle({}, { stick: { radius: 0.15 }, sphere: { scale: 0.25 } });
        viewer.zoomTo();
        viewer.render();
        if (alive) setStatus("ready");
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
      }
    })();

    return () => {
      alive = false;
      // 卸载时释放 WebGL 资源, 防内存泄漏
      try {
        viewer?.clear();
      } catch { /* ignore */ }
      viewerRef.current = null;
    };
  }, [sid, path, fmt]);

  // 容器尺寸变化时通知 3Dmol 重绘 (全屏切换/窗口拖拽后)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      try {
        viewerRef.current?.resize();
        viewerRef.current?.render();
      } catch { /* ignore */ }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (status === "error") {
    return (
      <div className="flex flex-col items-center justify-center py-16 px-6 text-center gap-2">
        <div className="text-error text-sm font-medium">无法加载分子结构</div>
        <div className="text-xs text-faint font-mono break-all">{error}</div>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      {status === "loading" && (
        <div className="absolute inset-0 flex items-center justify-center text-faint text-sm animate-pulse z-10">
          加载 3D 分子…
        </div>
      )}
      {/* 3Dmol 渲染挂载点; 必须有显式尺寸 */}
      <div ref={containerRef} className="w-full h-full min-h-[400px]" />
      {/* 操作提示 */}
      {status === "ready" && (
        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 text-[10px] text-faint bg-card/80 backdrop-blur px-2.5 py-1 rounded-full border border-border pointer-events-none">
          拖拽旋转 · 滚轮缩放 · 右键平移
        </div>
      )}
    </div>
  );
}
