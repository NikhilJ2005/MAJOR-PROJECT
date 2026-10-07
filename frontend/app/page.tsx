"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Files } from "@/components/Files";
import { Architecture } from "@/components/Architecture";
import { Empty, SelfHealing } from "@/components/Ledger";
import { NODE_META, Pipeline, type NodeState } from "@/components/Pipeline";
import { Tests } from "@/components/Tests";
import { api, type RunEvent, type ServerConfig, type Snapshot } from "@/lib/api";

const EXAMPLES = [
  "A blog API with posts and comments. Users sign up and log in; only logged-in users can write. Posts are searchable by title.",
  "Library system tracking books, members and loans with due dates and a returned flag.",
  "Clinic appointment booking with doctors, patients and appointments. Staff must log in to change data.",
];

const TABS = ["architecture", "code", "self-healing", "tests"] as const;
type Tab = (typeof TABS)[number];

const NEXT: Record<string, string> = {
  parse_spec: "plan",
  plan: "generate",
  generate: "validate",
};

function reduceStates(events: RunEvent[]): { states: Record<string, NodeState>; iteration: number } {
  const states: Record<string, NodeState> = {};
  let iteration = 0;
  let started = false;
  for (const ev of events) {
    if (ev.type === "status" && ev.status === "running" && !started) {
      started = true;
      if (!Object.keys(states).length) states.parse_spec = "active";
    }
    if (ev.type === "error") {
      for (const k of Object.keys(states)) if (states[k] === "active") states[k] = "failed";
    }
    if (ev.type !== "node" || !ev.node) continue;
    const node = ev.node;
    if (typeof ev.iteration === "number") iteration = ev.iteration;
    if (node === "validate") {
      const ok = ev.validation?.ok;
      states.validate = ok ? "done" : "failed";
      if (ok) {
        states.package = "active";
      } else {
        states.classify = "active";
        states.reflect = states.reflect === "done" ? "done" : "idle";
      }
      continue;
    }
    if (node === "classify") states.reflect = "active";
    if (node === "reflect") states.validate = "active";
    states[node] = "done";
    const next = NEXT[node];
    if (next) states[next] = "active";
    if (node === "failure_report") {
      states.classify = states.classify === "active" ? "idle" : states.classify;
      states.failure_report = "failed";
    }
  }
  return { states, iteration };
}

