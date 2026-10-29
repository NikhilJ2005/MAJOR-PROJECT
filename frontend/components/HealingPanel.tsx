"use client";

import { useEffect, useState } from "react";
import type { LedgerEntry, RunEvent, Snapshot, ToolCall } from "@/lib/api";
import { explainStop } from "@/components/AgentSteps";

type Attempt = {
  n: number;
  rung: string; // quick model, strong model, verified templates
  files: string[];
  diagnosis?: string;
  tools: ToolCall[];
  rolledBack: boolean;
  failingAfter?: number | null; // tests failing when this attempt was re-validated
  gateAfter?: string;
  passed?: boolean;
};

const WHAT: Record<string, string> = {
  syntax: "A typo stops the business logic from loading",
  import: "A file asks for something that does not exist",
  dependency: "The code uses a package the app does not have",
  orm: "The database model and the code disagree",
  validation: "The app accepts or refuses the wrong input",
  runtime: "Some requests crash the business logic",
  contract: "Some endpoints answer with the wrong result",
  frontend: "A screen asks for something the backend does not have",
  timeout: "Something is too slow or never finishes",
  static: "The safety checks found risky code",
  sandbox: "The test machine was overloaded",
};

const short = (path: string) => path.split("/").pop() ?? path;

function rungOf(e: LedgerEntry): string {
  if (e.agent.startsWith("template_repair")) return "verified templates";
  if (e.agent.startsWith("judge")) return "judge (strong model)";
  if (e.agent.includes("strong")) return "strong model";
  if (e.agent.includes("cheap")) return "quick model";
  return "reflector";
}

/** A tool call said like a person would say it. */
function toolText(t: ToolCall): string {
  const first = String(Object.values(t.args ?? {})[0] ?? "");
  switch (t.tool) {
    case "read_file":
      return `read ${short(first)}`;
    case "search_code":
      return `searched for “${first.slice(0, 30)}”`;
    case "list_files":
      return "looked at the files";
    case "list_routes":
      return "checked the routes";
    case "write_file":
      return `staged a fix to ${short(first)}`;
    case "run_tests":
      return "re-ran the failing tests";
    case "check_frontend":
      return "checked the screens";
    default:
      return t.tool.replace(/_/g, " ");
  }
}

function failingCount(log?: string): number | null {
  if (!log) return null;
  const m = log.match(/(\d+) failed/);
  if (m) return Number(m[1]);
  return /(\d+) passed/.test(log) ? 0 : null;
}

/** Repair attempts and their effect, from live events (running) or the ledger (finished). */
function attemptsFrom(events: RunEvent[], ledger: LedgerEntry[]): { attempts: Attempt[]; firstFailing: number | null } {
  const attempts: Attempt[] = [];
  let firstFailing: number | null = null;
  const onRepair = (e: LedgerEntry) => {
    const n = e.iteration ?? attempts.length + 1;
    attempts.push({
      n,
      rung: rungOf(e),
      files: e.files ?? [],
      diagnosis: e.rationale,
      tools: e.tools ?? [],
      rolledBack: (e.rationale ?? "").includes("rolled back"),
    });
  };
  if (events.length) {
    for (const ev of events) {
      if (ev.type !== "node") continue;
      if (ev.node === "reflect") ev.ledger?.forEach(onRepair);
      if (ev.node === "validate" && ev.validation) {
        const count = failingCount(ev.validation.log);
        const last = attempts.at(-1);
        if (!last) firstFailing = ev.validation.ok ? 0 : count;
        else if (last.gateAfter === undefined) {
          last.failingAfter = ev.validation.ok ? 0 : count;
          last.gateAfter = ev.validation.gate;
          last.passed = ev.validation.ok;
        }
      }
    }
  } else {
    for (const e of ledger) {
      const agent = e.agent.split(" ")[0];
      if (agent === "reflector" || agent === "template_repair" || agent === "judge") onRepair(e);
      if (agent === "validator") {
        const last = attempts.at(-1);
        const ok = e.action.startsWith("all gates passed");
        const gate = e.action.match(/gate '(\w+)'/)?.[1];
        if (last && last.gateAfter === undefined) {
          last.gateAfter = ok ? "passed" : gate;
          last.passed = ok;
        }
      }
    }
  }
  return { attempts, firstFailing };
}

function Trend({ before, after }: { before: number | null | undefined; after: number | null | undefined }) {
  if (after == null) return null;
  if (after === 0) return <span className="text-emerald-300">all tests pass</span>;
  const arrow = before == null || after === before ? "→" : after < before ? "↓" : "↑";
  const color = arrow === "↓" ? "text-emerald-300" : arrow === "↑" ? "text-rose-300" : "text-ink-300";
  return <span className={color}>{arrow} {after} failing</span>;
}

