import type { Validation } from "@/lib/api";
import { Empty } from "./Ledger";

const GATES = ["import", "boot", "tests"] as const;
const GATE_LABEL: Record<string, string> = { import: "imports", boot: "server boots", tests: "API tests" };

export function Tests({ validation, report }: { validation?: Validation | null; report?: string | null }) {
  if (!validation) return <Empty text="The generated app is imported, started and tested in a sandbox. Results appear here." />;
  const failedAt = validation.ok ? -1 : GATES.indexOf(validation.gate as (typeof GATES)[number]);
  const summary = validation.ok ? validation.log.match(/\d+ passed[^=\n]*/)?.[0]?.trim() : null;
  return (
    <div className="space-y-3">
      <div className={`rounded-xl border p-4 ${validation.ok ? "border-emerald-500/40 bg-emerald-500/5" : "border-rose-500/40 bg-rose-500/5"}`}>
        <div className="flex flex-wrap items-center gap-3">
          {GATES.map((g, i) => {
            const ok = failedAt === -1 || i < failedAt;
            const bad = i === failedAt;
            return (
              <span key={g} className={`flex items-center gap-1.5 text-sm ${ok ? "text-emerald-300" : bad ? "text-rose-300" : "text-zinc-600"}`}>
                <span className="font-mono">{ok ? "✓" : bad ? "✗" : "·"}</span>
                {GATE_LABEL[g]}
              </span>
            );
          })}
          {validation.duration_s != null && <span className="ml-auto font-mono text-xs text-zinc-500">{validation.duration_s.toFixed(1)}s</span>}
        </div>
        <p className={`mt-2 text-sm ${validation.ok ? "text-zinc-200" : "text-zinc-300"}`}>
          {validation.ok
            ? `The generated application works: it starts up and passes every API test${summary ? ` (${summary})` : ""}.`
            : `Failed at "${validation.gate}"${validation.failing_file ? ` in ${validation.failing_file}` : ""}.`}
        </p>
      </div>
      <pre className="max-h-[440px] overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-400">
        {report ?? validation.log}
      </pre>
    </div>
  );
}
