import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import App from "./App";
import LandingPage from "./LandingPage";
import { ThemeProvider } from "./hooks/useTheme";
import "./index.css";
// v2 现代主题: 全部规则限定在 html.ui-v2 作用域, 经典版永不匹配, 零影响。
import "./v2/theme-v2.css";

// UI 变体开关: 默认经典版; 以 VITE_UI_VARIANT=v2 编译/启动时启用 v2 主题。
// 构建期静态替换, 经典版产物中此分支为死代码。
if (import.meta.env.VITE_UI_VARIANT === "v2") {
  document.documentElement.classList.add("ui-v2");
}

const isTauri = typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;

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
