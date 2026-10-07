import type { LedgerEntry } from "@/lib/api";

const colors: Record<string, string> = {
  spec_architect: "text-sky-300",
  human: "text-amber-300",
  planner: "text-zinc-300",
  api_engineer: "text-sky-300",
  fault_injector: "text-rose-300",
  review_council: "text-violet-300",
  validator: "text-emerald-300",
  error_classifier: "text-amber-300",
  reflector: "text-amber-300",
  template_repair: "text-amber-300",
  devops_packager: "text-emerald-300",
  circuit_breaker: "text-rose-400",
};

export function Ledger({ entries }: { entries: LedgerEntry[] }) {
  if (!entries.length) return <Empty text="Every agent action is recorded here with its rationale." />;
  return (
    <ol className="relative space-y-3 border-l border-zinc-800 pl-4">
      {entries.map((e, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-zinc-600" />
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className={`font-mono text-xs ${colors[e.agent.split(" ")[0]] ?? "text-zinc-300"}`}>{e.agent}</span>
            <span className="text-sm text-zinc-200">{e.action}</span>
            {e.tokens ? <span className="font-mono text-[11px] text-zinc-500">{e.tokens} tok</span> : null}
          </div>
          {e.rationale && <p className="text-xs text-zinc-500">{e.rationale}</p>}
          {e.files && e.files.length > 0 && e.files.length <= 6 && (
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

export function Empty({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-zinc-800 p-6 text-center text-sm text-zinc-500">{text}</div>;
}

const HEAL_AGENTS = ["fault_injector", "validator", "error_classifier", "reflector", "template_repair", "circuit_breaker"];

export function SelfHealing({ entries, done }: { entries: LedgerEntry[]; done: boolean }) {
  const heal = entries.filter((e) => HEAL_AGENTS.includes(e.agent.split(" ")[0]));
  const repaired = heal.some((e) => e.agent.startsWith("reflector") || e.agent === "template_repair");
  if (!heal.length) return <Empty text="When the sandbox finds a bug, the classifier → reflector → re-validate loop shows up here." />;
  return (
    <div className="space-y-4">
      {done && (
        <p className="text-sm text-zinc-400">
          {repaired
            ? "The agent found a bug in its own output, located the faulty file, patched it and re-validated, with no human involved."
            : "The generated code passed validation on the first try; no healing was needed."}
        </p>
      )}
      <Ledger entries={heal} />
    </div>
  );
}
