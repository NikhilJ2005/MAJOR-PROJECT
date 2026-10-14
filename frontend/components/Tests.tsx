import type { Validation } from "@/lib/api";
import { Empty } from "./Ledger";

const GATES = ["static", "import", "boot", "tests"] as const;
const GATE_LABEL: Record<string, string> = { static: "static checks", import: "imports", boot: "server boots", tests: "tests" };

const SUITES = [
  { path: "tests/test_smoke.py", name: "Contract tests", by: "derived from the design (never edited by AI)" },
  { path: "tests/test_rules.py", name: "Business-rule tests", by: "Test Engineer, written before the code" },
  { path: "tests/test_edge_cases.py", name: "Edge-case tests", by: "Bug Hunter, written to break the code" },
];

export function Tests({ validation, report, files }: { validation?: Validation | null; report?: string | null; files: string[] }) {
  if (!validation) return <Empty text="The generated app is checked, imported, started and tested in a sandbox. Results appear here." />;
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
            ? `The generated application works: it passes the static checks, starts up and passes every test${summary ? ` (${summary})` : ""}.`
            : `Failed at "${validation.gate}"${validation.failing_file ? ` in ${validation.failing_file}` : ""}.`}
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {SUITES.map((suite) => {
          const present = files.includes(suite.path);
          return (
            <div key={suite.path} className={`rounded-lg border px-3 py-2 ${present ? "border-zinc-700" : "border-dashed border-zinc-800 opacity-50"}`}>
              <div className="text-sm font-semibold text-zinc-100">{suite.name}</div>
              <div className="font-mono text-[11px] text-zinc-500">{suite.path}</div>
              <div className="text-xs text-zinc-400">{present ? suite.by : "not used in this run"}</div>
            </div>
          );
        })}
      </div>
      <pre className="max-h-[440px] overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-400">
        {report ?? validation.log}
      </pre>
    </div>
  );
}
