// 可拖动调整宽度、可折叠的侧边栏。
// 宽度与折叠状态持久化到 localStorage。
import { useEffect, useRef, useState } from "react";

interface Props {
  side: "left" | "right";
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  storageKey: string;
  // floating: 圆角浮动卡片 (脱离窗口边缘); 默认 flush 贴边全高
  floating?: boolean;
  // 受控折叠: 提供时折叠状态由父组件管理, 折叠后整体隐藏 (无窄条), 由父组件的按钮恢复
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  children: React.ReactNode | ((toggleCollapsed: () => void) => React.ReactNode);
  className?: string;
}

export function ResizableSidebar({
  side,
  defaultWidth,
  minWidth,
  maxWidth,
  storageKey,
  floating = false,
  collapsed: collapsedProp,
  onToggleCollapsed,
  children,
  className = "",
}: Props) {
  const [width, setWidth] = useState(() => loadWidth(storageKey, defaultWidth));
  const [collapsedInternal, setCollapsedInternal] = useState(() => loadCollapsed(storageKey));
  const isControlled = collapsedProp !== undefined;
  const collapsed = isControlled ? collapsedProp : collapsedInternal;
  const [dragging, setDragging] = useState(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(width);

  useEffect(() => {
    if (!dragging) return;
    const handleMove = (e: MouseEvent) => {
      const delta =
        side === "left" ? e.clientX - startXRef.current : startXRef.current - e.clientX;
      const next = Math.max(minWidth, Math.min(maxWidth, startWidthRef.current + delta));
      setWidth(next);
    };
    const handleUp = () => {
      setDragging(false);
      saveWidth(storageKey, width);
    };
    document.addEventListener("mousemove", handleMove);
    document.addEventListener("mouseup", handleUp);
    return () => {
      document.removeEventListener("mousemove", handleMove);
      document.removeEventListener("mouseup", handleUp);
    };
  }, [dragging, side, minWidth, maxWidth, storageKey, width]);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    startXRef.current = e.clientX;
    startWidthRef.current = width;
    setDragging(true);
  };

  const toggleCollapsed = () => {
    if (isControlled) {
      onToggleCollapsed?.();
      return;
    }
    const next = !collapsedInternal;
    setCollapsedInternal(next);
    saveCollapsed(storageKey, next);
  };

  if (collapsed) {
    // 受控模式: 折叠即整体隐藏 (由外部按钮恢复, 如主卡顶栏的面板开关)
    if (isControlled) return null;
    return (
      <div
        data-side={side}
        data-tauri-drag-region="deep"
        className={`app-sidebar flush shrink-0 self-stretch flex flex-col items-center py-3 ${
          // 左栏收起成窄条时, 顶部留出 macOS 红绿灯高度, 避免展开按钮被挡住
          side === "left" ? "traffic-clear-top" : ""
        } ${className}`}
        style={{ width: 40 }}
      >
        <button
          onClick={toggleCollapsed}
          className="ghost-icon-btn"
          title={side === "left" ? "展开左栏" : "展开右栏"}
        >
          {side === "left" ? <ChevronRightIcon /> : <ChevronLeftIcon />}
        </button>
      </div>
    );
  }

  const isFunctionChildren = typeof children === "function";

  return (
    <aside
      data-side={side}
      data-tauri-drag-region="deep"
      className={`app-sidebar ${floating ? "floating" : "flush"} relative shrink-0 self-stretch flex flex-col ${
        isFunctionChildren ? "" : side === "left" ? "pr-10" : "pl-10"
      } ${className}`}
      style={{ width }}
    >
      {isFunctionChildren ? children(toggleCollapsed) : children}
      {!isFunctionChildren && (
        <button
          onClick={toggleCollapsed}
          className={`ghost-icon-btn absolute top-3 z-10 ${
            side === "left" ? "right-2" : "left-2"
          }`}
          title={side === "left" ? "收起左栏" : "收起右栏"}
        >
          {side === "left" ? <ChevronLeftIcon /> : <ChevronRightIcon />}
        </button>
      )}
      <div
        onMouseDown={startDrag}
        data-tauri-drag-region="false"
        className={`absolute top-0 bottom-0 w-1 cursor-col-resize hover:bg-accent/40 transition-colors ${
          side === "left" ? "right-0" : "left-0"
        } ${dragging ? "bg-accent/60" : "bg-transparent"}`}
      />
    </aside>
  );
}

function loadWidth(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(`rs-width-${key}`);
    if (raw) return Math.max(40, Number(raw) || fallback);
  } catch {
    /* ignore */
  }
  return fallback;
}

function saveWidth(key: string, width: number) {
  try {
    localStorage.setItem(`rs-width-${key}`, String(width));
  } catch {
    /* ignore */
  }
}

function loadCollapsed(key: string): boolean {
  try {
    return localStorage.getItem(`rs-collapsed-${key}`) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(key: string, collapsed: boolean) {
  try {
    if (collapsed) localStorage.setItem(`rs-collapsed-${key}`, "1");
    else localStorage.removeItem(`rs-collapsed-${key}`);
  } catch {
    /* ignore */
  }
}

function ChevronLeftIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}
