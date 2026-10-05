"use client";

export type NodeState = "idle" | "active" | "waiting" | "done" | "failed";

export const NODE_META: Record<string, { label: string; role: string }> = {
  parse_spec: { label: "Spec Architect", role: "NL → validated ProjectSpec" },
  approve_spec: { label: "Human Approval", role: "interrupt() · edit or approve" },
  plan: { label: "Planner", role: "dependency-ordered file plan" },
  generate: { label: "API Engineer", role: "models · schemas · routers" },
  review: { label: "Review Council", role: "security + architecture" },
  validate: { label: "Sandbox Validator", role: "import → boot → tests" },
  classify: { label: "Error Classifier", role: "taxonomy + fault localisation" },
  reflect: { label: "Reflector", role: "targeted patch (delta context)" },
  package: { label: "Packager", role: "zip + change ledger" },
  failure_report: { label: "Circuit Breaker", role: "stop + diagnostic report" },
};

const MAIN = ["parse_spec", "approve_spec", "plan", "generate", "review", "validate"];

const dot: Record<NodeState, string> = {
  idle: "bg-zinc-700",
  active: "bg-sky-400 animate-pulse",
  waiting: "bg-amber-400 animate-pulse",
  done: "bg-emerald-400",
  failed: "bg-rose-500",
};

const ring: Record<NodeState, string> = {
  idle: "border-zinc-800 text-zinc-500",
  active: "border-sky-500/70 text-zinc-100 shadow-[0_0_24px_-6px] shadow-sky-500/60",
  waiting: "border-amber-400/70 text-zinc-100 shadow-[0_0_24px_-6px] shadow-amber-400/60",
  done: "border-emerald-600/50 text-zinc-200",
  failed: "border-rose-600/70 text-zinc-100",
};

function Node({ id, state }: { id: string; state: NodeState }) {
  const meta = NODE_META[id];
  return (
    <div className={`rounded-lg border bg-zinc-900/80 px-3 py-2 transition-all ${ring[state]}`}>
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 shrink-0 rounded-full ${dot[state]}`} />
        <span className="text-sm font-medium">{meta.label}</span>
      </div>
      <div className="mt-0.5 pl-4 font-mono text-[11px] text-zinc-500">{meta.role}</div>
    </div>
  );
}

function Arrow() {
  return <div className="mx-auto h-3 w-px bg-zinc-700" />;
}

export function Pipeline({
  states,
  iteration,
  maxIterations,
}: {
  states: Record<string, NodeState>;
  iteration: number;
  maxIterations: number;
}) {
  const s = (id: string) => states[id] ?? "idle";
  const loopActive = ["classify", "reflect"].some((n) => s(n) !== "idle");
  const end = s("failure_report") !== "idle" ? "failure_report" : "package";
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3">
      <div>
        {MAIN.map((id) => (
          <div key={id}>
            <Node id={id} state={s(id)} />
            <Arrow />
          </div>
        ))}
        <Node id={end} state={s(end)} />
      </div>
      <div className="flex flex-col justify-end pb-[3.6rem]">
        <div
          className={`rounded-xl border border-dashed p-2 transition-colors ${
            loopActive ? "border-amber-500/60 bg-amber-500/5" : "border-zinc-800"
          }`}
        >
          <div className="mb-2 px-1 text-[11px] uppercase tracking-wider text-zinc-500">
            <div>Self-healing loop</div>
            <div className={`font-mono normal-case tracking-normal ${loopActive ? "text-amber-400" : ""}`}>
              attempt {iteration}/{maxIterations}
            </div>
          </div>
          <Node id="classify" state={s("classify")} />
          <Arrow />
          <Node id="reflect" state={s("reflect")} />
          <div className="mt-2 px-1 font-mono text-[11px] text-zinc-500">↩ back to validator</div>
        </div>
      </div>
    </div>
  );
}
