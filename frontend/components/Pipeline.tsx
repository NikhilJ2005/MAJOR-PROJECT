"use client";

export type NodeState = "idle" | "active" | "done" | "failed";

export type StageInfo = { state: NodeState; model?: string; seconds?: number; detail?: string };

export const NODE_META: Record<string, { label: string; role: string; icon: string }> = {
  analyze: { label: "Analyst", role: "splits the request into requirements", icon: "☷" },
  parse_spec: { label: "Architect", role: "data model · access · actions", icon: "◆" },
  plan: { label: "Planner", role: "files in dependency order", icon: "☰" },
  write_tests: { label: "Test Engineer", role: "rule tests before any code", icon: "✓" },
  generate: { label: "API Engineer", role: "models · schemas · services · routers", icon: "⌘" },
  validate: { label: "Validator", role: "static → import → boot → tests", icon: "▣" },
  bug_hunt: { label: "Bug Hunter", role: "writes adversarial tests", icon: "⌖" },
  classify: { label: "Error Classifier", role: "names the failure + file", icon: "⚑" },
  reflect: { label: "Reflector", role: "patches only the faulty file", icon: "✎" },
  package: { label: "Packager", role: "migrations · zip · live preview", icon: "⬢" },
  failure_report: { label: "Circuit Breaker", role: "stops when there is no progress", icon: "⛔" },
};

const MAIN = ["analyze", "parse_spec", "plan", "write_tests", "generate", "validate"];

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
    <div className={`relative min-w-0 rounded-xl border-2 px-3 py-2.5 transition-all duration-300 ${card[info.state]}`}>
      <div className="flex items-center gap-2">
        <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-zinc-800 text-sm ${info.state === "active" ? "animate-pulse text-sky-300" : ""}`}>
          {meta.icon}
        </span>
        <div className="min-w-0">
          <div className={`${compact ? "text-sm" : "text-[15px]"} truncate font-semibold leading-tight`}>{meta.label}</div>
          <div className="truncate text-[11px] text-zinc-400">{meta.role}</div>
        </div>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[11px]">
        <span className={`font-semibold uppercase ${badge[info.state]}`}>
          {info.state === "active" && <span className="mr-1 inline-block h-1.5 w-1.5 animate-ping rounded-full bg-sky-400 align-middle" />}
          {label[info.state]}
        </span>
        {info.seconds != null && info.state !== "active" && <span className="text-zinc-500">{info.seconds.toFixed(1)}s</span>}
        {info.model && <span className="truncate text-violet-300">{info.model}</span>}
      </div>
      {info.detail && <div className="mt-1 truncate font-mono text-[11px] text-zinc-400" title={info.detail}>{info.detail}</div>}
    </div>
  );
}

function Arrow({ lit }: { lit: boolean }) {
  return (
    <div className="hidden items-center lg:flex">
      <div className={`h-0.5 w-full ${lit ? "bg-emerald-500/70" : "bg-zinc-700"}`} />
      <div className={`-ml-1 h-0 w-0 border-y-[5px] border-l-[7px] border-y-transparent ${lit ? "border-l-emerald-500/70" : "border-l-zinc-700"}`} />
    </div>
  );
}

function Loop({ title, active, tone, badge, hint, children }: {
  title: string;
  active: boolean;
  tone: "amber" | "violet";
  badge: string;
  hint: string;
  children: React.ReactNode;
}) {
  const on = tone === "amber" ? "border-amber-400/70 bg-amber-500/5" : "border-violet-400/70 bg-violet-500/5";
  const text = tone === "amber" ? "text-amber-300" : "text-violet-300";
  const pill = tone === "amber" ? "bg-amber-400/15 text-amber-300" : "bg-violet-400/15 text-violet-300";
  return (
    <div className={`rounded-2xl border-2 border-dashed p-3 transition-colors ${active ? on : "border-zinc-800"}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`text-sm font-semibold uppercase tracking-wider ${active ? text : "text-zinc-500"}`}>{title}</span>
        <span className={`rounded-full px-2.5 py-0.5 font-mono text-xs ${active ? pill : "bg-zinc-800 text-zinc-500"}`}>{badge}</span>
        <span className="text-xs text-zinc-500">{hint}</span>
      </div>
      {children}
    </div>
  );
}

export function Pipeline({
  stages,
  iteration,
  bugsFound,
  stopReason,
}: {
  stages: Record<string, StageInfo>;
  iteration: number;
  bugsFound: number;
  stopReason?: string | null;
}) {
  const s = (id: string): StageInfo => stages[id] ?? { state: "idle" };
  const end = s("failure_report").state !== "idle" ? "failure_report" : "package";
  const cols = [...MAIN, end];
  const healing = ["classify", "reflect"].some((n) => s(n).state !== "idle");
  const hunting = s("bug_hunt").state !== "idle";
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-[repeat(6,minmax(0,1fr)_14px)_minmax(0,1fr)]">
        {cols.map((id, i) => (
          <div key={id} className="contents">
            <Stage id={id} info={s(id)} />
            {i < cols.length - 1 && <Arrow lit={s(id).state === "done" || (id === "validate" && s(end).state !== "idle")} />}
          </div>
        ))}
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Loop
          title="↺ Self-healing loop"
          active={healing}
          tone="amber"
          badge={`${iteration} repair${iteration === 1 ? "" : "s"}`}
          hint={stopReason ? `stopped: ${stopReason}` : "keeps going while each repair makes progress · cheap → strong model → templates"}
        >
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_20px_minmax(0,1fr)]">
            <Stage id="classify" info={s("classify")} compact />
            <Arrow lit={s("classify").state === "done"} />
            <Stage id="reflect" info={s("reflect")} compact />
          </div>
        </Loop>
        <Loop
          title="⌖ Bug hunt"
          active={hunting}
          tone="violet"
          badge={`${bugsFound} bug${bugsFound === 1 ? "" : "s"} found`}
          hint="after the contract passes · no bugs are planted"
        >
          <Stage id="bug_hunt" info={s("bug_hunt")} compact />
        </Loop>
      </div>
    </div>
  );
}
