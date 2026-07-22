import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import App from "./App";
import LandingPage from "./LandingPage";
import { ThemeProvider } from "./hooks/useTheme";
import "./index.css";

const isTauri = typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;

// Tauri 桌面端给 <html> 打标: CSS 据此做 macOS 红绿灯 (Overlay 标题栏) 避让
if (isTauri) document.documentElement.classList.add("tauri");

// 窗口聚焦状态打标: 聚焦时主卡显示亮边 (macOS 活跃窗口高光描边), 失焦隐去
const syncWindowFocus = () => {
  document.documentElement.classList.toggle("window-blurred", !document.hasFocus());
};
window.addEventListener("focus", syncWindowFocus);
window.addEventListener("blur", syncWindowFocus);
syncWindowFocus();

// 全局错误捕获 — 白屏时在页面上显示错误信息
window.addEventListener("error", (e) => {
  const root = document.getElementById("root");
  if (root && root.children.length === 0) {
    root.innerHTML = `<div style="padding:20px;font-family:monospace;color:red">JS Error: ${e.message}<br>${e.error?.stack || ""}</div>`;
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={isTauri ? <Navigate to="/app" replace /> : <LandingPage />} />
          <Route path="/app" element={<App />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>
);
