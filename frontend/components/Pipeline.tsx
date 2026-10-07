"use client";

export type NodeState = "idle" | "active" | "done" | "failed";

export type StageInfo = { state: NodeState; model?: string; seconds?: number; detail?: string };

export const NODE_META: Record<string, { label: string; role: string; icon: string }> = {
  parse_spec: { label: "Architect", role: "designs data model + API", icon: "◆" },
  plan: { label: "Planner", role: "orders files by dependency", icon: "☰" },
  generate: { label: "Code Generator", role: "writes models · schemas · routers", icon: "⌘" },
  validate: { label: "Sandbox Validator", role: "import → boot → API tests", icon: "▣" },
  classify: { label: "Error Classifier", role: "names the failure + file", icon: "⚑" },
  reflect: { label: "Reflector", role: "patches only the faulty file", icon: "✎" },
  package: { label: "Packager", role: "project zip + live preview", icon: "⬢" },
  failure_report: { label: "Circuit Breaker", role: "stops after 3 attempts", icon: "⛔" },
};

const MAIN = ["parse_spec", "plan", "generate", "validate"];

const card: Record<NodeState, string> = {
  idle: "border-zinc-800 bg-zinc-900/50 text-zinc-500",
  active: "border-sky-400 bg-sky-500/10 text-white shadow-[0_0_32px_-4px] shadow-sky-500/50 scale-[1.03]",
  done: "border-emerald-500/60 bg-emerald-500/5 text-zinc-100",
  failed: "border-rose-500 bg-rose-500/10 text-white shadow-[0_0_28px_-6px] shadow-rose-500/50",
};

const badge: Record<NodeState, string> = {
  idle: "text-zinc-600",
  active: "text-sky-300",
  done: "text-emerald-300",
  failed: "text-rose-300",
};

const label: Record<NodeState, string> = { idle: "waiting", active: "running", done: "done", failed: "failed" };

function Stage({ id, info, compact }: { id: string; info: StageInfo; compact?: boolean }) {
  const meta = NODE_META[id];
  return (
    <div className={`relative rounded-xl border-2 px-4 py-3 transition-all duration-300 ${card[info.state]}`}>
      <div className="flex items-center gap-2">
        <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-800 text-base ${info.state === "active" ? "animate-pulse text-sky-300" : ""}`}>
          {meta.icon}
        </span>
        <div className="min-w-0">
          <div className={`${compact ? "text-sm" : "text-base"} font-semibold leading-tight`}>{meta.label}</div>
          <div className="truncate text-xs text-zinc-400">{meta.role}</div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[11px]">
        <span className={`font-semibold uppercase ${badge[info.state]}`}>
          {info.state === "active" && <span className="mr-1 inline-block h-1.5 w-1.5 animate-ping rounded-full bg-sky-400 align-middle" />}
          {label[info.state]}
        </span>
        {info.seconds != null && info.state !== "active" && <span className="text-zinc-500">{info.seconds.toFixed(1)}s</span>}
        {info.model && <span className="truncate text-violet-300">{info.model}</span>}
      </div>
      {info.detail && <div className="mt-1 truncate font-mono text-[11px] text-zinc-400">{info.detail}</div>}
    </div>
  );
}

function Arrow({ lit }: { lit: boolean }) {
  return (
    <div className="hidden items-center md:flex">
      <div className={`h-0.5 w-full ${lit ? "bg-emerald-500/70" : "bg-zinc-700"}`} />
      <div className={`-ml-1 h-0 w-0 border-y-[5px] border-l-[7px] border-y-transparent ${lit ? "border-l-emerald-500/70" : "border-l-zinc-700"}`} />
    </div>
  );
}

export function Pipeline({
  stages,
  iteration,
  maxIterations,
}: {
  stages: Record<string, StageInfo>;
  iteration: number;
  maxIterations: number;
}) {
  const s = (id: string): StageInfo => stages[id] ?? { state: "idle" };
  const end = s("failure_report").state !== "idle" ? "failure_report" : "package";
  const loopActive = ["classify", "reflect"].some((n) => s(n).state !== "idle");
  const cols = [...MAIN, end];
  return (
    <div className="space-y-3">
      <div className="grid gap-2 md:grid-cols-[1fr_24px_1fr_24px_1fr_24px_1fr_24px_1fr]">
        {cols.map((id, i) => (
          <div key={id} className="contents">
            <Stage id={id} info={s(id)} />
            {i < cols.length - 1 && <Arrow lit={s(id).state === "done" || (id === "validate" && s(end).state !== "idle")} />}
          </div>
        ))}
      </div>
      <div className="grid md:grid-cols-[1fr_24px_1fr_24px_1fr_24px_1fr_24px_1fr]">
        <div
          className={`rounded-2xl border-2 border-dashed p-3 transition-colors md:col-span-5 md:col-start-5 ${
            loopActive ? "border-amber-400/70 bg-amber-500/5" : "border-zinc-800"
          }`}
        >
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <span className={`text-sm font-semibold uppercase tracking-wider ${loopActive ? "text-amber-300" : "text-zinc-500"}`}>
              ↺ Self-healing loop
            </span>
            <span className={`rounded-full px-2.5 py-0.5 font-mono text-sm ${loopActive ? "bg-amber-400/15 text-amber-300" : "bg-zinc-800 text-zinc-500"}`}>
              attempt {iteration}/{maxIterations}
            </span>
            <span className="text-xs text-zinc-500">validator fails → classify → patch → validate again</span>
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_24px_1fr_auto]">
            <Stage id="classify" info={s("classify")} compact />
            <Arrow lit={s("classify").state === "done"} />
            <Stage id="reflect" info={s("reflect")} compact />
            <div className={`hidden items-center pl-2 font-mono text-xs sm:flex ${loopActive ? "text-amber-300" : "text-zinc-600"}`}>↩ re-validate</div>
          </div>
        </div>
      </div>
    </div>
  );
}
