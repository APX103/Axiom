// 验证面板: plan 步骤 + frame 状态。
// 对应原版 plan artifact + verification_checks (阶段 3 reviewer findings 后续接入)。
import type { PlanSnapshot } from "../types";
import type { RunStatus } from "../hooks/useSession";
import { EmptyHint } from "./WorkspacePanel";

export function PlanPanel({
  plan,
  status,
  awaiting,
  onApprove,
}: {
  plan: PlanSnapshot | null;
  status: RunStatus;
  awaiting: string | null;
  onApprove: () => void;
}) {
  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3">
        <h2 className="text-sm font-semibold text-default flex items-center gap-2">
          <PlanIcon />
          计划与验证
        </h2>
        <p className="text-[10px] text-faint mt-0.5">执行计划 · 状态门控</p>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        <section>
          <div className="text-[10px] font-semibold text-faint uppercase tracking-wider mb-2">计划步骤</div>
          {!plan ? (
            <EmptyHint text="无计划" sub="plan mode 下 agent 会先生成计划" />
          ) : (
            <ol className="space-y-2">
              {plan.steps.map((s, i) => (
                <li
                  key={s.id}
                  className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-page"
                >
                  <StepIcon status={s.status} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-mono text-faint">{i + 1}.</span>
                      <span className="text-[10px] font-mono text-faint">{s.id}</span>
                    </div>
                    <div className="text-[13px] text-default mt-0.5 leading-snug">{s.description}</div>
                    {s.note && <div className="text-[11px] text-faint mt-1">{s.note}</div>}
                  </div>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${statusColor(s.status)}`}>
                    {s.status}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {status === "awaiting" && awaiting === "plan_approval" && (
            <button
              onClick={onApprove}
              className="mt-3 w-full py-2.5 rounded-lg bg-success hover:bg-success/90 text-inverse text-sm font-medium shadow-md transition-all active:scale-[0.98] flex items-center justify-center gap-2"
            >
              <CheckIcon />
              批准计划并继续执行
            </button>
          )}
        </section>
      </div>
    </div>
  );
}

function StepIcon({ status }: { status: string }) {
  const color: Record<string, string> = {
    pending: "text-faint",
    in_progress: "text-info animate-pulse",
    completed: "text-success",
    skipped: "text-faint",
  };
  return (
    <svg
      className={`w-4 h-4 mt-0.5 shrink-0 ${color[status] || "text-faint"}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {status === "completed" ? (
        <>
          <circle cx="12" cy="12" r="10" />
          <path d="M9 12l2 2 4-4" />
        </>
      ) : status === "in_progress" ? (
        <>
          <circle cx="12" cy="12" r="10" />
          <path d="M12 6v6l4 2" />
        </>
      ) : (
        <>
          <circle cx="12" cy="12" r="10" />
          <circle cx="12" cy="12" r="3" />
        </>
      )}
    </svg>
  );
}

function statusColor(status: string): string {
  return (
    {
      pending: "bg-elevated text-muted",
      in_progress: "bg-info/15 text-info",
      completed: "bg-success/15 text-success",
      skipped: "bg-elevated text-faint",
    }[status] || "bg-elevated text-muted"
  );
}

function PlanIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 11l3 3L22 4" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
