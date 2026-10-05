import type { Finding, Validation } from "@/lib/api";
import { Empty } from "./Ledger";

const sev: Record<string, string> = {
  high: "bg-rose-500/15 text-rose-300",
  medium: "bg-amber-500/15 text-amber-300",
  low: "bg-sky-500/15 text-sky-300",
  info: "bg-zinc-700/50 text-zinc-300",
};

export function Review({ findings }: { findings: Finding[] }) {
  if (!findings.length) return <Empty text="Review council findings (static rules + LLM reviewer) appear here." />;
  return (
    <ul className="space-y-2">
      {findings.map((f, i) => (
        <li key={i} className="flex flex-wrap items-baseline gap-2 rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2">
          <span className={`rounded px-1.5 py-0.5 text-[11px] uppercase ${sev[f.severity] ?? sev.info}`}>{f.severity}</span>
          <span className="text-sm text-zinc-200">{f.issue}</span>
          <span className="font-mono text-[11px] text-zinc-500">{f.file}</span>
          {f.source && <span className="ml-auto text-[10px] uppercase text-zinc-600">{f.source}</span>}
        </li>
      ))}
    </ul>
  );
}

export function ValidationLog({ validation, report }: { validation?: Validation | null; report?: string | null }) {
  if (!validation) return <Empty text="Sandbox output (import, boot and smoke-test gates) appears here." />;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`rounded px-2 py-0.5 text-xs ${validation.ok ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300"}`}>
          {validation.ok ? "all gates passed" : `failed: ${validation.gate}`}
        </span>
        {validation.failing_file && <span className="font-mono text-xs text-zinc-400">suspect: {validation.failing_file}</span>}
        {validation.duration_s != null && <span className="ml-auto font-mono text-xs text-zinc-500">{validation.duration_s.toFixed(1)}s</span>}
      </div>
      <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-400">
        {report ?? validation.log}
      </pre>
    </div>
  );
}