/** Self-healing, explained: what broke, what each attempt tried, and whether it helped. */
export function HealingPanel({
  events, snap, running, stopping, compact,
}: {
  events: RunEvent[];
  snap: Snapshot | null;
  running: boolean;
  stopping?: boolean;
  compact?: boolean;
}) {
  const { attempts, firstFailing } = attemptsFrom(events, snap?.ledger ?? []);
  const failedOnce = attempts.length > 0 || (snap?.validation && !snap.validation.ok) ||
    events.some((e) => e.type === "node" && e.node === "validate" && e.validation && !e.validation.ok);
  const [open, setOpen] = useState(!compact);
  const lastProgress = [...events].reverse().find((e) => e.type === "progress")?.text;
  const lastNode = [...events].reverse().find((e) => e.type === "node")?.node;
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [running]);
  if (!failedOnce) return null;

  const status = snap?.status ?? "running";
  const fixed = status === "succeeded";
  const waiting = lastProgress?.match(/Still waiting for (.+?) \((\d+)s of (\d+)s\)/);
  const lastEventTs = [...events].reverse().find((e) => e.type === "node")?.ts;
  const working = lastEventTs ? Math.max(0, Math.round(now - lastEventTs)) : null;
  let chip = "Gave up";
  let chipColor = "bg-rose-400/15 text-rose-200";
  if (fixed) {
    chip = "Fixed";
    chipColor = "bg-emerald-400/15 text-emerald-200";
  } else if (stopping) {
    chip = "Stopping…";
    chipColor = "bg-ink-700 text-ink-200";
  } else if (running) {
    chipColor = "bg-signal-400/15 text-signal-200";
    chip = waiting ? `Waiting on ${waiting[1].replace(/^the /, "")} · ${waiting[2]}s / ${waiting[3]}s`
      : lastNode === "reflect" ? `Re-testing the fix${working != null ? ` · ${working}s` : ""}`
      : `Fixing${working != null ? ` · ${working}s` : ""}`;
  }
  const errorClass = snap?.error_class ?? [...events].reverse().find((e) => e.type === "node" && e.error_class)?.error_class;
  const problem = (errorClass && WHAT[errorClass]) || "Some tests fail";
  const failing = snap?.validation && !snap.validation.ok ? failingCount(snap.validation.log) : null;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/70 p-4" aria-label="Self-healing">
      <header className="flex flex-wrap items-center gap-3">
        <span className="font-display text-base text-ink-50">Self-healing</span>
        <span className="text-sm text-ink-400">{attempts.length ? `attempt ${attempts.length}` : "starting"}</span>
        <span className={`rounded-full px-2.5 py-0.5 text-xs ${chipColor}`}>{chip}</span>
        <button onClick={() => setOpen(!open)} className="ml-auto text-xs text-ink-400 hover:text-ink-100">
          {open ? "Hide steps" : `Show ${attempts.length} step${attempts.length === 1 ? "" : "s"}`}
        </button>
      </header>
      <p className="mt-2 text-sm text-ink-200">
        {fixed ? "Every test passes now." : `${problem}${failing ? ` (${failing} test${failing === 1 ? "" : "s"} failing)` : ""}.`}
        {running && snap?.has_draft && !fixed && " The screens already work in Preview; the agents are fixing the business rules."}
        {!running && !fixed && explainStop(snap?.stop_reason, errorClass) && ` ${explainStop(snap?.stop_reason, errorClass)}`}
      </p>
      {running && !stopping && (
        <p className="mt-1 text-xs text-ink-500">Ladder: quick model → strong model → verified templates. It stops by itself when a step stops helping.</p>
      )}
      {open && attempts.length > 0 && (
        <ol className="mt-3 space-y-2">
          {attempts.map((a, i) => (
            <li key={`${a.n}-${i}`} className="rounded-lg border border-ink-800 bg-ink-950/60 px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="font-medium text-ink-100">Attempt {a.n}</span>
                <span className="rounded bg-ink-800 px-1.5 py-0.5 text-[11px] text-ink-300">{a.rung}</span>
                <span className="text-xs text-ink-400">
                  {a.files.length ? `fixed ${a.files.map(short).join(", ")}` : "found nothing to change"}
                </span>
                <span className="ml-auto text-xs">
                  {a.rolledBack && <span className="mr-2 text-ink-400">previous fix undone (made it worse)</span>}
                  {a.gateAfter === undefined ? (
                    running ? <span className="text-signal-300">re-testing…</span> : null
                  ) : (
                    <Trend before={i === 0 ? firstFailing : attempts[i - 1].failingAfter} after={a.failingAfter ?? (a.passed ? 0 : null)} />
                  )}
                </span>
              </div>
              {a.tools.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {a.tools.map((t, j) => (
                    <span key={j} className="rounded-full bg-ink-800 px-2 py-0.5 text-[11px] text-ink-300" title={t.result}>{toolText(t)}</span>
                  ))}
                </div>
              )}
              {a.diagnosis && !compact && <p className="mt-1 line-clamp-2 text-xs text-ink-500">{a.diagnosis}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
