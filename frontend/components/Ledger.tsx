import { useEffect, useRef } from "react";
import type { Bug, LedgerEntry, ReviewNote } from "@/lib/api";

const colors: Record<string, string> = {
  architect: "text-signal-300",
  planner: "text-ink-200",
  code_generator: "text-sky-300",
  validator: "text-emerald-300",
  error_classifier: "text-amber-300",
  reflector: "text-amber-300",
  template_repair: "text-amber-300",
  judge: "text-fuchsia-300",
  devops_packager: "text-emerald-300",
  circuit_breaker: "text-rose-300",
};

const AGENT_NAMES: Record<string, string> = {
  architect: "Architect",
  planner: "Planner",
  code_generator: "Code Generator",
  validator: "Validator",
  error_classifier: "Error Classifier",
  reflector: "Reflector",
  template_repair: "Reflector (templates)",
  judge: "Reflector (judge)",
  devops_packager: "Packager",
  circuit_breaker: "Circuit Breaker",
};

const base = (agent: string) => agent.split(" ")[0];
export const agentName = (agent: string) =>
  agent.startsWith("plugin:") ? `Plugin ${agent.slice(7)}` : (AGENT_NAMES[base(agent)] ?? agent);
export const agentColor = (agent: string) => (agent.startsWith("plugin:") ? "text-lime-300" : (colors[base(agent)] ?? "text-ink-300"));

export function Diff({ text }: { text: string }) {
  return (
    <pre className="mt-2 overflow-x-auto rounded-lg border border-ink-800 bg-ink-950 py-2 font-mono text-xs leading-5">
      {text.split("\n").map((line, i) => {
        const cls = line.startsWith("+++") || line.startsWith("---")
          ? "text-ink-500"
          : line.startsWith("+")
            ? "bg-emerald-500/15 text-emerald-200"
            : line.startsWith("-")
              ? "bg-rose-500/15 text-rose-200"
              : line.startsWith("@@")
                ? "text-sky-400/70"
                : "text-ink-400";
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
    <ol className="relative space-y-4 border-l-2 border-ink-800 pl-5">
      {entries.map((e, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-ink-950 bg-ink-500" />
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className={`text-sm font-semibold ${agentColor(e.agent)}`}>{agentName(e.agent)}</span>
            <span className="text-base text-ink-100">{e.action}</span>
            {e.model && <span className="rounded bg-violet-500/15 px-1.5 font-mono text-[11px] text-violet-300">{e.model}</span>}
            {e.tokens ? <span className="font-mono text-[11px] text-ink-500">{e.tokens} tok</span> : null}
          </div>
          {e.rationale && <p className="text-sm text-ink-400">{e.rationale}</p>}
          {showDiffs && e.diff && Object.entries(e.diff).map(([path, d]) => <Diff key={path} text={d} />)}
          {!showDiffs && e.files && e.files.length > 0 && e.files.length <= 6 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {e.files.map((f) => (
                <span key={f} className="rounded bg-ink-800/80 px-1.5 py-0.5 font-mono text-[10px] text-ink-400">{f}</span>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

const HEAL_AGENTS = ["validator", "error_classifier", "reflector", "template_repair", "judge", "circuit_breaker"];

const FINDER_STYLE: Record<string, string> = {
  "edge-case tests": "bg-violet-500/15 text-violet-300",
  "rule tests": "bg-teal-500/15 text-teal-300",
  "frontend check": "bg-pink-500/15 text-pink-300",
  "contract tests": "bg-sky-500/15 text-sky-300",
  "static checker": "bg-orange-500/15 text-orange-300",
  validator: "bg-emerald-500/15 text-emerald-300",
};

export function BugsAndHealing({ entries, bugs, notes, done, stopReason }: {
  entries: LedgerEntry[];
  bugs: Bug[];
  notes: ReviewNote[];
  done: boolean;
  stopReason?: string | null;
}) {
  const heal = entries.filter((e) => HEAL_AGENTS.includes(base(e.agent)) && !(base(e.agent) === "validator" && !bugs.length));
  const fixed = bugs.filter((b) => b.fixed).length;
  if (!bugs.length && !heal.length && !notes.length)
    return <Empty text="Bugs the agents find on their own (static checks, contract, edge-case and rule tests, the frontend check) and every fix appear here." />;
  return (
    <div className="space-y-5">
      {done && (
        <p className={`rounded-lg border px-4 py-3 text-base ${bugs.length ? "border-amber-400/40 bg-amber-500/5 text-amber-100" : "border-emerald-500/40 bg-emerald-500/5 text-emerald-100"}`}>
          {bugs.length
            ? `The agents found ${bugs.length} bug${bugs.length === 1 ? "" : "s"} on their own and fixed ${fixed}, with no human involved. Nothing was planted.`
            : "No bugs found: the code passed the static checks, the frontend check and every test on the first try."}
          {stopReason && <span className="mt-1 block text-sm text-rose-200">Healing stopped: {stopReason}</span>}
        </p>
      )}
      {bugs.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-ink-800">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-ink-400">
              <tr>
                <th className="px-4 py-2">found by</th>
                <th className="px-4 py-2">problem</th>
                <th className="px-4 py-2 text-right">status</th>
              </tr>
            </thead>
            <tbody>
              {bugs.map((b) => (
                <tr key={b.id} className="border-t border-ink-800/60">
                  <td className="px-4 py-2">
                    <span className={`rounded px-1.5 py-0.5 text-xs ${FINDER_STYLE[b.found_by] ?? "bg-ink-800 text-ink-300"}`}>{b.found_by}</span>
                  </td>
                  <td className="max-w-[520px] truncate px-4 py-2 font-mono text-xs text-ink-300" title={b.detail}>{b.detail}</td>
                  <td className={`px-4 py-2 text-right text-xs font-semibold ${b.fixed ? "text-emerald-300" : "text-rose-300"}`}>
                    {b.fixed ? "fixed ✓" : b.open ? "open (listed in ledger)" : "not fixed"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {heal.length > 0 && <Ledger entries={heal} showDiffs />}
      {notes.length > 0 && (
        <div>
          <h4 className="mb-2 text-[11px] text-ink-500">Review notes (low severity, not blocking)</h4>
          <ul className="space-y-1 font-mono text-xs text-ink-400">
            {notes.map((n, i) => (
              <li key={i}>
                <span className="text-ink-500">{n.path}:{n.line}</span> [{n.rule}] {n.message}
              </li>
            ))}
          </ul>
        </div>
      )}
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
    <div className="flex h-full min-h-[320px] flex-col rounded-xl border border-ink-800 bg-black/60">
      <div className="flex items-center gap-2 border-b border-ink-800 px-4 py-2.5">
        <span className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-sky-400" : "bg-ink-600"}`} />
        <span className="text-xs font-semibold text-ink-400">Live agent activity</span>
      </div>
      <div className="flex-1 space-y-1.5 overflow-y-auto p-3 font-mono text-[13px] leading-snug">
        {entries.length === 0 && <div className="text-ink-600">$ waiting for a run…</div>}
        {entries.map((e, i) => (
          <div key={i}>
            <span className="text-ink-600">{t0 && e.ts ? `+${(e.ts - t0).toFixed(1).padStart(5)}s ` : ""}</span>
            <span className={`font-semibold ${agentColor(e.agent)}`}>{agentName(e.agent)}</span>
            <span className="text-ink-300"> {e.action}</span>
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
  return <div className="rounded-lg border border-dashed border-ink-800 p-8 text-center text-base text-ink-500">{text}</div>;
}
