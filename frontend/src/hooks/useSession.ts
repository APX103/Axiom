// 会话 hook: 管理 WS 连接 + 把事件流聚合成对话/工具/plan 状态。
// 对应 agent 循环的事件流 → 前端 UI 状态。

import { useCallback, useRef, useState } from "react";
import { connectSSE } from "../api";
import type { ArtifactInfo, PlanSnapshot, ToolCall, ToolResult, WSEvent } from "../types";

// 一条对话消息 (UI 展示用,聚合 text/tool 调用)
export interface UIMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  thinking?: string;
  toolCalls?: ToolCall[];
  toolResults?: ToolResult[];
  iter?: number;
}

export type RunStatus = "idle" | "running" | "awaiting" | "done" | "error";

let _id = 0;
const nextId = () => `m${++_id}`;

export function useSession() {
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [iteration, setIteration] = useState(0);
  const [plan, setPlan] = useState<PlanSnapshot | null>(null);
  const [artifacts, setArtifacts] = useState<Record<string, ArtifactInfo>>({});
  const [usage, setUsage] = useState<{ in: number; out: number }>({ in: 0, out: 0 });
  const [error, setError] = useState<string | null>(null);
  const [awaiting, setAwaiting] = useState<string | null>(null);
  // 当前轮正在累积的 assistant 消息 (text + tool calls)
  const curRef = useRef<UIMessage | null>(null);

  const reset = useCallback(() => {
    setMessages([]);
    setStatus("idle");
    setIteration(0);
    setPlan(null);
    setArtifacts({});
    setUsage({ in: 0, out: 0 });
    setError(null);
    setAwaiting(null);
    curRef.current = null;
  }, []);

  const handleEvent = useCallback((e: WSEvent) => {
    switch (e.type) {
      case "start":
        setStatus("running");
        break;
      case "iteration":
        setIteration(e.n);
        // 新一轮 → 把当前累积的消息 flush, 开新的 assistant 消息占位
        // 但不立即 push 到数组 — 延迟到有实际内容 (text/tool_calls) 才创建
        curRef.current = { id: nextId(), role: "assistant", text: "", iter: e.n };
        break;
      case "thinking":
        // 追加到当前 assistant 消息的 thinking
        setMessages((m) => {
          if (curRef.current) {
            curRef.current = {
              ...curRef.current,
              thinking: (curRef.current.thinking || "") + e.text,
            };
            if (m.length === 0 || m[m.length - 1].id !== curRef.current.id) {
              return [...m, curRef.current];
            }
            return [...m.slice(0, -1), curRef.current];
          }
          const nm = { id: nextId(), role: "assistant" as const, text: "", thinking: e.text };
          curRef.current = nm;
          return [...m, nm];
        });
        break;
      case "text":
        // 追加到当前 assistant 消息的 text
        setMessages((m) => {
          if (curRef.current) {
            curRef.current = { ...curRef.current, text: curRef.current.text + e.text };
            // 如果 curRef 还没在数组里 (延迟创建), 现在加进去
            if (m.length === 0 || m[m.length - 1].id !== curRef.current.id) {
              return [...m, curRef.current];
            }
            return [...m.slice(0, -1), curRef.current];
          }
          // 没有 curRef (没收到 iteration), 复用最后一条 assistant 或创建新的
          if (m.length === 0 || m[m.length - 1].role !== "assistant") {
            const nm = { id: nextId(), role: "assistant" as const, text: e.text };
            curRef.current = nm;
            return [...m, nm];
          }
          const last = m[m.length - 1];
          const updated = { ...last, text: last.text + e.text };
          curRef.current = updated;
          return [...m.slice(0, -1), updated];
        });
        break;
      case "tool_calls":
        setMessages((m) => {
          if (curRef.current) {
            curRef.current = {
              ...curRef.current,
              toolCalls: [...(curRef.current.toolCalls || []), ...e.calls],
            };
            // 延迟创建: 如果还没在数组里, 现在加进去
            if (m.length === 0 || m[m.length - 1].id !== curRef.current.id) {
              return [...m, curRef.current];
            }
            return [...m.slice(0, -1), curRef.current];
          }
          // 没有 curRef, 复用最后一条 assistant 或创建新的
          if (m.length === 0 || m[m.length - 1].role !== "assistant") {
            const nm = { id: nextId(), role: "assistant" as const, text: "", toolCalls: e.calls };
            curRef.current = nm;
            return [...m, nm];
          }
          const last = m[m.length - 1];
          const updated = { ...last, toolCalls: [...(last.toolCalls || []), ...e.calls] };
          curRef.current = updated;
          return [...m.slice(0, -1), updated];
        });
        break;
      case "tool_results":
        // 结果挂到发起调用的 assistant 消息上
        setMessages((m) => {
          if (m.length === 0) return m;
          const last = m[m.length - 1];
          // 只挂到 assistant 消息上 (避免挂到 user 消息)
          if (last.role !== "assistant") return m;
          const updated = {
            ...last,
            toolResults: [...(last.toolResults || []), ...e.results],
          };
          curRef.current = updated;
          return [...m.slice(0, -1), updated];
        });
        break;
      case "notice":
        // 简化为系统消息
        setMessages((m) => [
          ...m,
          { id: nextId(), role: "system", text: `[${e.event}] ${e.detail}` },
        ]);
        break;
      case "complete":
        if (e.usage) setUsage({ in: e.usage.input_tokens || 0, out: e.usage.output_tokens || 0 });
        if (e.plan) setPlan(e.plan);
        if (e.artifacts) setArtifacts(e.artifacts);
        setAwaiting(e.awaiting);
        setStatus(e.kind === "awaiting" ? "awaiting" : e.kind === "error" ? "error" : "done");
        if (e.kind === "error" && e.error) setError(e.error);
        break;
      case "error":
        setStatus("error");
        setError(e.message);
        break;
    }
  }, []);

  // SSE 连接句柄 (切到 SSE; WS 保留向后兼容)
  const wsRef = useRef<{ close: () => void } | null>(null);

  const start = useCallback(
    (sid: string, prompt: string) => {
      setError(null);
      setStatus("running");
      setMessages((m) => [...m, { id: nextId(), role: "user", text: prompt }]);
      const conn = connectSSE(sid, prompt, (e) => handleEvent(e), (err) => {
        setError(String(err));
        setStatus("error");
      });
      wsRef.current = conn;
    },
    [handleEvent]
  );

  const loadFromState = useCallback(
    (state: {
      messages?: { role: string; content: unknown }[];
      plan?: PlanSnapshot | null;
      artifacts?: Record<string, ArtifactInfo>;
    }) => {
      curRef.current = null;
      setError(null);
      setIteration(0);
      setAwaiting(null);

      if (state.plan) setPlan(state.plan);
      else setPlan(null);

      if (state.artifacts) setArtifacts(state.artifacts);
      else setArtifacts({});

      if (state.messages && state.messages.length > 0) {
        const uiMsgs: UIMessage[] = [];
        for (const m of state.messages) {
          const role = m.role as UIMessage["role"];
          let text = "";
          const toolCalls: ToolCall[] = [];
          const toolResults: ToolResult[] = [];

          if (typeof m.content === "string") {
            text = m.content;
          } else if (Array.isArray(m.content)) {
            for (const block of m.content as Record<string, unknown>[]) {
              if (block.type === "text") text += (block.text as string) || "";
              else if (block.type === "tool_use")
                toolCalls.push({
                  id: block.id as string,
                  name: block.name as string,
                  input: block.input as Record<string, unknown>,
                });
              else if (block.type === "tool_result")
                toolResults.push({
                  tool_use_id: block.tool_use_id as string,
                  content: (block.content as string) || "",
                  is_error: (block.is_error as boolean) || false,
                });
            }
          }

          const msg: UIMessage = { id: nextId(), role, text };
          if (toolCalls.length > 0) msg.toolCalls = toolCalls;
          if (toolResults.length > 0) msg.toolResults = toolResults;
          // 跳过 harness-notice 消息 (max_tokens 续传等内部提示, 不应显示给用户)
          if ((m as Record<string, unknown>).harness_notice) {
            continue;
          }
          // 跳过空 user 消息: 后端把工具结果存为 role=user (Anthropic 风格),
          // 这些消息 text 为空且只有 tool_result blocks, 不应渲染为用户气泡
          if (role === "user" && !text.trim() && toolCalls.length === 0) {
            continue;
          }
          uiMsgs.push(msg);
        }
        setMessages(uiMsgs);
        setStatus("done");
      } else {
        setMessages([]);
        setStatus("idle");
      }
    },
    []
  );

  return {
    messages,
    status,
    iteration,
    plan,
    artifacts,
    usage,
    error,
    awaiting,
    start,
    reset,
    setPlan,
    setStatus,
    setAwaiting,
    loadFromState,
  };
}
