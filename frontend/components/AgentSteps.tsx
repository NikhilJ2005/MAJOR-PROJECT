"use client";

import type { LedgerEntry, RunEvent, Snapshot } from "@/lib/api";

export type StepState = "idle" | "active" | "done" | "failed";
type Step = { id: string; name: string; job: string; state: StepState; detail?: string; live?: string; seconds?: number; count?: number };

// The 8 agents, in the order a run meets them.
const AGENTS: [string, string, string][] = [
  ["parse_spec", "Architect", "requirements, design and rule tests"],
  ["plan", "Planner", "files in dependency order"],
  ["generate", "Code Generator", "backend, frontend and the app's look"],
  ["validate", "Validator", "static, frontend, import, boot and tests"],
  ["classify", "Error Classifier", "what failed, and where"],
  ["reflect", "Reflector", "reads, fixes and re-runs tests with tools"],
  ["package", "Packager", "migrations, zip and live app"],
  ["failure_report", "Circuit Breaker", "stops when repairs stop helping"],
];
const NEXT: Record<string, string> = { parse_spec: "plan", plan: "generate", generate: "validate", reflect: "validate", classify: "reflect" };
const AGENT_OF: Record<string, string> = {
  architect: "parse_spec", planner: "plan", code_generator: "generate", validator: "validate", error_classifier: "classify",
  reflector: "reflect", template_repair: "reflect", judge: "reflect", devops_packager: "package", circuit_breaker: "failure_report",
};

/** Step states from live events (running) or from the ledger (finished runs). */
export function deriveSteps(events: RunEvent[], snap: Snapshot | null): Step[] {
  const steps: Record<string, Step> = Object.fromEntries(AGENTS.map(([id, name, job]) => [id, { id, name, job, state: "idle" }]));
  const status = snap?.status ?? "running";
  if (events.length) {
    let prev: number | null = null;
    for (const ev of events) {
      if (ev.type === "status" && ev.status === "running" && prev === null) {
        prev = ev.ts;
        steps.parse_spec.state = "active";
      }
      if (ev.type === "progress" && ev.node && steps[ev.node]) {
        steps[ev.node].live = ev.text;  // what the agent is doing right now
        continue;
      }
      if (ev.type !== "node" || !ev.node || !steps[ev.node]) continue;
      const s = steps[ev.node];
      s.live = undefined;
      s.seconds = (s.seconds ?? 0) + (prev !== null ? ev.ts - prev : 0);
      prev = ev.ts;
      s.count = (s.count ?? 0) + 1;
      s.detail = (ev.ledger ?? [])[0]?.action;
      const ok = ev.node !== "validate" || ev.validation?.ok;
      s.state = ev.node === "failure_report" ? "failed" : ok ? "done" : "failed";
      const next = ev.node === "validate" ? (ok ? "package" : "classify") : NEXT[ev.node];
      if (next && status === "running") steps[next].state = "active";
    }
  } else {
    for (const e of snap?.ledger ?? []) markFromLedger(steps, e);
  }
  if (status !== "running") for (const s of Object.values(steps)) if (s.state === "active") s.state = "idle";
  if (status === "succeeded" && steps.validate.state === "failed") steps.validate.state = "done";
  return AGENTS.map(([id]) => steps[id]);
}

function markFromLedger(steps: Record<string, Step>, e: LedgerEntry) {
  const id = AGENT_OF[e.agent.split(" ")[0]];
  if (!id) return;
  const s = steps[id];
  s.count = (s.count ?? 0) + (id === "reflect" || id === "validate" ? 1 : 0);
  s.detail = e.action;
  s.state = id === "failure_report" ? "failed" : "done";
}

const DOT: Record<StepState, string> = {
  idle: "border-ink-600 bg-ink-950",
  active: "border-signal-400 bg-signal-400 shadow-[0_0_0_4px_rgba(242,181,68,0.18)]",
  done: "border-emerald-400 bg-emerald-400",
  failed: "border-rose-400 bg-rose-400",
};

/** The blueprint line: one station per agent. */
export function AgentSteps({ steps, compact }: { steps: Step[]; compact?: boolean }) {
  const shown = steps.filter((s) => !compact || s.state !== "idle");
  return (
    <ol className="relative ml-1.5 border-l border-dashed border-ink-600">
      {shown.map((s) => (
        <li key={s.id} className="relative pb-3 pl-5 last:pb-0">
          <span className={`absolute -left-[6px] top-1.5 h-[11px] w-[11px] rounded-full border-2 ${DOT[s.state]} ${s.state === "active" ? "animate-pulse" : ""}`} />
          <div className="flex items-baseline gap-2">
            <span className={`text-sm font-medium ${s.state === "idle" ? "text-ink-500" : "text-ink-100"}`}>{s.name}</span>
            {s.id === "reflect" && (s.count ?? 0) > 0 && <span className="text-xs text-signal-300">{s.count} repair{s.count === 1 ? "" : "s"}</span>}
            {s.seconds != null && s.state !== "active" && s.seconds > 0.05 && <span className="text-xs text-ink-500">{s.seconds.toFixed(1)}s</span>}
          </div>
          <p className={`text-xs leading-5 ${s.state === "failed" ? "text-rose-300" : "text-ink-400"}`}>
            {s.state === "active" ? s.live ?? `Working: ${s.job}…` : s.detail ?? s.job}
          </p>
        </li>
      ))}
    </ol>
  );
}
