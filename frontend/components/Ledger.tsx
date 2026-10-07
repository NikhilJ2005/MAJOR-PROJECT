import { useEffect, useRef } from "react";
import type { LedgerEntry } from "@/lib/api";

const colors: Record<string, string> = {
  architect: "text-sky-300",
  planner: "text-zinc-300",
  api_engineer: "text-sky-300",
  fault_injector: "text-rose-300",
  validator: "text-emerald-300",
  error_classifier: "text-amber-300",
  reflector: "text-amber-300",
  template_repair: "text-amber-300",
  devops_packager: "text-emerald-300",
  circuit_breaker: "text-rose-400",
};

const AGENT_NAMES: Record<string, string> = {
  architect: "Architect",
  planner: "Planner",
  api_engineer: "Code Generator",
  fault_injector: "Bug injector (demo)",
  validator: "Sandbox Validator",
  error_classifier: "Error Classifier",
  reflector: "Reflector",
  template_repair: "Template repair",
  devops_packager: "Packager",
  circuit_breaker: "Circuit Breaker",
};

const base = (agent: string) => agent.split(" ")[0];
export const agentName = (agent: string) => AGENT_NAMES[base(agent)] ?? agent;
export const agentColor = (agent: string) => colors[base(agent)] ?? "text-zinc-300";

export function Diff({ text }: { text: string }) {
  return (
    <pre className="mt-2 overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-950 py-2 font-mono text-xs leading-5">
      {text.split("\n").map((line, i) => {
        const cls = line.startsWith("+++") || line.startsWith("---")
          ? "text-zinc-500"
          : line.startsWith("+")
            ? "bg-emerald-500/15 text-emerald-200"
            : line.startsWith("-")
              ? "bg-rose-500/15 text-rose-200"
              : line.startsWith("@@")
                ? "text-sky-400/70"
                : "text-zinc-400";
        return (
          <div key={i} className={`whitespace-pre px-3 ${cls}`}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

export function Ledger({ entries, showDiffs = false }: { entries: LedgerEntry[]; showDiffs?: boolean }) {
  if (!entries.length) return <Empty text="Every agent action is recorded here with its rationale." />;
  return (
    <ol className="relative space-y-4 border-l-2 border-zinc-800 pl-5">
      {entries.map((e, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-zinc-950 bg-zinc-500" />
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className={`text-sm font-semibold ${agentColor(e.agent)}`}>{agentName(e.agent)}</span>
            <span className="text-base text-zinc-100">{e.action}</span>
            {e.model && <span className="rounded bg-violet-500/15 px-1.5 font-mono text-[11px] text-violet-300">{e.model}</span>}
            {e.tokens ? <span className="font-mono text-[11px] text-zinc-500">{e.tokens} tok</span> : null}
          </div>
          {e.rationale && <p className="text-sm text-zinc-400">{e.rationale}</p>}
          {showDiffs && e.diff && Object.entries(e.diff).map(([path, d]) => <Diff key={path} text={d} />)}
          {!showDiffs && e.files && e.files.length > 0 && e.files.length <= 6 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {e.files.map((f) => (
                <span key={f} className="rounded bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">{f}</span>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

const HEAL_AGENTS = ["fault_injector", "validator", "error_classifier", "reflector", "template_repair", "circuit_breaker"];

export function SelfHealing({ entries, done }: { entries: LedgerEntry[]; done: boolean }) {
  const heal = entries.filter((e) => HEAL_AGENTS.includes(base(e.agent)));
  const repaired = heal.some((e) => base(e.agent) === "reflector" || e.agent === "template_repair");
  if (!heal.length) return <Empty text="When the sandbox finds a bug, the classify → patch → re-validate loop shows up here, with the exact code change." />;
  return (
    <div className="space-y-4">
      {done && (
        <p className={`rounded-lg border px-4 py-3 text-base ${repaired ? "border-amber-400/40 bg-amber-500/5 text-amber-100" : "border-emerald-500/40 bg-emerald-500/5 text-emerald-100"}`}>
          {repaired
            ? "The agent found a bug in its own output, located the faulty file, patched it and re-validated, with no human involved."
            : "The generated code passed validation on the first try; no healing was needed."}
        </p>
      )}
      <Ledger entries={heal} showDiffs />
    </div>
  );
}

export function ActivityFeed({ entries, running }: { entries: (LedgerEntry & { ts?: number })[]; running: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [entries.length]);
  const t0 = entries[0]?.ts;
  return (
    <div className="flex h-full min-h-[320px] flex-col rounded-xl border border-zinc-800 bg-black/60">
      <div className="flex items-center gap-2 border-b border-zinc-800 px-4 py-2.5">
        <span className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-sky-400" : "bg-zinc-600"}`} />
        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-400">Live agent activity</span>
      </div>
      <div className="flex-1 space-y-1.5 overflow-y-auto p-3 font-mono text-[13px] leading-snug">
        {entries.length === 0 && <div className="text-zinc-600">$ waiting for a run…</div>}
        {entries.map((e, i) => (
          <div key={i}>
            <span className="text-zinc-600">{t0 && e.ts ? `+${(e.ts - t0).toFixed(1).padStart(5)}s ` : ""}</span>
            <span className={`font-semibold ${agentColor(e.agent)}`}>{agentName(e.agent)}</span>
            <span className="text-zinc-300"> {e.action}</span>
            {e.model && <span className="text-violet-300/80"> [{e.model}]</span>}
          </div>
        ))}
        {running && <div className="animate-pulse text-sky-400">▍</div>}
        <div ref={end} />
      </div>
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center text-base text-zinc-500">{text}</div>;
}
