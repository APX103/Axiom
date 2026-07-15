import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import App from "./App";
import LandingPage from "./LandingPage";
import { ThemeProvider } from "./hooks/useTheme";
import "./index.css";

const isTauri = typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;

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