export default function Home() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [prompt, setPrompt] = useState(EXAMPLES[0]);
  const [fault, setFault] = useState(true);
  const [mode, setMode] = useState<"llm" | "template">("llm");
  const [accessCode, setAccessCode] = useState(() => {
    try {
      return typeof window === "undefined" ? "" : (localStorage.getItem("vibestack-access") ?? "");
    } catch {
      return "";
    }
  });
  const [runId, setRunId] = useState<string | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [tab, setTab] = useState<Tab>("architecture");
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [endedAt, setEndedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const lastSeq = useRef(-1);
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    api.config().then(setConfig, () => setError("Backend not reachable. Is the API running?"));
    const id = new URLSearchParams(window.location.search).get("run");
    if (id) attach(id, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = useCallback(async (id: string) => {
    try {
      setSnap(await api.snapshot(id));
    } catch {}
  }, []);

  const connect = useCallback(
    (id: string) => {
      source.current?.close();
      const es = new EventSource(api.eventsUrl(id, lastSeq.current));
      source.current = es;
      const onEvent = (msg: MessageEvent) => {
        const ev = JSON.parse(msg.data) as RunEvent;
        if (ev.seq <= lastSeq.current) return;
        lastSeq.current = ev.seq;
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "node" || ev.type === "error") refresh(id);
        if (ev.type === "node" && ev.node === "classify") setTab("self-healing");
        if (ev.type === "status" && ev.status && ev.status !== "running") {
          es.close();
          setEndedAt(Date.now());
          refresh(id);
          if (ev.status === "succeeded" || ev.status === "failed") setTab("tests");
        }
      };
      for (const t of ["node", "status", "error"]) es.addEventListener(t, onEvent as EventListener);
      es.onerror = () => {
        // The server closes the stream when the run goes idle; EventSource would retry forever.
        es.close();
        refresh(id);
      };
    },
    [refresh],
  );

  async function attach(id: string, fromUrl = false) {
    setRunId(id);
    setEvents([]);
    lastSeq.current = -1;
    await refresh(id);
    connect(id);
    if (fromUrl) setStartedAt(null);
  }

  async function start() {
    setError(null);
    try {
      localStorage.setItem("vibestack-access", accessCode);
    } catch {}
    try {
      const { run_id } = await api.start({ prompt, inject_fault: fault, codegen_mode: mode }, accessCode);
      window.history.replaceState(null, "", `?run=${run_id}`);
      setSnap(null);
      setTab("architecture");
      setStartedAt(Date.now());
      setEndedAt(null);
      await attach(run_id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const status = snap?.status ?? (runId ? "running" : "idle");
  const running = status === "running";

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [running]);

  const { states, iteration } = reduceStates(events);
  const lastNodeEvent = [...events].reverse().find((e) => e.type === "node");
  const errorEvent = events.find((e) => e.type === "error");
  const elapsed = startedAt ? ((endedAt ?? now) - startedAt) / 1000 : null;

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-sky-400 to-violet-500 font-mono text-sm font-bold text-zinc-950">
            VS
          </div>
          <div>
            <h1 className="text-lg font-semibold leading-tight">VibeStack</h1>
            <p className="text-xs text-zinc-500">prompt → architecture → working, self-healed FastAPI app</p>
          </div>
        </div>
        {config && (
          <div className="ml-auto flex flex-wrap gap-1.5 font-mono text-[11px]">
            <Badge label="orchestrator" value="LangGraph" />
            <Badge label="strong" value={config.models.strong} />
            <Badge label="cheap" value={config.models.cheap} />
            <Badge label="sandbox" value={config.sandbox} />
            {!config.llm_enabled && <Badge label="llm" value="not configured" warn />}
          </div>
        )}
      </header>

      <div className="grid gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
        <section className="space-y-5">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <label className="mb-2 block text-xs uppercase tracking-wider text-zinc-500">Describe your application</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={5}
              className="w-full resize-none rounded-lg border border-zinc-700 bg-zinc-950 p-3 text-sm text-zinc-100 outline-none focus:border-sky-500"
            />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {EXAMPLES.map((ex, i) => (
                <button key={i} onClick={() => setPrompt(ex)} className="rounded-full border border-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400 hover:border-zinc-600 hover:text-zinc-200">
                  {["blog + auth", "library", "clinic"][i]}
                </button>
              ))}
            </div>
            <div className="mt-4 space-y-2 text-sm">
              <label className="flex cursor-pointer items-center gap-2 text-zinc-300">
                <input type="checkbox" checked={fault} onChange={(e) => setFault(e.target.checked)} className="accent-amber-400" />
                Inject a bug (demo self-healing)
              </label>
              <div className="flex items-center gap-2 text-zinc-300">
                <span className="text-zinc-500">Codegen</span>
                {(["llm", "template"] as const).map((m) => (
                  <button key={m} onClick={() => setMode(m)} className={`rounded px-2 py-0.5 font-mono text-xs ${mode === m ? "bg-sky-500/20 text-sky-200" : "text-zinc-500 hover:text-zinc-300"}`}>
                    {m}
                  </button>
                ))}
              </div>
              {config?.access_required && (
                <input
                  type="password"
                  placeholder="Access code"
                  value={accessCode}
                  onChange={(e) => setAccessCode(e.target.value)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm outline-none focus:border-sky-500"
                />
              )}
            </div>
            <button
              onClick={start}
              disabled={running || prompt.trim().length < 10}
              className="mt-4 w-full rounded-lg bg-sky-500 py-2 text-sm font-semibold text-zinc-950 transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running ? "Agents working…" : "Generate application"}
            </button>
            {error && <p className="mt-2 text-xs text-rose-400">{error}</p>}
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-xs uppercase tracking-wider text-zinc-500">LangGraph run</h2>
              <StatusPill status={status} />
            </div>
            <Pipeline states={states} iteration={iteration} maxIterations={config?.max_heal_iterations ?? 3} />
            {lastNodeEvent?.node && running && (
              <p className="mt-3 font-mono text-[11px] text-zinc-500">last: {NODE_META[lastNodeEvent.node]?.label ?? lastNodeEvent.node}</p>
            )}
          </div>
        </section>

        <section className="min-w-0 space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="heal iterations" value={snap?.iteration ?? 0} />
            <Stat label="tokens" value={(snap?.usage?.total_tokens ?? 0).toLocaleString()} />
            <Stat label="est. cost" value={`$${(snap?.usage?.cost_usd ?? 0).toFixed(4)}`} />
            <Stat label="elapsed" value={elapsed != null ? `${elapsed.toFixed(1)}s` : "-"} />
          </div>

          {errorEvent && <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 font-mono text-xs text-rose-300">{errorEvent.message}</div>}

          <div className="rounded-xl border border-zinc-800 bg-zinc-900/40">
            <div className="flex items-center gap-1 overflow-x-auto border-b border-zinc-800 px-2">
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`-mb-px border-b-2 px-3 py-2.5 text-sm capitalize ${tab === t ? "border-sky-400 text-zinc-100" : "border-transparent text-zinc-500 hover:text-zinc-300"}`}
                >
                  {t.replace("-", " ")}
                  {t === "code" && snap?.files?.length ? <span className="ml-1 text-xs text-zinc-500">{snap.files.length}</span> : null}
                  {t === "self-healing" && (snap?.iteration ?? 0) > 0 ? <span className="ml-1 text-xs text-amber-400">{snap?.iteration}</span> : null}
                </button>
              ))}
              {snap?.has_artifact && runId && (
                <a href={api.downloadUrl(runId)} className="my-1.5 ml-auto shrink-0 rounded-lg bg-emerald-500 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-emerald-400">
                  Download .zip
                </a>
              )}
            </div>
            <div className="p-4">
              {tab === "architecture" &&
                (snap?.spec ? (
                  <Architecture spec={snap.spec} plan={snap.file_plan} />
                ) : (
                  <Empty text={running ? "The Architect is designing the data model and API…" : "Describe an application and press Generate."} />
                ))}
              {tab === "code" && runId && <Files runId={runId} files={snap?.files ?? []} version={snap?.ledger?.length ?? 0} />}
              {tab === "code" && !runId && <Empty text="Generated source files appear here." />}
              {tab === "self-healing" && <SelfHealing entries={snap?.ledger ?? []} done={!running && !!snap?.validation} />}
              {tab === "tests" && <Tests validation={snap?.validation} report={snap?.report} />}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}

function Badge({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <span className={`rounded-md border px-2 py-0.5 ${warn ? "border-amber-500/40 text-amber-300" : "border-zinc-800 text-zinc-400"}`}>
      <span className="text-zinc-600">{label}:</span> {value}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className="mt-0.5 font-mono text-lg text-zinc-100">{value}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    idle: "bg-zinc-800 text-zinc-400",
    running: "bg-sky-500/15 text-sky-300",
    succeeded: "bg-emerald-500/15 text-emerald-300",
    failed: "bg-rose-500/15 text-rose-300",
    error: "bg-rose-500/15 text-rose-300",
  };
  return <span className={`rounded-full px-2 py-0.5 text-[11px] ${map[status] ?? map.running}`}>{status.replace("_", " ")}</span>;
}
