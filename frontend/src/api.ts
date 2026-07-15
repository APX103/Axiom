// API 客户端。对应 operon/api/app.py 的 REST 端点。
// 开发时 Vite proxy /api → http://127.0.0.1:8000
// Tauri 桌面壳会把后端端口注入到 window.__BACKEND_PORT__

import type { ModelConfig, SessionInfo, SkillInfo, McpServerStatus, MemoryInfo } from "./types";

declare global {
  interface Window {
    __BACKEND_PORT__?: number;
    __TAURI_INTERNALS__?: unknown;
  }
}

function isTauri(): boolean {
  return typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;
}

function getApiBase(): string {
  if (isTauri()) {
    const port = window.__BACKEND_PORT__ || 8000;
    return `http://127.0.0.1:${port}/api`;
  }
  return "/api";
}

const BASE = getApiBase();

async function jfetch(url: string, opts?: RequestInit) {
  const resp = await fetch(url, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts?.headers || {}) },
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json();
}

export async function health(): Promise<{ status: string }> {
  return jfetch(`${BASE}/health`);
}

export async function getSettings(): Promise<Record<string, unknown>> {
  return jfetch(`${BASE}/settings`);
}

export async function saveSettings(body: Record<string, unknown>): Promise<{ status: string }> {
  return jfetch(`${BASE}/settings`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listSkills(): Promise<SkillInfo[]> {
  return jfetch(`${BASE}/skills`);
}

export async function getMcpTools(serverName: string): Promise<McpServerStatus> {
  return jfetch(`${BASE}/mcp/${encodeURIComponent(serverName)}/tools`);
}

export async function listMemories(entity?: string): Promise<MemoryInfo[]> {
  const qs = entity ? `?entity=${encodeURIComponent(entity)}` : "";
  return jfetch(`${BASE}/memories${qs}`);
}

export async function deleteMemory(memId: string): Promise<{ status: string }> {
  return jfetch(`${BASE}/memories/${encodeURIComponent(memId)}`, { method: "DELETE" });
}

export async function listSessions(): Promise<SessionInfo[]> {
  return jfetch(`${BASE}/sessions`);
}

export async function createSession(
  config: ModelConfig & { workspace?: string; plan_mode?: boolean }
): Promise<{ id: string; frame_id: string }> {
  return jfetch(`${BASE}/sessions`, {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function approvePlan(sid: string): Promise<{ approved: boolean; steps: unknown[] }> {
  return jfetch(`${BASE}/sessions/${sid}/approve`, { method: "POST" });
}

export async function getSessionState(sid: string): Promise<{
  id: string;
  frame_id: string | null;
  status: string;
  task_summary: string | null;
  plan_mode: boolean;
  plan: import("./types").PlanSnapshot | null;
  artifacts: Record<string, import("./types").ArtifactInfo>;
  messages: { role: string; content: unknown }[];
}> {
  return jfetch(`${BASE}/sessions/${sid}`);
}

export async function deleteSession(sid: string): Promise<{ status: string }> {
  return jfetch(`${BASE}/sessions/${sid}`, { method: "DELETE" });
}

export async function deleteFile(sid: string, path: string): Promise<{ status: string }> {
  return jfetch(`${BASE}/sessions/${sid}/files/${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
}

// WebSocket 流式连接。对应 /api/sessions/{sid}/stream
export function connectStream(
  sid: string,
  onEvent: (e: import("./types").WSEvent) => void,
  onError?: (e: Event) => void
): { send: (prompt: string) => void; close: () => void } {
  const proto = "ws:";
  const wsBase = BASE.replace(/^https?:\/\/([^/]+).*/, "$1");
  const ws = new WebSocket(`${proto}//${wsBase}/api/sessions/${sid}/stream`);
  // 缓冲握手前发出的消息, 等 onopen 再 flush (修 WS 时序 bug: send 在 open 前会丢失)
  const pending: string[] = [];
  ws.onopen = () => {
    while (pending.length) ws.send(pending.shift()!);
  };
  ws.onmessage = (ev) => {
    try {
      onEvent(JSON.parse(ev.data));
    } catch {
      /* ignore malformed */
    }
  };
  if (onError) ws.onerror = onError;
  return {
    send: (prompt: string) => {
      const data = JSON.stringify({ prompt });
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      } else {
        pending.push(data); // 连接未 open, 缓冲到 onopen
      }
    },
    close: () => ws.close(),
  };
}

// SSE 流式连接 (fetch + ReadableStream)。对应 /api/sessions/{sid}/stream-sse
// 用 POST (EventSource 只支持 GET, 无法发 prompt body); 事件模型与 WS 完全相同。
export function connectSSE(
  sid: string,
  prompt: string,
  onEvent: (e: import("./types").WSEvent) => void,
  onError?: (err: unknown) => void
): { close: () => void } {
  const controller = new AbortController();
  let closed = false;

  // SSE 文本流解析: 按 \n\n 分割事件块, 每块取 data: 行
  fetch(`${BASE}/sessions/${sid}/stream-sse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) {
        throw new Error(`SSE ${resp.status}: ${await resp.text().catch(() => "")}`);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // 按 "\n\n" 切事件块 (SSE 标准)
        let sep: number;
        while ((sep = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          // 取 data: 行 (可能多行, 拼接)
          const dataLines = block
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trim())
            .join("");
          if (!dataLines) continue;
          try {
            onEvent(JSON.parse(dataLines));
          } catch {
            /* ignore malformed */
          }
        }
      }
    })
    .catch((err) => {
      if (!closed && onError) onError(err);
    });

  return {
    close: () => {
      closed = true;
      controller.abort();
    },
  };
}
