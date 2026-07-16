// 可拖动调整宽度、可折叠的侧边栏。
// 宽度与折叠状态持久化到 localStorage。
import { useEffect, useRef, useState } from "react";

interface Props {
  side: "left" | "right";
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  storageKey: string;
  children: React.ReactNode;
  className?: string;
}

export function ResizableSidebar({
  side,
  defaultWidth,
  minWidth,
  maxWidth,
  storageKey,
  children,
  className = "",
}: Props) {
  const [width, setWidth] = useState(() => loadWidth(storageKey, defaultWidth));
  const [collapsed, setCollapsed] = useState(() => loadCollapsed(storageKey));
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
    const next = !collapsed;
    setCollapsed(next);
    saveCollapsed(storageKey, next);
  };

  if (collapsed) {
    return (
      <div
        className={`shrink-0 h-full bg-subtle flex flex-col items-center py-3 ${className}`}
        style={{
          width: 40,
          boxShadow:
            side === "left"
              ? "1px 0 0 0 rgba(15,23,42,0.04)"
              : "-1px 0 0 0 rgba(15,23,42,0.04)",
        }}
      >
        <button
          onClick={toggleCollapsed}
          className="w-7 h-7 rounded-lg flex items-center justify-center text-muted hover:bg-hover transition-colors"
          title={side === "left" ? "展开左栏" : "展开右栏"}
        >
          {side === "left" ? <ChevronRightIcon /> : <ChevronLeftIcon />}
        </button>
      </div>
    );
  }

  return (
    <aside
      className={`relative shrink-0 h-full bg-subtle flex flex-col ${
        side === "left" ? "pr-10" : "pl-10"
      } ${className}`}
      style={{
        width,
        boxShadow:
          side === "left"
            ? "1px 0 0 0 rgba(15,23,42,0.04)"
            : "-1px 0 0 0 rgba(15,23,42,0.04)",
      }}
    >
      {children}
      <button
        onClick={toggleCollapsed}
        className={`absolute top-3 z-10 w-7 h-7 rounded-md flex items-center justify-center text-faint hover:text-muted hover:bg-hover transition-colors ${
          side === "left" ? "right-2" : "left-2"
        }`}
        title={side === "left" ? "收起左栏" : "收起右栏"}
      >
        {side === "left" ? <ChevronLeftIcon /> : <ChevronRightIcon />}
      </button>
      <div
        onMouseDown={startDrag}
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
